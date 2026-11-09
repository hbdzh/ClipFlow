#!/usr/bin/env python3
"""Independent success matrix for the AI normalized input contract.

Fixtures are derived from an explicitly supplied licensed local source. This
does not change the older development-format test or its expected rejections.
"""
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


def ff(*args):
    return subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', *map(str, args)],
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=120, check=True).stdout


def main():
    parser = argparse.ArgumentParser()
    for name in ('library', 'python', 'worker', 'model', 'source', 'temp-parent'):
        parser.add_argument('--' + name, type=lambda x: Path(x).absolute(), required=True)
    args = parser.parse_args()
    api = API(args.library)
    root = Path(tempfile.mkdtemp(prefix='ai001-normalized-matrix-', dir=args.temp_parent)).resolve()
    os.chmod(root, 0o700)
    cfg = configuration()
    cfg['media_backend']['ai_backend'] = {'python': str(args.python), 'worker': str(args.worker),
        'model': str(args.model), 'timeout_ms': 600000, 'development_root': str(root)}
    rows, clean, engine = [], False, None
    try:
        engine = api.create(cfg)
        for codec, ext in [('pcm_s16le', 'wav'), ('pcm_s24le', 'wav'), ('pcm_f32le', 'wav'),
                           ('flac', 'flac'), ('libmp3lame', 'mp3'), ('aac', 'm4a')]:
            for rate in (44100, 48000):
                for channels in (1, 2):
                    case = f'{codec}-{rate}-{channels}'
                    source, destination = root / (case + '.' + ext), root / (case + '-output')
                    row = {'case': case, 'result': 'FAIL'}
                    try:
                        # 0.5 gain gives lossy codecs conservative headroom and
                        # isolates format mapping from source clipping behavior.
                        ff('-i', args.source, '-t', '3', '-af', 'volume=0.5', '-ar', rate,
                           '-ac', channels, '-c:a', codec, '-y', source)
                        row['source_sha256'] = source_hash = sha(source)
                        raw = ff('-i', source, '-map', '0:a:0', '-c:a', 'pcm_f32le', '-f', 'f32le', 'pipe:1')
                        native = np.frombuffer(raw, dtype='<f4')
                        if len(native) % channels or not np.isfinite(native).all():
                            raise AssertionError('native decode invalid')
                        decoded_frames = len(native) // channels
                        expected = (decoded_frames * 44100 * 2 + rate) // (rate * 2)
                        # Explicit unity-copy: FFmpeg's default mono upmix may
                        # attenuate by 3 dB and is not the contract's mapping.
                        filtergraph = ('pan=stereo|c0=c0|c1=c0,aresample=44100'
                                       if channels == 1 else 'aresample=44100')
                        normalized = np.frombuffer(ff('-i', source, '-map', '0:a:0', '-af', filtergraph,
                            '-ar', '44100', '-ac', '2', '-c:a', 'pcm_f32le', '-f', 'f32le', 'pipe:1'),
                            dtype='<f4').reshape(-1, 2)
                        if abs(len(normalized) - expected) > 1:
                            raise AssertionError('independent normalizer differs by more than one frame')
                        if len(normalized) < expected:
                            normalized = np.pad(normalized, ((0, expected - len(normalized)), (0, 0)))
                        normalized = normalized[:expected]
                        request = {'schema_version': 0, 'operation': 'ai_separate',
                            'source': {'uri': source.as_uri()}, 'destination': {'uri': destination.as_uri()},
                            'model_id': 'umx_hq_vocals_onnx_v1'}
                        rc, response = engine.submit(request)
                        if rc or not response or not response.get('job_id'):
                            raise AssertionError(('preflight rejection', rc, response))
                        final = engine.wait(response['job_id'], timeout=180)
                        if final['state'] != 'succeeded':
                            raise AssertionError(('job not successful', final))
                        outputs = {name: check_audio(destination / f'{name}.wav', shutil.which('ffprobe'),
                                                      shutil.which('ffmpeg'))
                                   for name in ('vocals', 'accompaniment')}
                        if any(x['frames'] != expected for x in outputs.values()):
                            raise AssertionError(('output frames', expected, outputs))
                        voice, vr = sf.read(destination / 'vocals.wav', dtype='float32', always_2d=True)
                        accomp, ar = sf.read(destination / 'accompaniment.wav', dtype='float32', always_2d=True)
                        combined = voice.astype('float64') + accomp.astype('float64')
                        err = np.abs(combined - normalized.astype('float64'))
                        # Relative to independently decoded/resampled input at
                        # the declared sample origin, including both endpoints.
                        if vr != 44100 or ar != 44100 or err.shape != (expected, 2) or float(err.max()) > 3e-6:
                            raise AssertionError(('mapping or endpoint mismatch', float(err.max())))
                        if channels == 1 and float(np.max(np.abs(combined[:, 0] - combined[:, 1]))) > 3e-6:
                            raise AssertionError('mono was not duplicated')
                        if sha(source) != source_hash:
                            raise AssertionError('source changed')
                        row.update(result='PASS', native_frames=decoded_frames, expected_frames=expected,
                                   max_mapping_error=float(err.max()), first_error=float(err[0].max()),
                                   last_error=float(err[-1].max()), outputs=outputs)
                    except Exception as exc:
                        row['error'] = repr(exc)
                    rows.append(row)
        if engine.destroy() != 0:
            raise RuntimeError('engine still busy')
        clean = True
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
              'model_sha256': sha(args.model), 'source_sha256': sha(args.source),
              'cases': rows, 'ffi_outputs': api.outputs, 'ffi_frees': api.frees,
              'result': 'PASS' if clean and len(rows) == 24 and
                       all(row['result'] == 'PASS' for row in rows) and api.outputs == api.frees else 'FAIL'}
    print(json.dumps(result, indent=2, allow_nan=False))
    if result['result'] != 'PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
