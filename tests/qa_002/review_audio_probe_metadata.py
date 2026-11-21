#!/usr/bin/env python3
"""Independent C ABI metadata-validation controls, not real ffprobe/media evidence."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import wave

ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'tests/core_004'))
from support import API, configuration


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wavex(path, channels, mask):
    subtype = struct.pack('<IHH8s', 1, 0, 0x10, b'\x80\0\0\xaa\0\x38\x9b\x71')
    fmt = struct.pack('<HHIIHHHHI', 0xfffe, channels, 44100, 44100 * channels * 2,
                      channels * 2, 16, 22, 16, mask) + subtype
    payload = b'\0' * (44100 * channels * 2)
    body = b'WAVEfmt ' + struct.pack('<I', len(fmt)) + fmt + b'data' + struct.pack('<I', len(payload)) + payload
    path.write_bytes(b'RIFF' + struct.pack('<I', len(body)) + body)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--ffmpeg', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--core004', action='store_true')
    a = parser.parse_args()
    a.work.mkdir(parents=True, exist_ok=False)
    source = a.work / 'control.wav'
    with wave.open(str(source), 'wb') as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(44100)
        out.writeframes(b'\0' * 88200)
    baseline = {'format': {'format_name': 'wav'}, 'streams': [{
        'index': 0, 'codec_type': 'audio', 'codec_name': 'pcm_s16le',
        'sample_rate': '44100', 'channels': 1, 'channel_layout': 'mono',
        'time_base': '1/44100', 'start_pts': 0, 'duration_ts': 44100}]}
    baseline_frames = [{'best_effort_timestamp': 0, 'nb_samples': 22050},
                       {'best_effort_timestamp': 22050, 'nb_samples': 22050}]
    cases = []

    def add(name, *, frames=None, stream=None, passed=False, origin=None, path=source, enabled=True, container=None):
        meta = copy.deepcopy(baseline)
        if stream: meta['streams'][0].update(stream)
        if container: meta['format']['format_name'] = container
        cases.append((name, meta, frames if frames is not None else baseline_frames,
                      passed, origin, path, enabled))

    add('continuous-positive-control', passed=True, origin=0)
    add('negative-origin-control', frames=[{'best_effort_timestamp': -44100, 'nb_samples': 22050},
                                          {'best_effort_timestamp': -22050, 'nb_samples': 22050}],
        passed=True, origin=-1000000)
    add('one-source-tick-quantization-control', frames=[baseline_frames[0],
        {'best_effort_timestamp': 22051, 'nb_samples': 22050}], passed=True, origin=0)
    add('two-sample-gap-rejected', frames=[baseline_frames[0], {'best_effort_timestamp': 22052, 'nb_samples': 22050}])
    add('two-sample-overlap-rejected', frames=[baseline_frames[0], {'best_effort_timestamp': 22048, 'nb_samples': 22050}])
    add('coarse-timebase-rejected', stream={'time_base': '1/1'},
        frames=[{'best_effort_timestamp': 0, 'nb_samples': 44100}, {'best_effort_timestamp': 1, 'nb_samples': 44100}])
    add('repeated-pts-rejected', frames=[baseline_frames[0], baseline_frames[0]])
    add('zero-samples-rejected', frames=[{'best_effort_timestamp': 0, 'nb_samples': 0}])
    add('shorter-than-one-second-rejected', frames=[baseline_frames[0]])
    add('longer-than-600-seconds-rejected', frames=[{'best_effort_timestamp': 0, 'nb_samples': 44100 * 601}])
    add('extreme-integers-fail-without-panic', frames=[{'best_effort_timestamp': -(2**63), 'nb_samples': 2**63 - 1}])
    add('unsupported-layout-rejected', stream={'channels': 2, 'channel_layout': '2 channels (FL+FC)'})
    add('unsupported-sample-rate-rejected', stream={'sample_rate': '96000'})
    add('unsupported-codec-rejected', stream={'codec_name': 'pcm_s32le'})
    add('closed-extension-rejects-audio', enabled=False)
    for channels, mask in [(1, 0), (1, 4), (2, 0), (2, 3), (1, 1), (2, 5)]:
        path = a.work / f'extensible-{channels}-{mask}.wav'
        wavex(path, channels, mask)
        add(f'wavex-mask-{channels}-{mask}', path=path,
            stream={'channels': channels, 'channel_layout': 'mono' if channels == 1 else 'stereo'},
            passed=(channels, mask) in [(1, 0), (1, 4), (2, 0), (2, 3)], origin=0)

    m4a = a.work / 'aac-content-control.m4a'
    subprocess.run([str(a.ffmpeg), '-nostdin', '-v', 'error', '-i', str(source),
                    '-c:a', 'aac', '-f', 'ipod', str(m4a)], check=True)
    aac_frames = [{'best_effort_timestamp': p, 'nb_samples': 1024} for p in range(0, 89088, 1024)]
    aac_stream = {'codec_name': 'aac', 'profile': 'LC', 'duration_ts': 88200}
    def aac(name, *, stream=None, frames=aac_frames, passed=False, origin=None):
        add(name, path=m4a, stream={**aac_stream, **(stream or {})}, frames=frames,
            container='mov,mp4,m4a,3gp,3g2,mj2', passed=passed, origin=origin)
    aac('aac-last-frame-padding-control', passed=True, origin=0)
    aac('aac-multi-frame-tail-trim-rejected', stream={'duration_ts': 86000})
    aac('aac-end-before-last-frame-rejected', stream={'duration_ts': 88064})
    aac('aac-end-after-decoded-frame-rejected', stream={'duration_ts': 90112})
    aac('aac-declared-start-mismatch-rejected', stream={'start_pts': 100})
    aac('aac-negative-origin-padding-control', stream={'start_pts': -88200},
        frames=[{**f, 'best_effort_timestamp': f['best_effort_timestamp'] - 88200} for f in aac_frames],
        passed=True, origin=-2000000)

    core_before = digest(a.library)
    api = API(a.library)
    records = []
    for name, meta, frames, expected_success, expected_origin, path, enabled in cases:
        own = a.work / name; own.mkdir()
        wrapper = own / 'ffprobe-stub'
        payload = {'streams': meta['streams'], 'format': meta['format']}
        script = f'''#!{sys.executable}
import json,sys
if '-version' in sys.argv:
 print('ffprobe version 7.1.1 QA-metadata-validation-stub')
elif '-show_frames' in sys.argv:
 print({json.dumps({'frames': frames})!r})
else:
 print(json.dumps({payload!r}))
'''
        wrapper.write_text(script); wrapper.chmod(0o700)
        before = digest(path)
        engine = api.create(configuration(core004=a.core004 and enabled, ffmpeg=a.ffmpeg, ffprobe=wrapper, core003_enabled=enabled))
        try:
            rc, receipt = engine.submit({'schema_version': 0, 'operation': 'probe', 'source': {'uri': path.as_uri()}})
            assert rc == 0, (name, rc, receipt)
            if receipt.get('job_id'):
                result = engine.wait(receipt['job_id'])
                success = result['state'] == 'succeeded'
            else:
                assert receipt.get('accepted') is False and receipt.get('errors') and not receipt.get('job_ids'), (name, receipt)
                result = None
                success = False
            assert success == expected_success, (name, result)
            if success:
                info = result['result']['media_info']
                assert info['media_kind'] == 'audio' and 'video_origin_us' not in info
                assert 'video_frame_count' not in info and 'video_cfr_rate' not in info
                assert info['audio_origin_us'] == expected_origin
                assert 1000000 <= info['duration_us'] <= 600000000
            assert before == digest(path), name
            records.append({'name': name, 'result': 'PASS', 'expected': 'succeeded' if expected_success else 'failed',
                            'source_sha256': before, 'stub_sha256': digest(wrapper), 'receipt': receipt, 'snapshot': result})
        finally:
            assert engine.destroy() == 0
    assert api.outputs == api.frees and core_before == digest(a.library)
    report = {'result': 'PASS', 'scope': 'Independent actual Core C ABI metadata-validation controls using explicit test-only ffprobe JSON stubs; no claim of real decode, GUI, signed helper, audio quality, or media acceptance',
              'core': str(a.library), 'core_sha256': core_before, 'core_file_unchanged': True,
              'core004_prepared_mode': a.core004, 'checks': records,
              'ffi_outputs': api.outputs, 'ffi_frees': api.frees,
              'qa_script_sha256': digest(Path(__file__)), 'argv': sys.argv}
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({'result': 'PASS', 'cases': len(records), 'ffi_outputs': api.outputs, 'ffi_frees': api.frees}))


if __name__ == '__main__':
    main()
