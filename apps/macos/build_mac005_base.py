#!/usr/bin/env python3
"""Build a new macOS arm64 development app without touching older evidence."""
import argparse
import os
import plistlib
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(args):
    subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True, help='new .app bundle path')
    parser.add_argument('--work-dir', type=Path, required=True, help='new build directory')
    parser.add_argument('--self-tests', action='store_true', help='include native self-test entry points')
    args = parser.parse_args()
    output = args.output.absolute()
    work = args.work_dir.absolute()
    if output.exists() or work.exists():
        parser.error('output and work directory must not already exist')
    if output.suffix != '.app':
        parser.error('output must end in .app')
    work.mkdir(parents=True)
    (output / 'Contents/MacOS').mkdir(parents=True)
    frameworks = output / 'Contents/Frameworks'
    frameworks.mkdir()
    target = work / 'rust'
    run(['cargo', 'build', '--offline', '--locked', '--manifest-path', 'core/Cargo.toml',
         '--target-dir', target, '-p', 'media-ffi'])
    ffi = frameworks / 'libmedia_ffi.dylib'
    shutil.copy2(target / 'debug/libmedia_ffi.dylib', ffi)
    run(['install_name_tool', '-id', '@rpath/libmedia_ffi.dylib', ffi])
    run(['codesign', '--force', '--sign', '-', '--timestamp=none', ffi])
    sdk = subprocess.check_output(['xcrun', '--sdk', 'macosx', '--show-sdk-path'], text=True).strip()
    sources = sorted((ROOT / 'apps/macos/Sources').glob('*.swift'))
    if not args.self_tests:
        sources = [source for source in sources if not source.name.endswith('SelfTest.swift')]
    binary = output / 'Contents/MacOS/MediaStudio'
    run(['xcrun', 'swiftc', '-swift-version', '6', '-strict-concurrency=complete', '-warnings-as-errors',
         *([] if args.self_tests else ['-D', 'MEDIASTUDIO_PRODUCTION']),
         '-parse-as-library', '-target', 'arm64-apple-macosx27.0', '-sdk', sdk,
         '-module-cache-path', work / 'swift-cache', '-import-objc-header', ROOT / 'contracts/media_core_v0.h',
         '-L', frameworks, '-lmedia_ffi', '-Xlinker', '-rpath', '-Xlinker', '@executable_path/../Frameworks',
         *sources, '-o', binary])
    info = {'CFBundleIdentifier': os.environ.get('MEDIASTUDIO_BUNDLE_ID', 'local.mediastudio.mac005dev'),
            'CFBundleName': 'ClipFlow', 'CFBundleExecutable': 'MediaStudio',
            'CFBundleIconFile': 'ClipFlow.icns',
            'CFBundlePackageType': 'APPL', 'CFBundleShortVersionString': '0.1.0', 'CFBundleVersion': '1',
            'LSMinimumSystemVersion': '27.0', 'NSHighResolutionCapable': True, 'NSPrincipalClass': 'NSApplication'}
    resources = output / 'Contents/Resources'
    resources.mkdir(exist_ok=True)
    shutil.copy2(ROOT / 'apps/macos/Resources/ClipFlow.icns', resources / 'ClipFlow.icns')
    (output / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
    run(['codesign', '--force', '--sign', '-', '--timestamp=none', output])
    run(['codesign', '--verify', '--deep', '--strict', output])
    print('Base development app:', output)


if __name__ == '__main__':
    main()
