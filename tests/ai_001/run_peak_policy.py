#!/usr/bin/env python3
"""Validate finite float headroom through Core and reject >2 without clamping."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests/core_004'))
from support import API, configuration
from verify_audio import check_audio, sha


def main():
    parser = argparse.ArgumentParser()
    for name in ('library', 'python', 'worker', 'model', 'torch-weights', 'source', 'temp-parent'):
        parser.add_argument('--' + name, type=lambda x: Path(x).absolute(), required=True)
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix='ai001-peak-policy-', dir=args.temp_parent)).resolve()
    os.chmod(root, 0o700)
    api, engine, clean = API(args.library), None, False
    rows = []
    try:
        samples, rate = sf.read(args.source, dtype='float32', always_2d=True)
        if rate != 44100 or samples.shape[1] != 2 or not np.isfinite(samples).all():
            raise ValueError('source must be finite 44.1k stereo')
        peak = float(np.max(np.abs(samples)))
        if peak < 1e-6:
            raise ValueError('source must not be silent')
        valid = samples * np.float32(1.4 / peak)
        too_loud = samples * np.float32(2.1 / peak)
        source = root / 'headroom.wav'
        invalid = root / 'over-limit.wav'
        sf.write(source, valid, rate, subtype='FLOAT')
        sf.write(invalid, too_loud, rate, subtype='FLOAT')
        converted_source = root / 'headroom-48k.wav'
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(source), '-ar', '48000',
                        '-c:a', 'pcm_f32le', '-y', str(converted_source)], check=True,
                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        cfg = configuration()
        cfg['media_backend']['ai_backend'] = {'python': str(args.python), 'worker': str(args.worker),
            'model': str(args.model), 'timeout_ms': 600000, 'development_root': str(root)}
        engine = api.create(cfg)
        for test_source in (source, converted_source, invalid):
            dest = root / (test_source.stem + '-output')
            before = sha(test_source)
            original, source_rate = sf.read(test_source, dtype='float32', always_2d=True)
            row = {'input_sha256': before, 'input_peak': float(np.max(np.abs(original))),
                   'sample_rate': source_rate, 'expected_reject': test_source == invalid}
            request = {'schema_version': 0, 'operation': 'ai_separate',
                       'source': {'uri': test_source.as_uri()}, 'destination': {'uri': dest.as_uri()},
                       'model_id': 'umx_hq_vocals_onnx_v1'}
            rc, response = engine.submit(request)
            if test_source == invalid:
                if response and response.get('job_id'):
                    row['terminal'] = engine.wait(response['job_id'], timeout=180)
                    rejected = row['terminal']['state'] == 'failed'
                else:
                    row['rejection'] = response
                    rejected = rc == 0 and response and response.get('accepted') is False
                if not rejected or dest.exists():
                    raise AssertionError(('>2 was not rejected safely', row))
            else:
                if rc or not response or not response.get('job_id'):
                    raise AssertionError(('valid headroom input rejected', rc, response))
                final = engine.wait(response['job_id'], timeout=180)
                if final['state'] != 'succeeded':
                    raise AssertionError(('headroom job failed', final))
                for name in ('vocals', 'accompaniment'):
                    check_audio(dest / f'{name}.wav', shutil.which('ffprobe'), shutil.which('ffmpeg'))
                reference = test_source
                if source_rate != 44100:
                    normalized = root / 'reference-normalized.wav'
                    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(test_source),
                                    '-ar', '44100', '-c:a', 'pcm_f32le', '-y', str(normalized)],
                                   check=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, timeout=120)
                    arr, _ = sf.read(normalized, dtype='float32', always_2d=True)
                    expected = (len(original) * 44100 * 2 + source_rate) // (source_rate * 2)
                    if abs(len(arr) - expected) > 1:
                        raise AssertionError('independent conversion >1 frame discrepancy')
                    if len(arr) < expected:
                        arr = np.pad(arr, ((0, expected-len(arr)), (0, 0)))
                    sf.write(normalized, arr[:expected], 44100, subtype='FLOAT')
                    reference = normalized
                outcome = subprocess.run([str(args.python), str(ROOT / 'docs/ai-001/compare_audio_upstream.py'),
                                          str(args.torch_weights), str(reference), str(dest)],
                                         check=True, capture_output=True, text=True, timeout=180)
                comparisons = [json.loads(x) for x in outcome.stdout.splitlines() if x.strip()]
                if len(comparisons) != 2 or any(x['relative_rms_error'] > 1e-4 for x in comparisons):
                    raise AssertionError(('upstream mismatch', comparisons))
                row['torch_reference'] = comparisons
            if sha(test_source) != before or list(root.glob('.mediastudio-ai-*')):
                raise AssertionError('source changed or stage remains')
            row['result'] = 'PASS'
            rows.append(row)
        clean = engine.destroy() == 0
    finally:
        if engine and not engine.closed:
            for job in engine.jobs:
                try:
                    engine.cancel(job)
                    engine.wait(job, timeout=20)
                except Exception:
                    pass
            clean = engine.destroy() == 0
        clean = clean and not list(root.glob('.mediastudio-ai-*'))
        if clean:
            shutil.rmtree(root)
        else:
            print(f'unconfirmed cleanup; retained fixture {root}', file=sys.stderr)
    result = {'library_sha256': sha(args.library), 'worker_sha256': sha(args.worker),
              'model_sha256': sha(args.model), 'cases': rows, 'ffi_outputs': api.outputs,
              'ffi_frees': api.frees, 'result': 'PASS' if clean and len(rows) == 3 and
              all(x['result'] == 'PASS' for x in rows) and api.outputs == api.frees else 'FAIL'}
    print(json.dumps(result, indent=2, allow_nan=False))
    if result['result'] != 'PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
