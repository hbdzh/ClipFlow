#!/usr/bin/env python3
"""Generate small owned fixtures. Never reads user media or invokes a shell."""
import json
from pathlib import Path
import shutil
import sys

sys.dont_write_bytecode = True
from common import HERE, ROOT, BUILD, EVIDENCE, Commands, sha256, write_json, timestamp, relative, require_owned_build, require_build_root, temporary_output


def main():
    rules_path = HERE / 'rules.json'
    rules = json.loads(rules_path.read_text())
    before_rules = sha256(rules_path)
    require_build_root()
    BUILD.mkdir(parents=True, exist_ok=True)
    inputs = BUILD / 'inputs'
    require_owned_build(inputs)
    inputs.mkdir(exist_ok=True)
    commands = Commands()
    ffmpeg = shutil.which('ffmpeg')
    ffprobe = shutil.which('ffprobe')
    assert ffmpeg and ffprobe, 'BLOCKED: ffmpeg and ffprobe are required'
    tools = {name: commands.run([path, '-version']).stdout for name, path in [('ffmpeg', ffmpeg), ('ffprobe', ffprobe)]}
    license_text = commands.run([ffmpeg, '-L'])
    prefix = [ffmpeg, '-hide_banner', '-nostdin', '-y', '-v', 'error', '-fflags', '+bitexact']
    video_input = ['-f', 'lavfi', '-i', 'testsrc2=size=160x96:rate=30:duration=4']
    audio_input = ['-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=4']
    video_options = ['-c:v', 'libx264', '-threads:v', '1', '-preset', 'medium', '-crf', '24',
                     '-g', '30', '-keyint_min', '30', '-sc_threshold', '0', '-bf', '2',
                     '-pix_fmt', 'yuv420p', '-flags:v', '+bitexact', '-video_track_timescale', '30000']
    audio_options = ['-c:a', 'aac', '-threads:a', '1', '-b:a', '64k', '-flags:a', '+bitexact']
    output_options = ['-map_metadata', '-1', '-movflags', '+faststart']
    files = {}

    def generate(identifier, args):
        path = require_owned_build(inputs / rules['fixtures'][identifier]['filename'])
        path.parent.mkdir(parents=True, exist_ok=True)
        with temporary_output(path, 'generation') as temporary:
            commands.run(prefix + args + output_options + [str(temporary)])
            assert temporary.is_file() and temporary.stat().st_size > 0
            temporary.replace(path)
        files[identifier] = {'path': relative(path), 'sha256': sha256(path), 'bytes': path.stat().st_size,
                             'provenance': 'Self-generated lavfi video/test tones; no third-party media.'}

    generate('cfr_bframes', video_input + audio_input + ['-map', '0:v:0', '-map', '1:a:0'] + video_options + audio_options)
    generate('vfr', video_input + ['-map', '0:v:0', '-vf', "select='not(mod(n,2))+not(mod(n,5))'", '-fps_mode', 'vfr'] + video_options)
    generate('multi_audio', video_input + audio_input + ['-f', 'lavfi', '-i', 'sine=frequency=880:sample_rate=48000:duration=4',
             '-map', '0:v:0', '-map', '1:a:0', '-map', '2:a:0'] + video_options + audio_options +
             ['-metadata:s:a:0', 'language=eng', '-metadata:s:a:1', 'language=zho'])
    generate('no_audio', video_input + ['-map', '0:v:0'] + video_options)
    source = ROOT / files['cfr_bframes']['path']
    for identifier in ['unicode_path', 'truncated']:
        path = require_owned_build(inputs / rules['fixtures'][identifier]['filename'])
        path.parent.mkdir(parents=True, exist_ok=True)
        content = source.read_bytes()
        if identifier == 'truncated':
            content = content[:rules['fixtures'][identifier]['retained_bytes']]
        with temporary_output(path, 'generation') as temporary:
            temporary.write_bytes(content)
            temporary.replace(path)
        files[identifier] = {'path': relative(path), 'sha256': sha256(path), 'bytes': path.stat().st_size,
                             'provenance': 'Owned copy/truncation of cfr_bframes; not external media.'}
    assert before_rules == sha256(rules_path), 'Rules changed during generation'
    report = {'captured_at': timestamp(), 'scope': 'QA-001 fixture generation only; not Core media acceptance',
              'rules_sha256_before_generation': before_rules, 'tools': tools,
              'ffmpeg_license_stdout': license_text.stdout, 'ffmpeg_license_stderr': license_text.stderr,
              'files': files, 'commands': commands.records}
    write_json(EVIDENCE / 'generation.json', report)
    print(f'Generated {len(files)} owned fixtures in ignored build/; rules {before_rules}')


if __name__ == '__main__':
    main()
