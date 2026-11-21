#!/usr/bin/env python3
"""Prepare a native sandbox-inheriting FFmpeg gate and watch an isolated GUI export.

The watcher never launches the application or changes its bundle/entitlements.
It refuses host filesystems and volumes outside 200..400 MiB. Only its exclusive
filler and an already-created, inode-checked gate byte are modified.
"""
import argparse
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import time
import traceback
import uuid

GATE_NAME = '.qa002-full-disk-gate'
MIB = 1024 * 1024


def require(value, message):
    if not value:
        raise RuntimeError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def absolute(path):
    path = Path(path)
    require(path.is_absolute(), 'An absolute path is required')
    return path


def ensure_no_symlink(path):
    for current in (path, *path.parents):
        require(not current.is_symlink(), 'Symlink test paths are not allowed')


def write_new_json(path, data):
    path = absolute(path)
    ensure_no_symlink(path)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def prepare(args):
    source = Path(__file__).with_suffix('.c')
    real = absolute(args.ffmpeg).resolve(strict=True)
    require(real.is_file() and os.access(real, os.X_OK), 'FFmpeg must be an executable regular file')
    prefix = absolute(args.output_prefix)
    ensure_no_symlink(prefix)
    prefix = prefix.resolve(strict=False)
    require(prefix != Path('/'), 'The output prefix must be a dedicated test directory')
    work = absolute(args.work_dir)
    ensure_no_symlink(work)
    work.mkdir(parents=True, exist_ok=False)
    header = work / 'gate_config.h'
    header.write_text('#define REAL_FFMPEG ' + json.dumps(str(real), ensure_ascii=False) + '\n'
                      '#define OUTPUT_PREFIX ' + json.dumps(str(prefix), ensure_ascii=False) + '\n', encoding='utf-8')
    shutil.copy2(source, work / source.name)
    unsigned = work / 'ffmpeg-gate-unsigned'
    signed = work / 'ffmpeg-gate'
    commands = []

    def run(argv):
        value = subprocess.run([str(x) for x in argv], capture_output=True, text=True, timeout=60)
        commands.append({'argv': [str(x) for x in argv], 'returncode': value.returncode,
                         'stdout': value.stdout, 'stderr': value.stderr})
        require(value.returncode == 0, 'Component build/signature command failed: ' + str(argv[0]))
        return value.stdout

    run(['/usr/bin/xcrun', 'clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2',
         '-I', work, work / source.name, '-o', unsigned])
    unsigned_sha = sha(unsigned)
    shutil.copy2(unsigned, signed)
    entitlements = work / 'inherit.plist'
    entitlements.write_bytes(plistlib.dumps({'com.apple.security.app-sandbox': True,
                                            'com.apple.security.inherit': True}))
    run(['/usr/bin/codesign', '--force', '--sign', '-', '--timestamp=none',
         '--entitlements', entitlements, signed])
    run(['/usr/bin/codesign', '--verify', '--strict', signed])
    manifest = {'schema_version': 1, 'created_at_utc': datetime.now(timezone.utc).isoformat(),
                'purpose': 'Only gate a real libx264 encode inside the dedicated output prefix',
                'real_ffmpeg': str(real), 'real_ffmpeg_sha256': sha(real), 'output_prefix': str(prefix),
                'gate': str(signed), 'gate_sha256': sha(signed), 'unsigned_gate': str(unsigned),
                'unsigned_gate_sha256': unsigned_sha, 'c_source_sha256': sha(source),
                'python_source_sha256': sha(__file__),
                'gate_timeout_seconds': 120, 'gate_bytes': 4096, 'commands': commands,
                'standalone_signed_execution': 'NOT_RUN: inherit-only helper must have a sandboxed parent',
                'installation': 'Copy gate into the intended app-specific Application Scripts directory; do not replace app files'}
    write_new_json(work / 'manifest.json', manifest)
    print(json.dumps({'gate': str(signed), 'manifest': str(work / 'manifest.json')}, ensure_ascii=False))
    return 0


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return None


def signature(path):
    value = path.stat(follow_symlinks=False)
    return value.st_dev, value.st_ino


