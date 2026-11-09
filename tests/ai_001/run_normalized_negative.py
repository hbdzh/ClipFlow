#!/usr/bin/env python3
"""Independent real-ABI negative checks for normalized audio input."""
import argparse
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests/core_004'))
from support import API, configuration
from verify_audio import sha
import soundfile as sf


def run(*args):
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', *map(str, args)],
                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                   stderr=subprocess.PIPE, timeout=120, check=True)


def main():
    parser = argparse.ArgumentParser()
    for name in ('library', 'python', 'worker', 'model', 'source', 'temp-parent'):
        parser.add_argument('--' + name, type=lambda x: Path(x).absolute(), required=True)
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix='ai001-normalized-negative-', dir=args.temp_parent)).resolve()
    os.chmod(root, 0o700)
    engine = None
    clean = False
    api = API(args.library)
    rows = []
    try:
        bad = root / 'bad.wav'
        bad.write_bytes(b'not a media file')
        full = root / 'fixture.wav'
        run('-i', args.source, '-t', '2', '-c:a', 'pcm_s16le', '-y', full)
        truncated = root / 'truncated.wav'
        truncated.write_bytes(full.read_bytes()[:100])
        multi = root / 'two-streams.m4a'
        run('-i', args.source, '-t', '2', '-map', '0:a:0', '-map', '0:a:0', '-c:a', 'aac', '-y', multi)
        surround = root / 'surround.wav'
        run('-i', args.source, '-t', '2', '-ac', '6', '-c:a', 'pcm_s16le', '-y', surround)
        # A structurally valid extensible stereo WAV whose channel mask claims
        # back-left/back-right, not the FL/FR layout accepted by the contract.
        unusual_mask = root / 'unusual-mask.wav'
        samples, rate = sf.read(args.source, frames=88200, dtype='float32', always_2d=True)
        if samples.shape[1] != 2 or rate != 44100:
            raise ValueError('test source must be 44.1 kHz stereo')
        valid_mask = root / 'valid-mask.wav'
        sf.write(valid_mask, samples, rate, format='WAVEX', subtype='FLOAT')
        shutil.copyfile(valid_mask, unusual_mask)
        too_short = root / 'too-short.wav'
        sf.write(too_short, samples[:2048], rate, format='WAV', subtype='FLOAT')
        empty = root / 'empty-audio.wav'
        sf.write(empty, samples[:0], rate, format='WAV', subtype='FLOAT')
        wav_bytes = bytearray(unusual_mask.read_bytes())
        fmt = wav_bytes.index(b'fmt ')
        if struct.unpack_from('<H', wav_bytes, fmt + 8)[0] != 0xfffe:
            raise AssertionError('fixture did not create extensible WAV')
        struct.pack_into('<I', wav_bytes, fmt + 8 + 20, 0x30)
        unusual_mask.write_bytes(wav_bytes)
        too_long = root / 'over-limit.flac'
        run('-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=mono', '-t', '600.5',
            '-c:a', 'flac', '-y', too_long)
        # Sparse file: valid PCM-float header declares one frame above the
        # maximum; the isolated fixture need not allocate 202 MiB of zeros.
        native_too_long = root / 'native-over-limit.wav'
        byte_count = (26460000 + 1) * 2 * 4
        with native_too_long.open('wb') as stream:
            stream.write(struct.pack('<4sI4s4sIHHIIHH4sI', b'RIFF', 36 + byte_count,
                         b'WAVE', b'fmt ', 16, 3, 2, 44100, 44100*8, 8, 32, b'data', byte_count))
            stream.truncate(44 + byte_count)
        cfg = configuration()
        cfg['media_backend']['ai_backend'] = {'python': str(args.python), 'worker': str(args.worker),
            'model': str(args.model), 'timeout_ms': 600000, 'development_root': str(root)}
        engine = api.create(cfg)
        control_destination = root / 'valid-mask-output'
        control_request = {'schema_version': 0, 'operation': 'ai_separate',
                           'source': {'uri': valid_mask.as_uri()},
                           'destination': {'uri': control_destination.as_uri()},
                           'model_id': 'umx_hq_vocals_onnx_v1'}
        rc, response = engine.submit(control_request)
        if rc or not response or not response.get('job_id'):
            raise AssertionError(('valid extensible WAV control rejected', rc, response))
        control = engine.wait(response['job_id'], timeout=180)
        if control['state'] != 'succeeded':
            raise AssertionError(('valid extensible WAV control failed', control))
        for stem in ('vocals.wav', 'accompaniment.wav'):
            run('-i', control_destination / stem, '-f', 'null', '-')
        for source in (bad, truncated, multi, surround, unusual_mask, too_long, too_short, native_too_long, empty):
            dest = root / (source.stem + '-output')
            before = sha(source)
            request = {'schema_version': 0, 'operation': 'ai_separate',
                       'source': {'uri': source.as_uri()}, 'destination': {'uri': dest.as_uri()},
                       'model_id': 'umx_hq_vocals_onnx_v1'}
            rc, response = engine.submit(request)
            row = {'case': source.name, 'source_sha256': before, 'ffi_rc': rc}
            if response and response.get('job_id'):
                final = engine.wait(response['job_id'], timeout=60)
                row['state'], row['error'] = final['state'], final.get('error')
                rejected = final['state'] == 'failed'
            else:
                row['rejection'] = response
                rejected = rc == 0 and response and response.get('accepted') is False
            if source in (too_short, native_too_long, empty):
                rejected = (rc == 0 and response and response.get('accepted') is False and
                            not response.get('job_id') and
                            response.get('errors', [{}])[0].get('code') == 'UNSUPPORTED_FORMAT')
            row['source_unchanged'] = sha(source) == before
            row['output_absent'] = not dest.exists()
            row['stage_absent'] = not list(root.glob('.mediastudio-ai-*'))
            row['result'] = 'PASS' if (rejected and row['source_unchanged'] and
                                       row['output_absent'] and row['stage_absent']) else 'FAIL'
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
    result = {'library_sha256': sha(args.library), 'valid_extensible_wav_control': 'PASS', 'cases': rows,
              'ffi_outputs': api.outputs, 'ffi_frees': api.frees,
              'result': 'PASS' if clean and len(rows) == 9 and
                        all(x['result'] == 'PASS' for x in rows) and api.outputs == api.frees else 'FAIL'}
    print(json.dumps(result, indent=2, allow_nan=False))
    if result['result'] != 'PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
