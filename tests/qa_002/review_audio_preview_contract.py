#!/usr/bin/env python3
"""Independent public-request syntax controls for the fixed preview proxy operation."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
import wave

ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'tests/core_004'))
from support import API, configuration, control


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--library', type=Path, required=True)
    p.add_argument('--ffmpeg', type=Path, required=True)
    p.add_argument('--ffprobe', type=Path, required=True)
    p.add_argument('--work', type=Path, required=True)
    p.add_argument('--report', type=Path, required=True)
    a = p.parse_args(); a.work.mkdir(parents=True, exist_ok=False)
    source = a.work / 'source.wav'
    with wave.open(str(source), 'wb') as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(44100)
        f.writeframes(b'\0' * 88200)
    source_hash = digest(source); core_hash = digest(a.library)
    api = API(a.library)
    e = api.create(configuration(ffmpeg=a.ffmpeg, ffprobe=a.ffprobe))
    base = {'schema_version': 0, 'operation': 'prepare_audio_preview',
            'source': {'uri': source.as_uri()}, 'destination': {'uri': (a.work / 'preview.wav').as_uri()},
            'profile': 'wav_pcm_f32_preview_v1'}
    rows = []
    def run_bad(name, req, expected):
        rc, before = e.submit(control('queue_snapshot')); assert rc == 0
        submit_rc, value = e.submit(req)
        assert submit_rc == expected and value is None, (name, submit_rc, value)
        rc, after = e.submit(control('queue_snapshot')); assert rc == 0 and before == after, (name, before, after)
        assert not (a.work / 'preview.wav').exists() and digest(source) == source_hash
        rows.append({'name': name, 'result': 'PASS', 'request': req, 'expected_abi_error': expected,
                     'actual_abi_error': submit_rc, 'no_job_or_output': True})
    try:
        for value in [None, 3, '', 'wav_pcm_f32_preview_v2', 'mp4_h264_standard_v1']:
            req = copy.deepcopy(base)
            if value is None: req.pop('profile')
            else: req['profile'] = value
            run_bad(f'fixed-profile-{value}', req, -1)
        for key, value in [('range', {'start_us': 0, 'end_us': 1000000}), ('range', None),
                           ('sample_rate', 48000), ('channels', 2), ('allow_reencode_fallback', True),
                           ('command', ['-i', 'anything'])]:
            run_bad(f'unknown-field-{key}-{value}', {**base, key: value}, -1)
        for part in ['source', 'destination']:
            for uri in ['https://example.invalid/private.wav', 'file://remote-host/private.wav', 'file:///private/tmp/a.wav?query=1']:
                req = copy.deepcopy(base); req[part]['uri'] = uri
                run_bad(f'{part}-local-uri-only-{uri}', req, -1)
            req = copy.deepcopy(base); req[part]['extra'] = 'not-allowed'
            run_bad(f'{part}-unknown-location-field', req, -1)
        run_bad('unsupported-schema', {**base, 'schema_version': 1}, -4)
        run_bad('case-sensitive-operation', {**base, 'operation': 'PrepareAudioPreview'}, -8)
    finally:
        assert e.destroy() == 0
    assert api.outputs == api.frees and digest(a.library) == core_hash
    report = {'result': 'PASS', 'scope': 'Actual Core C ABI malformed request / no ghost controls only; no successful proxy, media, GUI, or release claim',
              'core_sha256': core_hash, 'core_file_unchanged': True, 'source_sha256': source_hash,
              'checks': rows, 'ffi_outputs': api.outputs, 'ffi_frees': api.frees,
              'qa_script_sha256': digest(Path(__file__)), 'argv': sys.argv}
    a.report.parent.mkdir(parents=True, exist_ok=True); a.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'result': 'PASS', 'cases': len(rows), 'ffi_outputs': api.outputs, 'ffi_frees': api.frees}))


if __name__ == '__main__':
    main()
