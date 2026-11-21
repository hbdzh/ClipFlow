#!/usr/bin/env python3
"""Repeat final Core/FFI physical media checks without any sound device or GUI.

Uses self-authored fixtures and signature-only copies of the candidate helpers.
Does not call app selftests, AVPlayer.play, VoiceOver or a media player.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    for name in ('app', 'work', 'evidence'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--group', choices=['physical', 'boundaries'], required=True)
    args = parser.parse_args()
    app, work, evidence = args.app.resolve(), args.work.resolve(), args.evidence.resolve()
    work.mkdir(parents=True, exist_ok=True)
    evidence.mkdir(parents=True, exist_ok=True)
    library = app / 'Contents/Frameworks/libmedia_ffi.dylib'
    frozen = {str(p.relative_to(app)): sha(p) for p in sorted(app.rglob('*')) if p.is_file()}
    results = []

    def run(name, argv):
        # A parent resource measurement may request a pause between heavy steps.
        if (work.parent / 'pause-regression').exists():
            raise RuntimeError('Paused before ' + name + ' for independent resource measurement')
        argv = list(map(str, argv))
        started = time.monotonic()
        environment = dict(os.environ)
        if args.group == 'boundaries':
            environment['PATH'] = str(work / 'tools') + os.pathsep + environment['PATH']
        with (evidence / ('regression-' + name + '.log')).open('x') as stream:
            result = subprocess.run(argv, cwd=ROOT, stdin=subprocess.DEVNULL,
                                    stdout=stream, stderr=subprocess.STDOUT, env=environment)
        record = {'name': name, 'argv': argv, 'exit': result.returncode,
                  'elapsed_seconds': time.monotonic() - started,
                  'backend_PATH_prefix': str(work / 'tools') if args.group == 'boundaries' else None}
        results.append(record)
        report = {'scope': 'Actual current signed Core C ABI and independent physical media; '
                  'standalone code/data-identical helpers with test-only signatures; no GUI/playback claim',
                  'group': args.group, 'app': str(app), 'core_sha256': sha(library),
                  'bundle_sha256': frozen, 'commands': results,
                  'bundle_unchanged': frozen == {str(p.relative_to(app)): sha(p)
                      for p in sorted(app.rglob('*')) if p.is_file()},
                  'result': 'PASS' if all(x['exit'] == 0 for x in results) else 'FAIL'}
        (evidence / ('regression-' + args.group + '-commands.json')).write_text(
            json.dumps(report, indent=2) + '\n')
        print(json.dumps(record), flush=True)
        return result.returncode == 0

    py = [sys.executable, '-B']
    if args.group == 'physical':
        tools = work / 'tools'
        if not run('helper-copies', [*py, 'tests/qa_002/media_matrix_prepare_tools.py',
                   '--app', app, '--work', tools, '--report', evidence / 'regression-helper-identity.json']):
            return 1
        fixtures = work / 'fixtures'
        fixtures.mkdir(exist_ok=False)
        sys.path.insert(0, str(ROOT / 'tests/core_004'))
        from support import Commands
        spec = importlib.util.spec_from_file_location('release_fixtures', ROOT / 'tests/core_003/fixtures.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        commands = Commands()
        files = module.make_fixtures(fixtures, commands)
        manifest = {'files': {k: str(v) for k, v in files.items()},
                    'sha256': {k: sha(v) for k, v in files.items()}, 'commands': commands.records,
                    'fixture_generator_sha256': sha(ROOT / 'tests/core_003/fixtures.py'),
                    'scope': 'Self-authored seeded synthetic files; audio is encoded/decoded only, never played'}
        (fixtures / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        (evidence / 'regression-fixtures.json').write_text(json.dumps(manifest, indent=2) + '\n')
        run('abi', [*py, 'tests/core_001/run_qa.py', '--library', library,
                    '--output', evidence / 'regression-abi.json'])
        run('media', [*py, 'tests/qa_002/media_matrix_core.py', '--library', library,
                    '--ffmpeg', tools / 'ffmpeg', '--ffprobe', tools / 'ffprobe',
                    '--work', work / 'media', '--evidence', evidence / 'regression-media', '--fixtures', fixtures])
        run('audio-probe', [*py, 'tests/qa_002/media_matrix_audio_probe.py', '--library', library,
                    '--work', work / 'audio', '--evidence', evidence / 'regression-audio.json', '--tools', tools])
        run('pcm-proxy', [*py, 'tests/qa_002/media_matrix_proxy.py', '--library', library,
                    '--work', work / 'proxy', '--evidence', evidence / 'regression-proxy.json',
                    '--tools', tools, '--fixtures', work / 'audio'])
    else:
        source = work / 'fixtures/cfr.mp4'
        assert source.is_file() and all((work / 'tools' / name).is_file()
                                       for name in ('ffmpeg', 'ffprobe')), 'Run physical group first'
        run('queue', [*py, 'tests/qa_002/test_queue_lifecycle.py', '--library', library,
                    '--source', source, '--work-dir', work / 'queue', '--report', evidence / 'regression-queue.json'])
        run('file-safety', [*py, 'tests/qa_002/test_file_safety.py', '--library', library,
                    '--source', source, '--work-dir', work / 'file-safety', '--report', evidence / 'regression-file-safety.json'])
    return 0 if all(x['exit'] == 0 for x in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