def open_gate(path, expected_device):
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        require(stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and metadata.st_size == 4096,
                'Gate is not the expected private regular file')
        require(metadata.st_dev == expected_device, 'Gate escaped the isolated volume')
        header = os.pread(descriptor, 4096, 0).split(b'\n', 1)[0]
        match = re.fullmatch(rb'0 QA002_FULL_DISK_GATE pid=([1-9][0-9]*)', header)
        require(match is not None, 'Gate header is not recognized')
        return descriptor, (metadata.st_dev, metadata.st_ino), int(match.group(1))
    except BaseException:
        os.close(descriptor)
        raise


def release_gate(descriptor):
    require(os.pwrite(descriptor, b'1', 0) == 1, 'Cannot release existing gate byte')
    os.fsync(descriptor)


def remove_owned(path, identity):
    try:
        if signature(path) != identity:
            return False
        path.unlink()
        return True
    except FileNotFoundError:
        return True


def watch(args):
    report = absolute(args.report)
    ensure_no_symlink(report)
    require(not report.exists(), 'The evidence report must be new')
    mount = absolute(args.mount)
    prefix = absolute(args.output_prefix)
    stop = absolute(args.stop_file)
    for path in (mount, prefix, stop):
        ensure_no_symlink(path)
    mount = mount.resolve(strict=True)
    prefix = prefix.resolve(strict=True)
    require(prefix != mount and mount in prefix.parents and prefix.is_dir(),
            'Output prefix must be a dedicated child directory of the test mount')
    require(not list(prefix.iterdir()), 'Output prefix must start empty')
    require(5 <= args.timeout <= 900, 'Watcher timeout must be 5..900 seconds')
    host = Path('/private/tmp')
    usage = shutil.disk_usage(mount)
    require(os.path.ismount(mount), 'Specified test mount is not mounted')
    device = mount.stat().st_dev
    require(device != host.stat().st_dev, 'Refusing the host filesystem')
    require(200 * MIB <= usage.total <= 400 * MIB, 'Test volume must be 200..400 MiB')
    require(shutil.disk_usage(host).free >= 1024 * MIB, 'Host must retain at least 1 GiB free')
    require(prefix.stat().st_dev == device, 'Output prefix has the wrong filesystem device')
    require(mount not in report.parents and mount not in stop.parents,
            'Report and stop file must be outside the test volume')
    filler = mount / ('qa002-gui-exclusive-fill-' + uuid.uuid4().hex + '.bin')
    filler_identity = None
    gate_descriptor = None
    gate_path = None
    started = time.monotonic()
    deadline = started + args.timeout
    data = {'schema_version': 1, 'status': 'RUNNING', 'created_at_utc': datetime.now(timezone.utc).isoformat(),
            'scope': 'Filesystem/process observation only; the GUI/Core error must be observed independently',
            'fixture_python_sha256': sha(__file__),
            'mount': str(mount), 'output_prefix': str(prefix), 'mount_device': device,
            'host_device': host.stat().st_dev, 'total_bytes': usage.total, 'free_initial_bytes': usage.free,
            'events': []}

    def event(kind, **values):
        data['events'].append({'kind': kind, 'monotonic_seconds': time.monotonic(),
                               'elapsed_seconds': time.monotonic() - started, **values})

    def check_stop():
        require(time.monotonic() < deadline, 'Watcher deadline exceeded')
        require(not stop.exists(), 'Watcher stop file observed')
        require(mount.stat().st_dev == device and prefix.stat().st_dev == device,
                'Test mount identity changed')

    try:
        event('waiting-for-real-encode-gate')
        while gate_path is None:
            check_stop()
            candidates = list(prefix.glob('.mediastudio-*/' + GATE_NAME))
            require(len(candidates) <= 1, 'Multiple encode gates are outside this one-job test')
            if candidates:
                gate_path = candidates[0]
                require(gate_path.parent.parent == prefix, 'Gate is outside the selected output directory')
                require(not gate_path.parent.is_symlink(), 'Staging must not be a symlink')
                require(stat.S_IMODE(gate_path.parent.stat().st_mode) == 0o700,
                        'Staging must retain its private 0700 permissions')
                gate_descriptor, gate_identity, pid = open_gate(gate_path, device)
                data.update(gate_path=str(gate_path), gate_pid=pid, gate_identity=list(gate_identity))
                event('real-encode-gated', pid=pid, pid_alive=pid_alive(pid),
                      staging_directory=str(gate_path.parent), gate_bytes=4096)
                break
            time.sleep(0.02)

        written = 0
        failures = []
        fill_descriptor = os.open(filler, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            metadata = os.fstat(fill_descriptor)
            filler_identity = metadata.st_dev, metadata.st_ino
            require(metadata.st_dev == device and metadata.st_nlink == 1 and stat.S_ISREG(metadata.st_mode),
                    'Filler is not the owned isolated-volume file')
            for size in (1048576, 4096, 512, 1):
                block = b'\xa7' * size
                while True:
                    check_stop()
                    require(written + size <= usage.total, 'Bounded image fill ceiling reached')
                    try:
                        count = os.write(fill_descriptor, block)
                        require(count > 0, 'Zero-byte filler write')
                        written += count
                    except OSError as error:
                        require(error.errno == errno.ENOSPC, 'Filler failed without real ENOSPC')
                        failures.append({'errno': error.errno, 'block_bytes': size, 'written_bytes': written})
                        break
            os.fsync(fill_descriptor)
        finally:
            os.close(fill_descriptor)
        exhausted = shutil.disk_usage(mount)
        require(failures and exhausted.free == 0, 'Isolated volume did not reach zero free bytes')
        event('real-filesystem-enospc', written_bytes=written, free_bytes=exhausted.free,
              write_failures=failures)
        data.update(filler_path=str(filler), filled_bytes=written, free_bytes_at_release=exhausted.free)
        require(signature(gate_path) == gate_identity, 'Gate changed before release')
        release_gate(gate_descriptor)
        event('gate-released-in-place', pid=pid)
        while gate_path.parent.exists() or pid_alive(pid) is True:
            check_stop()
            time.sleep(0.02)
        outputs = list(prefix.glob('*.mp4'))
        require(not outputs, 'An output was published despite the full-volume test')
        require(not list(prefix.glob('.mediastudio-*')), 'Staging remains after the test')
        alive = pid_alive(pid)
        event('staging-gone', pid=pid, pid_alive=alive,
              pid_exit_observed=alive is False, published_outputs=[])
        data['status'] = 'COMPLETE'
        data['observed_real_enospc'] = True
        data['pid_exit_observed'] = alive is False
        data['core_error_code'] = 'NOT_OBSERVED_BY_THIS_CONTROLLER'
    except BaseException as error:
        data['status'] = 'STOPPED' if stop.exists() else 'FAIL'
        data['error'] = str(error)
        data['traceback'] = traceback.format_exc()
    finally:
        if gate_descriptor is not None:
            try:
                release_gate(gate_descriptor)
                event('gate-finally-released')
            except BaseException as error:
                data['status'] = 'FAIL'
                event('gate-finally-release-error', error=str(error), errno=getattr(error, 'errno', None))
            finally:
                try:
                    os.close(gate_descriptor)
                except OSError as error:
                    data['status'] = 'FAIL'
                    event('gate-finally-close-error', errno=error.errno)
        if filler_identity is not None:
            try:
                removed = remove_owned(filler, filler_identity)
            except BaseException as error:
                removed = False
                event('owned-filler-cleanup-error', error=str(error), errno=getattr(error, 'errno', None))
            data['owned_filler_removed'] = removed
            if not removed:
                data['status'] = 'FAIL'
            event('owned-filler-cleanup', removed=removed)
        try:
            require(mount.stat().st_dev == device, 'Test mount changed during cleanup')
            data['free_bytes_after_cleanup'] = shutil.disk_usage(mount).free
        except BaseException as error:
            data['status'] = 'FAIL'
            event('post-cleanup-volume-check-error', error=str(error))
        data['volume_unmounted_by_controller'] = False
        write_new_json(report, data)
    print(json.dumps({'status': data['status'], 'report': str(report)}, ensure_ascii=False))
    return 0 if data['status'] == 'COMPLETE' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    build = commands.add_parser('prepare')
    build.add_argument('--ffmpeg', required=True)
    build.add_argument('--output-prefix', required=True)
    build.add_argument('--work-dir', required=True)
    observe = commands.add_parser('watch')
    observe.add_argument('--mount', required=True)
    observe.add_argument('--output-prefix', required=True)
    observe.add_argument('--report', required=True)
    observe.add_argument('--stop-file', required=True)
    observe.add_argument('--timeout', type=float, default=300)
    args = parser.parse_args()
    return prepare(args) if args.command == 'prepare' else watch(args)


if __name__ == '__main__':
    raise SystemExit(main())
