#!/usr/bin/env python3
"""Assemble a new local candidate; retain the preceding evidence bundle intact."""
import argparse
import json
import plistlib
import shutil
import subprocess
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--built-base', type=Path, required=True)
    parser.add_argument('--previous', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--core-library', type=Path, help='final independently built Core, same ABI')
    parser.add_argument('--bundle-id', default='local.mediastudio.qa002.media')
    parser.add_argument('--preserve-core', action='store_true')
    args = parser.parse_args()
    if args.output.exists() or args.output.suffix != '.app':
        parser.error('output must be a new .app path')
    shutil.copytree(args.previous, args.output)
    for relative in ['Contents/MacOS/MediaStudio', 'Contents/Frameworks/libmedia_ffi.dylib']:
        if args.preserve_core and relative.endswith('libmedia_ffi.dylib'):
            continue
        shutil.copy2(args.built_base / relative, args.output / relative)
    if args.core_library is not None:
        library = args.output / 'Contents/Frameworks/libmedia_ffi.dylib'
        shutil.copy2(args.core_library, library)
        subprocess.run(['install_name_tool', '-id', '@rpath/libmedia_ffi.dylib', str(library)], check=True)
        subprocess.run(['codesign', '--force', '--sign', '-', '--timestamp=none', str(library)], check=True)
    info_path = args.output / 'Contents/Info.plist'
    info = plistlib.loads(info_path.read_bytes())
    info['CFBundleIdentifier'] = args.bundle_id
    info_path.write_bytes(plistlib.dumps(info))
    entitlements = {'com.apple.security.app-sandbox': True,
                    'com.apple.security.files.user-selected.read-write': True,
                    'com.apple.security.files.bookmarks.app-scope': True}
    commands = []
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'app.plist'
        path.write_bytes(plistlib.dumps(entitlements))
        for command in [
            ['codesign', '--force', '--sign', '-', '--timestamp=none', '--entitlements', str(path), str(args.output)],
            ['codesign', '--verify', '--deep', '--strict', str(args.output)],
        ]:
            subprocess.run(command, check=True)
            commands.append(command)
    print(json.dumps({'app': str(args.output.absolute()), 'commands': commands,
                      'helpers_models_runtime_preserved': True}, indent=2))


if __name__ == '__main__':
    main()
