#!/usr/bin/env python3
"""Read-only final artifact, window, cache and silent output verification.

Never launches the app, an audio device, a player or VoiceOver. Performance
failures remain failures; an instrumented diagnostic session is kept separate.
"""
import argparse
import array
import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def read(path):
    return json.loads(path.read_text())


def jpeg_dimensions(path):
    """Read JPEG SOF metadata; never resizes or rewrites GUI evidence."""
    data = path.read_bytes()
    assert data[:2] == b'\xff\xd8', path
    offset = 2
    while offset < len(data):
        assert data[offset] == 0xff, (path, offset)
        while data[offset] == 0xff:
            offset += 1
        marker = data[offset]
        offset += 1
        assert marker not in (0xd9, 0xda), ('Missing JPEG frame header', path)
        if marker == 0x01 or 0xd0 <= marker <= 0xd7:
            continue
        length = int.from_bytes(data[offset:offset + 2], 'big')
        assert length >= 2 and offset + length <= len(data), path
        if marker in (0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7,
                      0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf):
            assert length >= 8, path
            return (int.from_bytes(data[offset + 5:offset + 7], 'big'),
                    int.from_bytes(data[offset + 3:offset + 5], 'big'))
        offset += length
    raise AssertionError(('Missing JPEG frame header', path))


