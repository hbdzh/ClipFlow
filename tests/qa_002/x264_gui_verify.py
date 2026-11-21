#!/usr/bin/env python3
"""Independently inspect the two actual silent x264 follow-up GUI exports."""
import argparse
import array
import hashlib
import json
import math
from pathlib import Path
import subprocess

from release_gui_verify import selected_packets_match


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ['app', 'evidence', 'source', 'copy', 'precise']:
        p.add_argument('--' + key, type=Path, required=True)
    a = p.parse_args()
    report = a.evidence / 'gui-final-verification.json'
    assert not report.exists()
    frozen = json.loads((a.evidence / 'release-assembly.json').read_text())['bundle_files']
    actual = {str(f.relative_to(a.app)): sha(f) for f in a.app.rglob('*') if f.is_file()}
    assert actual == frozen
    inputs = json.loads((a.evidence / 'gui-inputs.json').read_text())['files']
    assert all(sha(path) == row['sha256'] for path, row in inputs.items())
    commands = []
    ff, probe = '/opt/homebrew/bin/ffmpeg', '/opt/homebrew/bin/ffprobe'

    def run(argv):
        r = subprocess.run(list(map(str, argv)), stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=90)
        commands.append({'argv': list(map(str, argv)), 'exit': r.returncode,
                         'stderr': r.stderr.decode(errors='replace')})
        assert r.returncode == 0, commands[-1]
        return r.stdout

    def pcm(path):
        data = run([ff, '-nostdin', '-v', 'error', '-i', path, '-map', '0:a:0', '-f', 'f32le', '-'])
        samples = array.array('f'); samples.frombytes(data)
        assert samples and all(map(math.isfinite, samples)) and max(map(abs, samples)) == 0
        return data, len(samples)

    rows = []
    for path, seconds, frames in [(a.copy, 2, 60), (a.precise, 1, 30)]:
        meta = json.loads(run([probe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', path]))
        assert abs(float(meta['format']['duration']) - seconds) <= .04
        assert len([s for s in meta['streams'] if s['codec_type'] == 'audio']) == 1
        decode = run([ff, '-nostdin', '-v', 'error', '-xerror', '-i', path, '-map', '0',
                      '-progress', 'pipe:1', '-nostats', '-f', 'null', '-']).decode()
        assert [int(x.split('=', 1)[1]) for x in decode.splitlines() if x.startswith('frame=')][-1] == frames
        _, n = pcm(path)
        rows.append({'path': str(path), 'sha256': sha(path), 'metadata': meta,
                     'decoded_video_frames': frames, 'zero_finite_audio_values': n})

    def packets(path):
        return json.loads(run([probe, '-v', 'error', '-show_packets', '-show_entries',
            'packet=stream_index,pts,dts,duration,side_data_list,data_hash', '-show_data_hash', 'sha256',
            '-of', 'json', path]))['packets']

    meta = json.loads(run([probe, '-v', 'error', '-show_streams', '-of', 'json', a.source]))
    audio = next(s for s in meta['streams'] if s['codec_type'] == 'audio')
    assert audio['index'] == 1
    comparison = selected_packets_match(packets(a.source), packets(a.copy), audio)
    assert pcm(a.source)[0] == pcm(a.copy)[0]
    watch = json.loads((a.evidence / 'gui-resource-summary.json').read_text())
    assert watch['samples'] > 0 and watch['normal_stop_requested'] and not watch['sampled_survivors']
    report.write_text(json.dumps({'result': 'PASS', 'scope': __doc__, 'app': str(a.app),
        'bundle_files_unchanged': len(actual), 'input_hashes_unchanged': True,
        'outputs': rows, 'packet_comparison': comparison, 'copy_pcm_identical': True,
        'reused_packet_checker_sha256': sha(Path(__file__).with_name('release_gui_verify.py')),
        'script_sha256': sha(__file__), 'commands': commands}, indent=2) + '\n')
    print('PASS: two GUI exports fully decoded, exact copy packets/PCM, immutable app')


if __name__ == '__main__':
    main()
