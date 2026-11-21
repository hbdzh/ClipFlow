#!/usr/bin/env python3
"""Read-only artifact binding and file decoding. Never opens or plays media."""
import argparse
import hashlib
import json
import plistlib
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(app):
    sources = sorted(p for p in (ROOT / 'core').rglob('*') if p.is_file()
                     and p.suffix in {'.rs', '.c', '.cc', '.cpp', '.h', '.hpp'}
                     and not {'target', 'build', 'CMakeFiles'}.intersection(p.relative_to(ROOT).parts))
    sources += sorted((ROOT / 'apps/macos/Sources').glob('*.swift'))
    sources += sorted((ROOT / 'contracts').glob('*.h'))
    return {
        'production_sources': {str(p.relative_to(ROOT)): digest(p) for p in sources},
        'bundle_files': {str(p.relative_to(app)): digest(p)
                         for p in sorted(app.rglob('*')) if p.is_file()},
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--app', type=Path, required=True)
    p.add_argument('--evidence', type=Path, required=True)
    p.add_argument('--freeze', action='store_true')
    p.add_argument('--verify', action='store_true')
    a = p.parse_args()
    app = a.app.resolve(strict=True)
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    assert info['CFBundleIdentifier'].startswith('local.mediastudio.qa002.native.')
    out = a.evidence
    out.mkdir(parents=True, exist_ok=True)
    if a.freeze:
        value = {'app': str(app), 'bundle_id': info['CFBundleIdentifier'], **snapshot(app)}
        (out / 'final-before-gui.json').write_text(json.dumps(value, indent=2) + '\n')
    if not a.verify:
        return
    before = json.loads((out / 'final-before-gui.json').read_text())
    after = snapshot(app)
    binding = {
        'app': str(app), 'bundle_id': info['CFBundleIdentifier'],
        'all_bundle_files_unchanged': before['bundle_files'] == after['bundle_files'],
        'all_production_sources_unchanged': before['production_sources'] == after['production_sources'],
        'file_count': len(after['bundle_files']),
        'source_count': len(after['production_sources']),
        'main_sha256': digest(app / 'Contents/MacOS/MediaStudio'),
        'core_sha256': digest(app / 'Contents/Frameworks/libmedia_ffi.dylib'),
    }
    binding['result'] = 'PASS' if all(binding[k] for k in
        ['all_bundle_files_unchanged', 'all_production_sources_unchanged']) else 'FAIL'
    (out / 'final-after-gui.json').write_text(json.dumps(binding, indent=2) + '\n')
    fixtures = json.loads((out / 'gui-fixtures.json').read_text())
    inputs = [{'path': x['copy'], 'sha256': digest(Path(x['copy'])),
               'unchanged': digest(Path(x['copy'])) == x['sha256']} for x in fixtures]
    export = Path('/private/tmp/mediastudio-qa002-native-gui/QA Exports With Spaces')
    outputs = sorted(export.glob('native-gui-v*.mp4'))
    outputs += sorted(export.glob('native-ai-v*/*.wav'))
    commands, media = [], []
    for path in outputs:
        argv = ['/opt/homebrew/bin/ffprobe', '-v', 'error', '-show_format',
                '-show_streams', '-of', 'json', str(path)]
        probe = subprocess.run(argv, capture_output=True, text=True, check=True)
        commands.append({'argv': argv, 'exit': probe.returncode})
        value = json.loads(probe.stdout)
        # Null output decodes data without any audio/video output device.
        argv = ['/opt/homebrew/bin/ffmpeg', '-nostdin', '-v', 'error', '-i',
                str(path), '-map', '0', '-f', 'null', '-']
        decoded = subprocess.run(argv, capture_output=True, text=True)
        commands.append({'argv': argv, 'exit': decoded.returncode, 'stderr': decoded.stderr})
        expected = len(value['streams']) == (2 if path.suffix == '.mp4' else 1)
        if path.suffix == '.wav':
            track = value['streams'][0]
            expected = expected and track['sample_rate'] == '44100' and track['channels'] == 2
            expected = expected and int(track['duration_ts']) == 352800
        else:
            videos = [s for s in value['streams'] if s['codec_type'] == 'video']
            audios = [s for s in value['streams'] if s['codec_type'] == 'audio']
            expected = expected and len(videos) == len(audios) == 1
            expected = expected and videos[0]['codec_name'] == 'h264'
            expected = expected and (videos[0]['width'], videos[0]['height']) == (320, 180)
            expected = expected and int(videos[0]['nb_frames']) == 240
            expected = expected and audios[0]['codec_name'] == 'aac'
            expected = expected and abs(float(value['format']['duration']) - 8) < 0.05
        media.append({'path': str(path), 'sha256': digest(path), 'probe': value,
                      'null_decode_exit': decoded.returncode, 'layout_expected': expected})
    physical = {'result': 'PASS' if all(x['unchanged'] for x in inputs)
                and all(x['null_decode_exit'] == 0 and x['layout_expected'] for x in media) else 'FAIL',
                'scope': 'Synthetic local QA files only; file decoding, no playback or listening',
                'inputs': inputs, 'outputs': media, 'commands': commands}
    (out / 'gui-physical-validation.json').write_text(json.dumps(physical, indent=2) + '\n')
    hb = out / 'heartbeat-logs'
    hb.mkdir(exist_ok=True)
    sessions = []
    for name in ['local.mediastudio.qa002.native.v2', 'local.mediastudio.qa002.native.v3']:
        temp = Path.home() / 'Library/Containers' / name / 'Data/tmp'
        for source in sorted(temp.glob('qa002-heartbeat-*.jsonl')):
            rows = [json.loads(x) for x in source.read_text().splitlines()]
            assert all(x['schema_version'] == 1 for x in rows)
            target = hb / (name + '-' + source.name)
            shutil.copy2(source, target)
            last = rows[-1]
            sessions.append({'container': name, 'file': str(target.relative_to(out)),
                'session': last['session'], 'last_kind': last['kind'],
                'elapsed_ms': last['elapsed_ms'], 'sample_count': last['sample_count'],
                'active_count': last['active_count'], 'measured_max_ms': last['measured_max_ms'],
                'active_max_ms': last['active_max_ms'],
                'within_500_ms': last['measured_count'] > 0 and last['measured_max_ms'] <= 500,
                'geometries': sorted({json.dumps(x['window_geometry'], sort_keys=True)
                                      for x in rows if 'window_geometry' in x})})
    (out / 'heartbeat-sessions.json').write_text(json.dumps(sessions, indent=2) + '\n')
    assert binding['result'] == physical['result'] == 'PASS'
    print(json.dumps({'binding': binding, 'decoded_outputs': len(media),
                      'source_inputs_unchanged': all(x['unchanged'] for x in inputs)}, indent=2))


if __name__ == '__main__':
    main()
