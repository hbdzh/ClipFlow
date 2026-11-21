#!/usr/bin/env python3
"""Assemble a local ad-hoc development app with a native AI worker.

The Python interpreter is used only to assemble the development bundle. It is
not copied into the application or used by the bundled AI worker.
"""
import argparse
import plistlib
import shutil
import subprocess
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-app', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--worker', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--graphs', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--ffmpeg', type=Path)
    parser.add_argument('--ffprobe', type=Path)
    parser.add_argument('--licenses', type=Path)
    parser.add_argument('--sandbox', action='store_true')
    args = parser.parse_args()

    if (args.ffmpeg is None) != (args.ffprobe is None):
        parser.error('--ffmpeg and --ffprobe must be provided together')

    # Never overwrite an existing application bundle.
    shutil.copytree(args.base_app, args.output)
    contents = args.output / 'Contents'
    macos = contents / 'MacOS'
    frameworks = contents / 'Frameworks'
    resources = contents / 'Resources' / 'ai'
    frameworks.mkdir(parents=True, exist_ok=True)
    resources.mkdir(parents=True, exist_ok=True)
    worker = macos / 'media-ai-worker'
    runtime = frameworks / 'libonnxruntime.dylib'
    shutil.copy2(args.worker, worker)
    shutil.copy2(args.runtime, runtime)
    shutil.copy2(args.model, resources / 'model.onnx')
    shutil.copytree(args.graphs, resources / 'graphs', dirs_exist_ok=True)
    if args.licenses is not None:
        shutil.copytree(args.licenses, contents / 'Resources' / 'licenses', dirs_exist_ok=True)
    tools = []
    if args.ffmpeg is not None:
        for source, name in [(args.ffmpeg, 'ffmpeg'), (args.ffprobe, 'ffprobe')]:
            destination = macos / name
            shutil.copy2(source, destination)
            tools.append(destination)
    sign = ['codesign', '--force', '--sign', '-', '--timestamp=none']
    subprocess.run([*sign, str(runtime)], check=True)
    with tempfile.TemporaryDirectory() as temp:
        helper_args = []
        app_args = []
        if args.sandbox:
            helper = Path(temp) / 'helper.plist'
            helper.write_bytes(plistlib.dumps({'com.apple.security.app-sandbox': True,
                                               'com.apple.security.inherit': True}))
            app = Path(temp) / 'app.plist'
            app.write_bytes(plistlib.dumps({'com.apple.security.app-sandbox': True,
                                            'com.apple.security.files.user-selected.read-write': True,
                                            'com.apple.security.files.bookmarks.app-scope': True}))
            helper_args = ['--entitlements', str(helper)]
            app_args = ['--entitlements', str(app)]
        for executable in [worker, *tools]:
            subprocess.run([*sign, *helper_args, str(executable)], check=True)
        subprocess.run([*sign, *app_args, str(args.output)], check=True)
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(args.output)], check=True)
    print('Local development app assembled:', args.output)


if __name__ == '__main__':
    main()
