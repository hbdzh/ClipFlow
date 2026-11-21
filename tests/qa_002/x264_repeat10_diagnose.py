#!/usr/bin/env python3
"""Bounded normal x264 10-bit slice repeatability investigation; no fault inputs."""
import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

BASE = 'sync-lookahead=0:lookahead-threads=2:sliced-threads=1'
VARIANTS = {
    'base': ('', ['-crf', '23']),
    'cpu-none': (':asm=0', ['-crf', '23']),
    'lookahead-one': (':lookahead-threads=1', ['-crf', '23']),
    'mbtree-off': (':mbtree=0', ['-crf', '23']),
    'aq-off': (':aq-mode=0', ['-crf', '23']),
    'cqp': ('', ['-qp', '24']),
}

def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ['ffmpeg', 'source', 'work', 'report']:
        p.add_argument('--' + key, type=Path, required=True)
    p.add_argument('--chroma', choices=['420', '444'], default='420')
    p.add_argument('--interlaced', action='store_true')
    p.add_argument('--require-repeatable', action='store_true')
    p.add_argument('--bit-depth', type=int, choices=[8, 10], default=10)
    p.add_argument('--variant', choices=VARIANTS, action='append')
    p.add_argument('--repeats', type=int, default=3)
    a = p.parse_args()
    assert 2 <= a.repeats <= 3
    a.work.mkdir(parents=True, exist_ok=False)
    assert not a.report.exists()
    frozen = a.work / 'input.yuv'
    shutil.copyfile(a.source, frozen)
    expected_source = sha(a.source)
    assert sha(frozen) == expected_source
    pix_fmt = f'yuv{a.chroma}p' + ('10le' if a.bit_depth == 10 else '')
    field_flags = ['-vf', 'setfield=tff'] if a.interlaced else []
    report = {'bit_depth': a.bit_depth, 'chroma': a.chroma, 'interlaced': a.interlaced, 'scope': __doc__, 'ffmpeg': str(a.ffmpeg), 'ffmpeg_sha256': sha(a.ffmpeg),
              'source': str(frozen), 'source_sha256': expected_source,
              'checker_sha256': sha(__file__), 'commands': [], 'cases': []}
    def save():
        a.report.write_text(json.dumps(report, indent=2) + '\n')
    def run(cmd):
        r = subprocess.run(list(map(str, cmd)), stdin=subprocess.DEVNULL, capture_output=True, timeout=90)
        report['commands'].append({'argv': list(map(str, cmd)), 'exit': r.returncode,
            'stderr': r.stderr.decode(errors='replace')})
        save()
        assert r.returncode == 0, report['commands'][-1]
        return r.stdout
    for variant in a.variant or VARIANTS:
        extra, rate = VARIANTS[variant]
        rows = []
        report['cases'].append({'variant': variant, 'rows': rows})
        for n in range(a.repeats):
            output = a.work / f'{variant}-{n}.h264'
            run([a.ffmpeg, '-nostdin', '-v', 'info', '-f', 'rawvideo', '-pixel_format', pix_fmt,
                 '-video_size', '320x256', '-framerate', '30', '-i', frozen, '-frames:v', '30', *field_flags, '-an',
                 '-c:v', 'libx264', '-pix_fmt', pix_fmt, '-threads', '4', '-preset', 'medium',
                 *rate, '-x264-params', BASE + f':tff={int(a.interlaced)}' + extra, output])
            assert not any('Error parsing option' in r['stderr'] for r in report['commands'])
            decoded = run(['/opt/homebrew/bin/ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-threads',
                '1', '-i', output, '-map', '0:v:0', '-pix_fmt', pix_fmt, '-f', 'rawvideo', '-'])
            frame_bytes = 320 * 256 * 3 * (2 if a.bit_depth == 10 else 1) // (1 if a.chroma == '444' else 2)
            assert len(decoded) == frame_bytes * 30
            info = json.loads(run(['/opt/homebrew/bin/ffprobe', '-v', 'error', '-show_streams',
                '-show_format', '-of', 'json', output]))
            assert info['streams'][0]['pix_fmt'] == pix_fmt
            assert info['streams'][0]['field_order'] in ('tt', 'tb') if a.interlaced else info['streams'][0]['field_order'] == 'progressive'
            rows.append({'repeat': n, 'output': str(output), 'encoded_sha256': sha(output),
                'decoded_sha256': hashlib.sha256(decoded).hexdigest(),
                'frame_sha256': [hashlib.sha256(decoded[k:k+frame_bytes]).hexdigest()
                                 for k in range(0, len(decoded), frame_bytes)],
                'stream': info['streams'][0]})
            save()
        case = report['cases'][-1]
        case['encoded_unique'] = len({x['encoded_sha256'] for x in rows})
        case['decoded_unique'] = len({x['decoded_sha256'] for x in rows})
        case['first_differing_frame'] = next((k for k in range(30)
            if len({x['frame_sha256'][k] for x in rows}) > 1), None)
        save()
    assert sha(a.source) == expected_source and sha(frozen) == expected_source
    report['source_unchanged'] = True
    stable = all(c['encoded_unique'] == c['decoded_unique'] == 1 for c in report['cases'])
    report['result'] = ('PASS_NORMAL_REPEATABILITY_ONLY' if stable else 'FAIL_NORMAL_REPEATABILITY') if a.require_repeatable else 'DIAGNOSTIC_COMPLETE_NOT_CVE_OR_QUALITY_ACCEPTANCE'
    save()
    if a.require_repeatable:
        assert stable, 'Same-binary encoded/decoded repeatability failed'
    save()
    print(json.dumps([{k:v for k,v in c.items() if k!='rows'} for c in report['cases']]))

if __name__ == '__main__':
    main()
