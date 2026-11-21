#!/usr/bin/env python3
"""Verify this dependency candidate's real GUI outputs without playback."""
import argparse
import array
import hashlib
import json
import math
from pathlib import Path
import subprocess

from release_gui_verify import selected_packets_match


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def main():
    p = argparse.ArgumentParser()
    for key in ['app', 'evidence', 'work']:
        p.add_argument('--' + key, required=True, type=Path)
    a = p.parse_args()
    ev, work = a.evidence, a.work
    assert not (ev / 'gui-final-verification.json').exists()
    assembly = json.loads((ev / 'release-assembly.json').read_text())
    actual = {str(f.relative_to(a.app)): sha(f) for f in a.app.rglob('*') if f.is_file()}
    assert actual == assembly['bundle_files']
    inputs = json.loads((ev / 'gui-inputs.json').read_text())['files']
    assert all(sha(path) == row['sha256'] for path, row in inputs.items())
    commands = []

    def run(argv, binary=False):
        r = subprocess.run(list(map(str, argv)), stdin=subprocess.DEVNULL, capture_output=True, timeout=90)
        commands.append({'argv': list(map(str, argv)), 'exit': r.returncode,
                         'stderr': r.stderr.decode(errors='replace')})
        assert r.returncode == 0, commands[-1]
        return r.stdout if binary else r.stdout.decode()

    ff, probe = '/opt/homebrew/bin/ffmpeg', '/opt/homebrew/bin/ffprobe'
    root = work / 'GUI outputs'
    outputs, copy_pcm = [], None
    for path, seconds, frames in [(root / 'v2-copy.mp4', 2, 60),
            (root / 'v2-precise.mp4', 1, 30),
            (root / 'v2 stems/vocals.wav', 60, None),
            (root / 'v2 stems/accompaniment.wav', 60, None)]:
        meta = json.loads(run([probe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', path]))
        assert abs(float(meta['format']['duration']) - seconds) <= .04
        decode = run([ff, '-nostdin', '-v', 'error', '-xerror', '-i', path, '-map', '0',
                      '-progress', 'pipe:1', '-nostats', '-f', 'null', '-'])
        audio = [s for s in meta['streams'] if s['codec_type'] == 'audio']
        assert len(audio) == 1
        pcm = run([ff, '-nostdin', '-v', 'error', '-i', path, '-map', '0:a:0', '-f', 'f32le', '-'], True)
        samples = array.array('f')
        samples.frombytes(pcm)
        assert samples and all(map(math.isfinite, samples)) and max(map(abs, samples)) == 0
        if path.name == 'v2-copy.mp4':
            copy_pcm = pcm
        if frames is None:
            assert len(samples) == 44100 * 2 * seconds
            assert (audio[0]['codec_name'], audio[0]['sample_rate'], audio[0]['channels']) == ('pcm_f32le', '44100', 2)
        else:
            counts = [int(x.split('=', 1)[1]) for x in decode.splitlines() if x.startswith('frame=')]
            assert counts[-1] == frames
        outputs.append({'path': str(path), 'sha256': sha(path), 'metadata': meta,
                        'decoded_video_frames': frames, 'audio_sample_values': len(samples),
                        'zero_audio': True, 'all_finite': True, 'full_decode_exit': 0})

    def packets(path):
        return json.loads(run([probe, '-v', 'error', '-show_packets', '-show_entries',
            'packet=stream_index,pts,dts,duration,side_data_list,data_hash', '-show_data_hash', 'sha256', '-of', 'json', path]))['packets']

    source = work / 'GUI inputs/v2 silent video.mp4'
    source_meta = json.loads(run([probe, '-v', 'error', '-show_streams', '-of', 'json', source]))
    source_audio = next(s for s in source_meta['streams'] if s['codec_type'] == 'audio')
    assert source_audio['index'] == 1
    comparison = selected_packets_match(packets(source), packets(root / 'v2-copy.mp4'), source_audio)
    assert copy_pcm == run([ff, '-nostdin', '-v', 'error', '-i', source, '-map', '0:a:0', '-f', 'f32le', '-'], True)
    rss = json.loads((ev / 'gui-resource-summary.json').read_text())
    assert rss['samples'] > 0 and rss['normal_stop_requested'] and not rss['sampled_survivors']
    result = {'result': 'PASS', 'scope': 'Actual signed candidate CUA exports and normal restart; no playback/listening/VoiceOver or repeated GUI memory/heartbeat claim',
        'app': str(a.app.resolve()), 'bundle_files_unchanged': len(actual), 'input_hashes_unchanged': True,
        'outputs': outputs, 'packet_comparison': comparison, 'copy_audio_pcm_identical': True,
        'reused_packet_checker_sha256': sha(Path(__file__).with_name('release_gui_verify.py')),
        'script_sha256': sha(__file__), 'commands': commands}
    (ev / 'gui-final-verification.json').write_text(json.dumps(result, indent=2) + '\n')
    print('PASS: four GUI outputs fully decoded, exact copy packets/PCM, minute-long dual stems and immutable app')


if __name__ == '__main__':
    main()
