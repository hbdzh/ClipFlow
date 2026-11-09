#!/usr/bin/env python3
"""Build a host-only MAC-001 app bundle without external build dependencies."""
from pathlib import Path
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import stat

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BUILD = HERE / 'build'
APP = BUILD / 'MediaStudioSpike.app'
RECORD = HERE / 'evidence' / 'build.json'


class UnsafeBuildPath(ValueError):
    pass


def assert_safe_output(anchor, target, *, allow_hardlinks=False):
    """Reject existing aliases/special files before any task-owned path write.

    This guards pre-existing filesystem state; it is not a promise against a
    hostile process racing directory replacement during the build.
    """
    anchor = Path(anchor).resolve(strict=True)
    target = Path(target).absolute()
    try:
        parts = target.relative_to(anchor).parts
    except ValueError as error:
        raise UnsafeBuildPath('Output is outside the task root') from error
    current = anchor
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise UnsafeBuildPath('Output path contains a symlink: ' + str(current))
        if current.exists():
            info = current.stat(follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                continue
            if current != target or not stat.S_ISREG(info.st_mode) or (info.st_nlink != 1 and not allow_hardlinks):
                raise UnsafeBuildPath('Output contains a special/aliased file: ' + str(current))
    if not target.resolve(strict=False).is_relative_to(anchor):
        raise UnsafeBuildPath('Resolved output escaped the task root')


def assert_safe_tree(anchor, tree):
    assert_safe_output(anchor, tree)
    linked_files = {}
    if tree.exists():
        for directory, dirs, files in os.walk(tree, followlinks=False):
            for name in dirs + files:
                child = Path(directory) / name
                assert_safe_output(anchor, child, allow_hardlinks=True)
                info = child.stat(follow_symlinks=False)
                if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
                    key = (info.st_dev, info.st_ino)
                    links, count = linked_files.get(key, (info.st_nlink, 0))
                    linked_files[key] = (links, count + 1)
    # Cargo legitimately hardlinks incremental objects inside its target tree.
    # Accept only groups whose complete link count is contained in this tree;
    # an external hardlink must not turn a later write into a tree escape.
    if any(links != count for links, count in linked_files.values()):
        raise UnsafeBuildPath('Build tree has a hardlink outside its tree')


def check_layout():
    for path in [BUILD, APP, RECORD.parent, RECORD]:
        assert_safe_output(HERE, path)
    assert_safe_tree(HERE, BUILD)
    assert_safe_tree(HERE, RECORD.parent)


def safe_mkdir(path):
    assert_safe_output(HERE, path)
    path.mkdir(parents=True, exist_ok=True)


def write_record(data):
    assert_safe_output(HERE, RECORD)
    RECORD.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def main():
    check_layout()
    safe_mkdir(BUILD)
    safe_mkdir(RECORD.parent)
    commands = []

    def run(argv):
        result = subprocess.run(list(map(str, argv)), cwd=ROOT, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=240)
        commands.append({'argv': list(map(str, argv)), 'returncode': result.returncode, 'output': result.stdout})
        print(result.stdout, end='', flush=True)
        if result.returncode:
            write_record({'result': 'FAIL', 'commands': commands})
            raise SystemExit(result.returncode)
        return result.stdout.strip()

    run(['rustc', '-Vv'])
    rust_target = BUILD / 'RustTarget'
    assert_safe_output(HERE, rust_target)
    run(['cargo', 'build', '--manifest-path', 'core/Cargo.toml', '--target-dir', rust_target, '--offline', '--locked'])
    assert_safe_tree(HERE, BUILD)
    sdk = run(['xcrun', '--sdk', 'macosx', '--show-sdk-path'])
    swiftc = run(['xcrun', '--find', 'swiftc'])
    run([swiftc, '--version'])
    binary = APP / 'Contents/MacOS/MediaStudioSpike'
    frameworks = APP / 'Contents/Frameworks'
    safe_mkdir(binary.parent)
    safe_mkdir(frameworks)
    source = rust_target / 'debug/libmedia_ffi.dylib'
    library = frameworks / source.name
    assert_safe_output(HERE, library)
    shutil.copyfile(source, library)
    run(['install_name_tool', '-id', '@rpath/libmedia_ffi.dylib', library])
    run(['codesign', '--force', '--sign', '-', '--timestamp=none', library])
    header = ROOT / 'contracts/media_core_v0.h'
    args = [swiftc, '-swift-version', '6', '-strict-concurrency=complete', '-warnings-as-errors',
            '-parse-as-library', '-target', 'arm64-apple-macosx27.0', '-sdk', sdk,
            '-module-cache-path', BUILD / 'ModuleCache', '-import-objc-header', header,
            '-L', frameworks, '-lmedia_ffi', '-Xlinker', '-rpath', '-Xlinker', '@executable_path/../Frameworks',
            *sorted((HERE / 'Sources').glob('*.swift')), '-o', binary]
    assert_safe_output(HERE, binary)
    assert_safe_output(HERE, BUILD / 'ModuleCache')
    run(args)
    info = {'CFBundleIdentifier': 'local.mediastudio.mac001', 'CFBundleName': 'MediaStudioSpike',
            'CFBundleExecutable': 'MediaStudioSpike', 'CFBundlePackageType': 'APPL',
            'CFBundleShortVersionString': '0.1.0', 'CFBundleVersion': '1',
            'LSMinimumSystemVersion': '27.0', 'NSHighResolutionCapable': True,
            'NSPrincipalClass': 'NSApplication'}
    info_path = APP / 'Contents/Info.plist'
    assert_safe_output(HERE, info_path)
    info_path.write_bytes(plistlib.dumps(info))
    assert_safe_tree(HERE, APP)
    run(['codesign', '--force', '--sign', '-', '--timestamp=none', APP])
    for argv in [['file', binary, library], ['lipo', '-info', binary], ['otool', '-L', binary],
                 ['codesign', '--verify', '--strict', '--verbose=2', APP],
                 ['codesign', '-dv', '--verbose=2', APP]]:
        run(argv)
    artifacts = [{'path': str(p.relative_to(ROOT)), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
                  'bytes': p.stat().st_size, 'committed': False} for p in [source, library, binary]]
    write_record({'result': 'PASS', 'scope': 'host arm64-macosx27.0 spike, not product minimum OS or distribution',
                                 'signing': 'ad-hoc only; no identity/certificate/notarization',
                                 'canonical_header_sha256': hashlib.sha256(header.read_bytes()).hexdigest(),
                                 'commands': commands, 'artifacts': artifacts})
    print('Built:', APP)


if __name__ == '__main__':
    main()
