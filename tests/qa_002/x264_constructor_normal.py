#!/usr/bin/env python3
"""Compare ordinary valid encoder lifecycles across fixed baseline/new helpers.

No failure injection, malformed input, device, playback or vulnerability test.
"""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ['baseline', 'patched', 'work', 'report']:
        p.add_argument('--' + key, type=Path, required=True)
    a = p.parse_args()
    a.work.mkdir(parents=True, exist_ok=False)
    assert not a.report.exists()
    commands, cases = [], []

    def run(argv):
        r = subprocess.run(list(map(str, argv)), stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=90)
        commands.append({'argv': list(map(str, argv)), 'exit': r.returncode,
                         'stderr': r.stderr.decode(errors='replace')})
        assert r.returncode == 0, commands[-1]
        return r.stdout

    for depth in [8, 10]:
        pix = 'yuv420p' if depth == 8 else 'yuv420p10le'
        for threads, sync in [(1, 0), (4, 0), (4, 10)]:
            results = {}
            for label, tool in [('baseline', a.baseline), ('patched', a.patched)]:
                output = a.work / f'{depth}-{threads}-{sync}-{label}.h264'
                run([tool, '-nostdin', '-v', 'warning', '-f', 'lavfi', '-i',
                     'testsrc2=size=320x180:rate=30:duration=1', '-an', '-c:v',
                     'libx264', '-pix_fmt', pix, '-threads', threads, '-preset',
                     'medium', '-crf', '23', '-x264-params',
                     f'sync-lookahead={sync}:lookahead-threads=2', output])
                decoded = run([tool, '-nostdin', '-v', 'error', '-xerror', '-i',
                               output, '-map', '0:v:0', '-pix_fmt', pix,
                               '-f', 'rawvideo', '-'])
                expected = 320 * 180 * 3 // 2 * 30 * (2 if depth == 10 else 1)
                assert len(decoded) == expected, (len(decoded), expected)
                results[label] = {'file': str(output), 'encoded_sha256': sha(output),
                                  'decoded_sha256': hashlib.sha256(decoded).hexdigest(),
                                  'decoded_bytes': len(decoded)}
            assert results['baseline']['decoded_sha256'] == results['patched']['decoded_sha256']
            assert results['baseline']['encoded_sha256'] == results['patched']['encoded_sha256']
            cases.append({'bit_depth': depth, 'threads': threads, 'sync_lookahead': sync,
                          'frames': 30, 'encoded_and_decoded_identical': True, 'results': results})
    a.report.write_text(json.dumps({'result': 'PASS', 'scope': __doc__,
        'baseline_sha256': sha(a.baseline), 'patched_sha256': sha(a.patched),
        'script_sha256': sha(__file__), 'cases': cases, 'commands': commands}, indent=2) + '\n')
    print('PASS: six normal configurations, twelve encodes, bit-identical baseline/new output')


if __name__ == '__main__':
    main()
