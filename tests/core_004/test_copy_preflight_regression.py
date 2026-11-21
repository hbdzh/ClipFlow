#!/usr/bin/env python3
"""CORE-004 copy preflight regression: real C ABI, GOP boundaries, no golden changes."""
import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path
from support import API, configuration, control


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def probe(path, ffprobe):
    return json.loads(subprocess.run([
        str(ffprobe), '-v', 'error', '-show_streams', '-show_packets',
        '-show_data_hash', 'sha256', '-show_entries',
        'stream=index,codec_type,time_base:packet=stream_index,pts,dts,data_hash',
        '-of', 'json', str(path)], capture_output=True, text=True, check=True).stdout)


def covers_expected_range(kind, source_packets, selected_packets, time_base, start, end):
    """Tie the copied packet sequence to the requested source interval."""
    pts = lambda packet: int(packet['pts']) * time_base
    if kind == 'video':
        expected = sorted(pts(p) for p in source_packets if start <= pts(p) < end)
        return bool(expected) and sorted(pts(p) for p in selected_packets) == expected
    # Bounds are specific to the fixed 30 fps/B-frame/48 kHz AAC fixture.
    values = [pts(p) for p in selected_packets]
    return (bool(values) and abs(min(values) - start) <= Fraction(100, 1000)
            and abs(max(values) - end) <= Fraction(80, 1000))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--source', type=Path, help='existing 4-second, 30 fps, 1-second GOP H.264/AAC fixture')
    parser.add_argument('--ffmpeg', type=Path, default=shutil.which('ffmpeg'))
    parser.add_argument('--ffprobe', type=Path, default=shutil.which('ffprobe'))
    options = parser.parse_args()
    if not options.ffmpeg or not options.ffprobe:
        parser.error('ffmpeg and ffprobe are required')
    api = API(options.library)
    with tempfile.TemporaryDirectory(prefix='qa-copy-preflight-', dir='/private/tmp') as temp:
        directory = Path(temp)
        source = options.source.resolve(strict=True) if options.source else directory / 'source.mp4'
        if not options.source:
            subprocess.run([str(options.ffmpeg), '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                'testsrc2=size=320x180:rate=30:duration=4', '-f', 'lavfi', '-i',
                'anoisesrc=color=pink:amplitude=0.2:sample_rate=48000:duration=4:seed=1234',
                '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'libx264', '-threads:v', '1',
                '-g', '30', '-keyint_min', '30', '-sc_threshold', '0', '-bf', '2',
                '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-threads:a', '1', str(source)], check=True)
        before = digest(source)
        engine = api.create(configuration(ffmpeg=options.ffmpeg, ffprobe=options.ffprobe))
        try:
            for label, end, expected_end in [('middle', 2600000, 3000000), ('eof', 3600000, 4000000)]:
                dest = directory / (label + '.mp4')
                request = {'schema_version': 0, 'operation': 'trim',
                    'source': {'uri': source.as_uri()}, 'destination': {'uri': dest.as_uri()},
                    'range': {'start_us': 1100000, 'end_us': end}, 'strategy': 'stream_copy',
                    'profile': 'mp4_h264_copy_v1', 'stream_selection': 'all_supported',
                    'allow_reencode_fallback': False}
                rc, receipt = engine.submit(control('batch_submit', submission_id='copy_'+label, items=[request]))
                assert rc == 0 and receipt['accepted'], (label, rc, receipt)
                final = engine.wait(receipt['job_ids'][0])
                assert final['state'] == 'succeeded', (label, final)
                result = final['result']
                assert result['actual_range_us'] == {'start_us': 1000000, 'end_us': expected_end}, result
                assert not result['reencoded'] and result['lossless'], result
                source_probe, output_probe = probe(source, options.ffprobe), probe(dest, options.ffprobe)
                shifts = []
                for kind in ('video', 'audio'):
                    ss = next(x for x in source_probe['streams'] if x['codec_type'] == kind)
                    ds = next(x for x in output_probe['streams'] if x['codec_type'] == kind)
                    sp = [x for x in source_probe['packets'] if x['stream_index'] == ss['index']]
                    dp = [x for x in output_probe['packets'] if x['stream_index'] == ds['index']]
                    sh = [x['data_hash'] for x in sp]
                    dh = [x['data_hash'] for x in dp]
                    matches = [i for i in range(len(sh)-len(dh)+1) if sh[i:i+len(dh)] == dh]
                    assert dh and matches, (label, kind, 'payloads are not a contiguous source sequence')
                    st, dt = Fraction(ss['time_base']), Fraction(ds['time_base'])
                    start, end_time = Fraction(1), Fraction(expected_end, 1000000)
                    matches = [i for i in matches if covers_expected_range(
                        kind, sp, sp[i:i+len(dp)], st, start, end_time)]
                    assert matches, (label, kind, 'source packet interval differs from actual range')
                    if kind == 'video' and label == 'middle':
                        chosen = sp[matches[0]:matches[0]+len(dp)]
                        assert not covers_expected_range(kind, sp, sp[:len(dp)], st, start, end_time), 'wrong-range negative check'
                        assert not covers_expected_range(kind, sp, chosen[:-1], st, start, end_time), 'truncated-output negative check'
                    if kind == 'audio' and label == 'middle':
                        chosen = sp[matches[0]:matches[0]+len(dp)]
                        assert not covers_expected_range(kind, sp, chosen[:-1], st, start, end_time), 'truncated audio negative check'
                    dts = [int(x['dts']) for x in dp]
                    assert all(a < b for a, b in zip(dts, dts[1:])), (label, kind, 'nonmonotonic DTS')
                    mapped = [int(a['pts'])*st-int(b['pts'])*dt for a, b in zip(sp[matches[0]:], dp)]
                    assert max(mapped)-min(mapped) <= Fraction(2,1000), (label, kind, 'inconsistent timing')
                    shifts.append(mapped[0])
                assert abs(shifts[0]-shifts[1]) <= Fraction(2,1000), (label, shifts)
                subprocess.run([str(options.ffmpeg), '-v', 'error', '-xerror', '-i', str(dest),
                    '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'], check=True)
                print(json.dumps({'scenario':label, 'state':final['state'], 'actual_range_us':result['actual_range_us'],
                    'sha256':digest(dest), 'packet_shifts_seconds':[str(x) for x in shifts]}), flush=True)
            existing = directory / 'existing.mp4'
            existing.write_bytes(b'unchanged existing target')
            existing_before = digest(existing)
            request['destination'] = {'uri': existing.as_uri()}
            rc, rejected = engine.submit(control('batch_submit', submission_id='copy_existing', items=[request]))
            assert rc == 0 and not rejected['accepted'], (rc, rejected)
            assert any(item['code'] == 'OUTPUT_EXISTS' for item in rejected['errors']), rejected
            assert digest(existing) == existing_before, 'existing target modified'
            assert digest(source) == before, 'source modified'
        finally:
            assert engine.destroy() == 0
    print('PASS: middle and EOF copy preflight, payloads, time shift, decode, source and existing target integrity')


if __name__ == '__main__':
    main()
