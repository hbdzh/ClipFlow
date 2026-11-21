#!/usr/bin/env python3
"""Normal valid A/B encodes for late-abort success-path compatibility only.

No malformed input, failure injection, device, playback or CVE reproduction.
"""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path


CONFIGURATIONS = [
    ('serial420', '420', 1, 0, False, False, 'crf'),
    ('frame420', '420', 4, 10, False, False, 'crf'),
    ('slice420', '420', 4, 0, True, False, 'crf'),
    ('frame444', '444', 4, 10, False, False, 'crf'),
    ('slice444', '444', 4, 0, True, False, 'crf'),
    ('frame-interlaced', '420', 4, 10, False, True, 'crf'),
    ('slice-interlaced', '420', 4, 0, True, True, 'crf'),
    ('frame-abr', '420', 4, 10, False, False, 'abr'),
    ('frame-cqp', '420', 4, 10, False, False, 'cqp'),
]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ['baseline', 'patched', 'decoder', 'probe', 'work', 'report']:
        p.add_argument('--' + key, type=Path, required=True)
    p.add_argument('--case', choices=[row[0] for row in CONFIGURATIONS])
    p.add_argument('--bit-depth', type=int, choices=[8, 10])
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

    for depth in ([a.bit_depth] if a.bit_depth else [8, 10]):
        for name, chroma, threads, sync, sliced, interlaced, rc in CONFIGURATIONS:
            if a.case and name != a.case:
                continue
            pix = f'yuv{chroma}p' + ('10le' if depth == 10 else '')
            params = f'sync-lookahead={sync}:lookahead-threads=2:sliced-threads={int(sliced)}:tff={int(interlaced)}'
            field_flags = ['-vf', 'setfield=tff'] if interlaced else []
            rate = ['-crf', '23'] if rc == 'crf' else ['-b:v', '192k'] if rc == 'abr' else ['-qp', '24']
            results = {}
            for label, tool in [('baseline', a.baseline), ('patched', a.patched)]:
                output = a.work / f'{depth}-{name}-{label}.h264'
                run([tool, '-nostdin', '-v', 'warning', '-f', 'lavfi', '-i',
                     'testsrc2=size=320x256:rate=30:duration=1', *field_flags, '-an', '-c:v',
                     'libx264', '-pix_fmt', pix, '-threads', threads, '-preset',
                     'medium', *rate, '-x264-params', params, output])
                data = output.read_bytes()
                options = data.split(b' options: ', 1)[1].split(b'\x00', 1)[0].decode('ascii')
                actual = dict(re.findall(r'(\w+)=([^ ]+)', options))
                assert int(actual['threads']) == 1 if threads == 1 else int(actual['threads']) > 1
                assert int(actual['sliced_threads']) == int(sliced)
                assert actual['interlaced'] == ('tff' if interlaced else '0')
                assert actual['rc'] == rc
                metadata = json.loads(run([a.probe, '-v', 'error', '-count_frames',
                    '-show_streams', '-of', 'json', output]))['streams']
                assert len(metadata) == 1
                stream = metadata[0]
                assert (stream['width'], stream['height'], stream['pix_fmt'], stream['nb_read_frames']) == (320, 256, pix, '30')
                assert stream['field_order'] in ('tt', 'tb') if interlaced else stream['field_order'] == 'progressive'
                decoded = run([a.decoder, '-nostdin', '-v', 'error', '-xerror',
                    '-threads', '1', '-i', output, '-map', '0:v:0', '-pix_fmt', pix,
                    '-f', 'rawvideo', '-'])
                expected = 320 * 256 * (3 if chroma == '444' else 3 / 2) * 30 * (2 if depth == 10 else 1)
                assert len(decoded) == expected, (name, len(decoded), expected)
                results[label] = {'file': str(output), 'encoded_sha256': sha(output),
                    'decoded_sha256': hashlib.sha256(decoded).hexdigest(), 'decoded_bytes': len(decoded),
                    'actual_encoder_options': actual, 'stream': stream}
            assert results['baseline']['decoded_sha256'] == results['patched']['decoded_sha256'], name
            assert results['baseline']['encoded_sha256'] == results['patched']['encoded_sha256'], name
            cases.append({'name': name, 'bit_depth': depth, 'chroma': chroma, 'threads': threads,
                'sync_lookahead': sync, 'sliced': sliced, 'interlaced_requested': interlaced,
                'rate_control': rc, 'frames': 30, 'encoded_and_decoded_identical': True, 'results': results})
    a.report.write_text(json.dumps({'result': 'PASS', 'scope': __doc__,
        'baseline_sha256': sha(a.baseline), 'patched_sha256': sha(a.patched),
        'decoder_sha256': sha(a.decoder), 'probe_sha256': sha(a.probe), 'script_sha256': sha(__file__),
        'cases': cases, 'commands': commands}, indent=2) + '\n')
    print(f'PASS: {len(cases)} selected normal configurations,{2 * len(cases)} encodes, baseline/new bit-identical output')


if __name__ == '__main__':
    main()
