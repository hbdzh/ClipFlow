#!/usr/bin/env python3
"""Independent actual Core audio quality check using local licensed fixtures.

Requires three existing case-N/{mix,voice,piano}.wav files, fixed ONNX model,
upstream weights, runtime, and a freshly built C ABI library. Copies inputs to
the private development root; never overwrites caller-owned audio. Audio and
models stay on the local machine. Metrics and hashes are printed as JSON.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import numpy as np
import soundfile as sf

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'tests' / 'core_004'))
from support import API, configuration
from verify_audio import check_audio, sha


def si_sdr(reference, estimated):
    r = reference.astype(np.float64).reshape(-1)
    x = estimated.astype(np.float64).reshape(-1)
    energy = np.dot(r, r)
    if energy <= 1e-12:
        raise ValueError('silent SI-SDR reference')
    target = np.dot(x, r) / energy * r
    noise = x - target
    return float(10 * np.log10(max(float(np.dot(target, target)), 1e-12) /
                               max(float(np.dot(noise, noise)), 1e-12)))


def audio(path):
    signal, rate = sf.read(path, dtype='float32', always_2d=True)
    if rate != 44100 or signal.shape[1] != 2 or not np.isfinite(signal).all():
        raise ValueError('invalid decoded input')
    return signal


def main():
    parser = argparse.ArgumentParser()
    for name in ('library', 'python', 'worker', 'model', 'torch-weights', 'fixture', 'temp-parent'):
        parser.add_argument('--' + name, type=lambda x: Path(x).absolute(), required=True)
    parser.add_argument('--timeout-ms', type=int, default=600000)
    args = parser.parse_args()
    ffprobe, ffmpeg = shutil.which('ffprobe'), shutil.which('ffmpeg')
    if not ffprobe or not ffmpeg:
        raise SystemExit('ffmpeg/ffprobe unavailable')
    result = {'schema': 1, 'kind': 'core_quality_three_licensed_mixes', 'result': 'FAIL',
              'library_sha256': sha(args.library), 'worker_sha256': sha(args.worker),
              'model_sha256': sha(args.model), 'torch_weights_sha256': sha(args.torch_weights),
              'cases': [], 'caveats': ['three ~3 second artificial mixes, not complete songs',
                    'no human listener assessment or claim of general model quality',
                    'SI-SDR is not a substitute for listening']}
    api = API(args.library)
    root = Path(tempfile.mkdtemp(prefix='mediastudio-ai001-quality-', dir=args.temp_parent)).resolve()
    confirmed_clean = False
    try:
        os.chmod(root, 0o700)
        config = configuration()
        config['media_backend']['ai_backend'] = {
            'python': str(args.python), 'worker': str(args.worker), 'model': str(args.model),
            'timeout_ms': args.timeout_ms, 'development_root': str(root)}
        engine = api.create(config)
        try:
            for case in (1, 3, 5):
                folder = args.fixture / f'case-{case}'
                mix, voice, piano = (folder / f'{name}.wav' for name in ('mix', 'voice', 'piano'))
                source = root / f'case-{case}.wav'
                destination = root / f'result-{case}'
                source_sha = sha(mix)
                shutil.copyfile(mix, source)
                if sha(source) != source_sha:
                    raise AssertionError('source copy differs')
                request = {'schema_version': 0, 'operation': 'ai_separate',
                    'source': {'uri': source.as_uri()}, 'destination': {'uri': destination.as_uri()},
                    'model_id': 'umx_hq_vocals_onnx_v1'}
                start = time.monotonic()
                rc, response = engine.submit(request)
                if rc != 0 or not response or not response.get('job_id'):
                    raise AssertionError(('submit rejected', rc, response))
                final = engine.wait(response['job_id'], timeout=args.timeout_ms / 1000 + 30)
                if final['state'] != 'succeeded':
                    raise AssertionError(('job did not succeed', final))
                files = {name: check_audio(destination / f'{name}.wav', ffprobe, ffmpeg)
                         for name in ('vocals', 'accompaniment')}
                samples = audio(source)
                outputs = {name: audio(destination / f'{name}.wav') for name in files}
                references = {'vocals': audio(voice), 'accompaniment': audio(piano)}
                if any(x.shape != samples.shape for x in (*outputs.values(), *references.values())):
                    raise AssertionError('frame or channel mismatch')
                scores = {}
                for name in outputs:
                    before = si_sdr(references[name], samples)
                    after = si_sdr(references[name], outputs[name])
                    scores[name] = {'input_db': before, 'output_db': after, 'improvement_db': after-before,
                                    'peak': float(np.max(np.abs(outputs[name])))}
                cmd = [str(args.python), str(ROOT / 'docs' / 'ai-001' / 'compare_audio_upstream.py'),
                       str(args.torch_weights), str(source), str(destination)]
                upstream = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                                          text=True, timeout=180, check=True)
                comparison = [json.loads(line) for line in upstream.stdout.splitlines() if line.strip()]
                if len(comparison) != 2 or any(x['relative_rms_error'] > 1e-4 for x in comparison):
                    raise AssertionError(('upstream parity mismatch', comparison))
                if sha(mix) != source_sha or sha(source) != source_sha:
                    raise AssertionError('source modified')
                result['cases'].append({'case': case, 'source_sha256': source_sha,
                    'voice_reference_sha256': sha(voice), 'piano_reference_sha256': sha(piano),
                    'frames': len(samples), 'elapsed_seconds': time.monotonic()-start,
                    'outputs': files, 'scores': scores, 'torch_reference': comparison})
            if engine.destroy() != 0:
                raise AssertionError('engine destroy failed')
            confirmed_clean = True
            if api.outputs != api.frees:
                raise AssertionError('FFI outputs/frees mismatch')
            for name in ('vocals', 'accompaniment'):
                improvements = [case['scores'][name]['improvement_db'] for case in result['cases']]
                if float(np.median(improvements)) < 0:
                    raise AssertionError((name, 'negative median SI-SDR improvement', improvements))
            result['ffi_outputs'] = api.outputs
            result['ffi_frees'] = api.frees
            result['result'] = 'PASS'
        finally:
            if not engine.closed:
                for job in engine.jobs:
                    try:
                        engine.cancel(job)
                        engine.wait(job, timeout=20)
                    except Exception:
                        pass
                confirmed_clean = engine.destroy() == 0
    finally:
        if confirmed_clean:
            shutil.rmtree(root)
        else:
            print(f'unconfirmed cleanup; retained private fixture: {root}', file=sys.stderr)
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