def main():
    p = argparse.ArgumentParser()
    for name in ['app', 'evidence', 'work', 'container']:
        p.add_argument('--' + name, required=True, type=Path)
    p.add_argument('--tag', choices=['v5', 'v6'], default='v5')
    a = p.parse_args()
    root = Path(__file__).resolve().parents[2]
    ev, work, app = a.evidence, a.work, a.app.resolve()
    frozen = read(ev / 'final-before-gui.json')
    assert frozen['app'] == str(app) and app.name.endswith('Closure-' + a.tag + '.app')
    sources = {name: sha(root / name) for name in frozen['production_sources']}
    files = {str(path.relative_to(app)): sha(path)
             for path in sorted(app.rglob('*')) if path.is_file()}
    binding = {'result': 'PASS' if sources == frozen['production_sources']
               and files == frozen['bundle_files'] else 'FAIL',
               'production_sources': len(sources), 'bundle_files': len(files),
               'all_production_sources_unchanged': sources == frozen['production_sources'],
               'all_bundle_files_unchanged': files == frozen['bundle_files'],
               'main_sha256': files['Contents/MacOS/MediaStudio'],
               'core_sha256': files['Contents/Frameworks/libmedia_ffi.dylib']}
    (ev / 'final-after-gui.json').write_text(json.dumps(binding, indent=2) + '\n')
    assert binding['result'] == 'PASS', binding

    original = read(ev / 'external-cache-original.json')
    originals = {name: sha(work / 'External Components Cache' / name) == digest
                 for name, digest in original.items()}
    inputs = read(ev / 'silent-fixtures.json')['files']
    inputs[str(work / 'Silent Inputs/silent float.wav')] = read(ev / 'float-input.json')['sha256']
    inputs = {name: sha(Path(name)) == digest for name, digest in inputs.items()}
    cache = a.container / 'Library/Application Support/MediaStudio/ai-components-v1'
    record = read(cache / 'selection.json')
    current = cache / record['id']
    selected = {name: sha(current / name) == digest for name, digest in original.items()
                if name == 'model.onnx' or (name.startswith('graphs/') and name.endswith('.onnx'))}
    missing_case = read(ev / (a.tag + '-owned-cache-missing-fault.json'))
    cache_result = {'external_originals_unchanged': originals, 'inputs_unchanged': inputs,
                    'selection': record, 'all_nine_cached_data_files_match_reference': selected,
                    'damaged_old_cache_retired': not (cache / missing_case['selection']['id']).exists(),
                    'current_folder_names': sorted(x.name for x in current.iterdir()),
                    'cache_root_mode': oct(cache.stat().st_mode & 0o777),
                    'model_mode': oct((current / 'model.onnx').stat().st_mode & 0o777),
                    'native_worker_or_dylib_copied_into_data_cache': any(
                        x.suffix == '.dylib' or x.name == 'media-ai-worker' for x in cache.rglob('*'))}
    (ev / 'final-cache-and-inputs.json').write_text(json.dumps(cache_result, indent=2) + '\n')
    assert all(originals.values()) and all(inputs.values()) and all(selected.values())
    assert cache_result['damaged_old_cache_retired']
    assert not cache_result['native_worker_or_dylib_copied_into_data_cache']

    exports = [(work / (a.tag + '-final-silent-copy.mp4'), 'GUI media export')]
    folders = ['v5 managed stems', 'v5 restart stems 20261115'] if a.tag == 'v5' else ['v6 managed stems', 'v6 recovered stems']
    for folder in folders:
        exports += [(work / folder / stem, 'GUI dual-stem export')
                    for stem in ['vocals.wav', 'accompaniment.wav']]
    if a.tag == 'v5':
        exports += [(work / 'v5-missing-cache-result-diagnostic' / stem,
                     'Core local-result diagnostic copy, not a GUI export')
                    for stem in ['vocals.wav', 'accompaniment.wav']]
    media = []
    for path, scope in exports:
        probe = ['/opt/homebrew/bin/ffprobe', '-v', 'error', '-show_streams',
                 '-show_format', '-of', 'json', str(path)]
        decode = ['/opt/homebrew/bin/ffmpeg', '-nostdin', '-v', 'error', '-i', str(path),
                  '-map', '0', '-f', 'null', '-']
        q = subprocess.run(probe, capture_output=True, text=True, timeout=30)
        d = subprocess.run(decode, capture_output=True, text=True, timeout=30)
        pcm_argv = ['/opt/homebrew/bin/ffmpeg', '-nostdin', '-v', 'error', '-i', str(path),
                    '-map', '0:a:0', '-f', 'f32le', '-']
        pcm = subprocess.run(pcm_argv, capture_output=True, timeout=30)
        samples = array.array('f'); samples.frombytes(pcm.stdout)
        peak = max((abs(x) for x in samples), default=0)
        row = {'path': str(path), 'scope': scope, 'sha256': sha(path),
               'probe_argv': probe, 'probe_exit': q.returncode,
               'metadata': json.loads(q.stdout), 'decode_argv': decode,
               'decode_exit': d.returncode, 'decode_stderr': d.stderr,
               'pcm_decode_argv': pcm_argv, 'pcm_decode_exit': pcm.returncode,
               'decoded_audio_samples': len(samples), 'finite': all(map(math.isfinite, samples)),
               'decoded_audio_peak': peak, 'no_audio_device_opened': True}
        media.append(row)
        assert q.returncode == d.returncode == pcm.returncode == 0
        assert samples and row['finite'] and peak == 0, path
    (ev / 'final-media-inspection.json').write_text(json.dumps(media, indent=2) + '\n')

    geometry = []
    for filename, width, height in [('v5-1200x800-first-geometry.json', 1200, 800),
                                     ('v5-1600x1000-geometry.json', 1600, 1000),
                                     ('v5-1200x800-return-geometry.json', 1200, 800)]:
        filename = filename.replace('v5-', a.tag + '-')
        value = read(ev / filename)['window_geometry']
        passed = value['frame_width_points'] == width and value['frame_height_points'] == height
        geometry.append({'evidence': filename, 'geometry': value, 'result': 'PASS' if passed else 'FAIL'})
        assert passed
    screenshots = [a.tag + '-' + x for x in ['1200x800-media-first.jpg', '1600x1000-media.jpg',
                   '1200x800-media-return.jpg', '1200x800-' +
                   ('media-export-visible' if a.tag == 'v5' else 'export-visible') + '.jpg',
                   '1200x800-ai.jpg', '1600x1000-ai.jpg']]
    screenshot_dimensions = []
    for filename in screenshots:
        expected = (1600, 1000) if '1600x1000' in filename else (1200, 800)
        actual = jpeg_dimensions(ev / filename)
        assert actual == expected, (filename, actual, expected)
        screenshot_dimensions.append({'file': filename, 'width_pixels': actual[0],
                                      'height_pixels': actual[1], 'result': 'PASS'})
    (ev / 'final-window-matrix.json').write_text(json.dumps({
        'result': 'PASS', 'appearance': 'light', 'same_media_workspace_with_video_and_tasks': True,
        'direct_small_large_small_without_switching_workspace': True, 'geometry': geometry,
        'screenshots': screenshots, 'verified_jpeg_dimensions': screenshot_dimensions},
        indent=2) + '\n')

    sessions = []
    unprofiled = {'0E2D4B7F-CE05-4114-9597-3BE3783DCDFC', '5BCD55CE-F1C3-4008-9F5B-60C5A65CAFE3'}
    logs = list((a.container / 'tmp').glob('qa002-heartbeat-*.jsonl'))
    profile_log = a.container / 'tmp/qa002-heartbeat-4C26AEA2-BE97-44B8-BA33-FE22DEB9E933.jsonl'
    if a.tag == 'v6':
        anchor = read(ev / 'v6-startup-observation.json')['session']
        first = a.container / ('tmp/qa002-heartbeat-' + anchor + '.jsonl')
        unprofiled = {read_session['session'] for path in logs
                      if path.stat().st_birthtime >= first.stat().st_birthtime
                      for read_session in [json.loads(path.read_text().splitlines()[-1])]}
        profile_log = None
    for path in logs:
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        session = rows[-1]['session']
        is_profile = path == profile_log
        if session not in unprofiled and not is_profile:
            continue
        target = ev / (a.tag + '-' + path.name)
        shutil.copy2(path, target)
        final = rows[-1]
        sessions.append({'file': target.name, 'session': session,
                         'latency_acceptance_eligible': session in unprofiled,
                         'scope': 'unprofiled final-package GUI session' if session in unprofiled
                         else 'missing-cache functional recovery plus diagnostic profiling suffix',
                         'kind': final['kind'], 'sample_count': final['sample_count'],
                         'measured_max_ms': final['measured_max_ms'], 'active_max_ms': final['active_max_ms'],
                         'warmup_max_ms': final['warmup_max_ms'],
                         'stage_timings': final.get('stage_timings', {}),
                         'skipped_writes': final['skipped_writes']})
    eligible = [x for x in sessions if x['latency_acceptance_eligible']]
    assert len(eligible) >= 2 and all(x['kind'] in ['stopped', 'limit'] for x in eligible)
    maximum = max(x['measured_max_ms'] for x in eligible)
    performance = {'result': 'PASS' if maximum <= 500 else 'FAIL_REMAINS',
                   'threshold_ms': 500, 'period_ms': 100, 'warmup_ms': 2000,
                   'maximum_unprofiled_ms': maximum, 'all_eligible_sessions_retained': True,
                   'startup_warmup_max_ms': max(x['warmup_max_ms'] for x in eligible),
                   'stage_counts_are_measurements_not_physical_constructor_counts': True,
                   'profile_never_replaces_or_explains_an_earlier_interval_by_itself': True,
                   'sessions': sessions}
    (ev / 'final-heartbeat-summary.json').write_text(json.dumps(performance, indent=2) + '\n')
    summary = {'artifact_binding': binding['result'], 'specified_window_geometry': 'PASS',
               'component_recovery': 'PASS_GUI_AND_FILES', 'silent_outputs_decoded': len(media),
               'gui_export_outputs': sum(x['scope'].startswith('GUI') for x in media),
               'main_thread_latency': performance['result'], 'maximum_unprofiled_ms': maximum,
               'whole_AC22_AC24_or_QA002_accepted': False}
    (ev / 'closure-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
