#!/usr/bin/env python3
"""AC15/AC20 bounded real C ABI checks against an existing candidate library.

Only synthesized/copy fixtures and a separately attached <=400 MiB image are
modified. Host FFmpeg is independent of the candidate's bundled GUI tools.
"""
import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import time
import traceback

sys.dont_write_bytecode = True
if not __debug__:
    raise SystemExit('Run without -O: safety bounds and test assertions are required.')
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests/core_004'))
from support import API, configuration, control, TERMINAL
from fault_tools import holding_encoder, pause_before_encoder, wait_event, pid_alive


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def trim(source, target, precise=False, end=2600000):
    return {'schema_version': 0, 'operation': 'trim', 'source': {'uri': source.as_uri()},
            'destination': {'uri': target.as_uri()}, 'range': {'start_us': 1100000, 'end_us': end},
            'strategy': 'accurate_reencode' if precise else 'stream_copy',
            'profile': 'mp4_h264_standard_v1' if precise else 'mp4_h264_copy_v1',
            'stream_selection': 'all_supported', 'allow_reencode_fallback': False}


def external_reference(data, url):
    """Turn self-contained MOV data entries into ordinary external references.

    The synthesized source has its moov after mdat: chunk offsets stay valid.
    Only url entries and enclosing atom sizes change; no decoder payload edits.
    """
    def rebuild(blob):
        result = bytearray()
        cursor = 0
        while cursor < len(blob):
            size, kind = struct.unpack('>I4s', blob[cursor:cursor+8])
            if size < 8 or cursor + size > len(blob):
                raise ValueError('Unsupported fixture box layout')
            body = blob[cursor+8:cursor+size]
            if kind in (b'moov', b'trak', b'mdia', b'minf', b'dinf'):
                body = rebuild(body)
            elif kind == b'dref':
                body = body[:8] + rebuild(body[8:])
            elif kind == b'url ':
                body = b'\0\0\0\0' + url.encode() + b'\0'
            result += struct.pack('>I4s', len(body)+8, kind) + body
            cursor += size
        return bytes(result)
    assert data.index(b'mdat') < data.index(b'moov')
    return rebuild(data)


class Suite:
    def __init__(self, args):
        self.args = args
        self.root = args.work_dir.resolve()
        self.root.mkdir(parents=True, exist_ok=False)
        self.source = self.root / 'source.mp4'
        shutil.copy2(args.source, self.source)
        self.source_hash = sha(self.source)
        self.api = API(args.library)
        self.checks = []
        self.commands = []
        self.counter = 0

    def run(self, argv):
        r = subprocess.run([str(a) for a in argv], capture_output=True, text=True, timeout=60)
        self.commands.append({'argv': [str(a) for a in argv], 'returncode': r.returncode,
                              'stdout': r.stdout, 'stderr': r.stderr})
        assert r.returncode == 0, self.commands[-1]
        return r.stdout

    def submit(self, engine, request):
        self.counter += 1
        rc, receipt = engine.submit(control('batch_submit', submission_id=f'safety_{self.counter}', items=[request]))
        assert rc == 0, (rc, receipt)
        return receipt

    def close(self, engine):
        for job in engine.jobs:
            value = engine.snapshot(job)
            if value['state'] not in TERMINAL:
                engine.cancel(job)
                engine.wait(job, timeout=20)
        assert engine.destroy() == 0

    def resources(self, engine):
        rc, value = engine.submit(control('queue_snapshot'))
        assert rc == 0 and not value['cleanup_unconfirmed'], value
        assert value['active_count'] == 0, value
        for key in ('global_reserved_storage_bytes', 'global_reserved_target_count',
                    'global_estimated_retained_plan_bytes'):
            assert value['resources'][key] == 0, value
        return value['resources']

    def successful(self, engine, folder, precise=False):
        target = folder / f'recovered-{self.counter}.mp4'
        receipt = self.submit(engine, trim(self.source, target, precise))
        assert receipt['accepted'], receipt
        final = engine.wait(receipt['job_ids'][0], timeout=60)
        assert final['state'] == 'succeeded', final
        probe = json.loads(self.run(['/opt/homebrew/bin/ffprobe', '-v', 'error', '-count_frames',
            '-show_entries', 'stream=codec_type,codec_name,nb_read_frames:format=duration', '-of', 'json', target]))
        assert [(s['codec_type'], s['codec_name']) for s in probe['streams']] == [('video', 'h264'), ('audio', 'aac')]
        assert int(probe['streams'][0]['nb_read_frames']) == (45 if precise else 60), probe
        self.run(['/opt/homebrew/bin/ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-i', target, '-map', '0', '-f', 'null', '-'])
        assert sha(self.source) == self.source_hash
        assert not list(folder.glob('.mediastudio-*'))
        return {'receipt': receipt, 'terminal': final, 'output_sha256': sha(target), 'probe': probe,
                'full_decode_exit': 0, 'resources': self.resources(engine)}

    def check(self, name, fn):
        started = time.monotonic()
        try:
            detail = fn()
            assert sha(self.source) == self.source_hash
            self.checks.append({'name': name, 'status': 'PASS', 'detail': detail})
        except Exception as error:
            self.checks.append({'name': name, 'status': 'FAIL', 'error': str(error), 'traceback': traceback.format_exc()})
        self.checks[-1]['seconds'] = time.monotonic()-started
        print(name, self.checks[-1]['status'], flush=True)

    def rejected_path(self, kind):
        folder = self.root / kind
        folder.mkdir()
        target = folder / 'target.mp4'
        request_source = self.source
        restore = []
        protected = None
        if kind == 'same-source':
            target = self.source
        elif kind == 'existing-file':
            target.write_bytes(b'QA002 KEEP EXISTING TARGET\n'); protected = sha(target)
        elif kind == 'existing-directory':
            target.mkdir(); (target/'keep.txt').write_text('KEEP')
        elif kind == 'symlink':
            target.symlink_to(self.source)
        elif kind == 'dangling-symlink':
            target.symlink_to(folder/'missing.mp4')
        elif kind == 'hardlink':
            os.link(self.source, target)
        elif kind == 'fifo':
            os.mkfifo(target)
        elif kind == 'no-search-parent':
            folder.chmod(0); restore.append((folder, 0o700))
        elif kind == 'nonwritable-parent':
            folder.chmod(0o500); restore.append((folder, 0o700))
        elif kind == 'unreadable-source':
            request_source = folder/'unreadable.mp4'; shutil.copy2(self.source, request_source)
            request_source.chmod(0); restore.append((request_source, 0o600))
        engine = self.api.create(configuration())
        try:
            receipt = self.submit(engine, trim(request_source, target))
            assert not receipt['accepted'] and not receipt.get('job_ids'), receipt
            expected = 'PERMISSION_DENIED' if kind in ('no-search-parent', 'nonwritable-parent', 'unreadable-source') else 'OUTPUT_EXISTS'
            accurate_error = expected in [e['code'] for e in receipt['errors']]
            for path, mode in restore:
                path.chmod(mode)
            restore.clear()
            assert not list(folder.glob('.mediastudio-*'))
            if protected:
                assert sha(target) == protected
            if kind in ('symlink', 'dangling-symlink'):
                assert target.is_symlink()
            if kind == 'existing-directory':
                assert (target/'keep.txt').read_text() == 'KEEP'
            recovery = self.successful(engine, folder)
            assert accurate_error, {'expected': expected, 'receipt': receipt, 'safety_and_recovery_passed': True}
            return {'receipt': receipt, 'expected_error': expected, 'error_category_matches': accurate_error,
                    'safety_and_recovery_passed': True, 'recovery': recovery}
        finally:
            for path, mode in restore:
                path.chmod(mode)
            self.close(engine)

    def invalid_input(self, kind):
        folder = self.root/kind; folder.mkdir()
        request = trim(self.source, folder/'bad-output.mp4')
        if kind in ('corrupt', 'truncated', 'external-playlist', 'external-mov'):
            p = folder/'invalid.mp4'
            if kind == 'corrupt': p.write_bytes(b'QA002 invalid synthetic media')
            elif kind == 'truncated': p.write_bytes(self.source.read_bytes()[:1024])
            elif kind == 'external-playlist': p.write_text('#EXTM3U\n#EXTINF:4,\nhttp://127.0.0.1:9/qa-only.mp4\n')
            else: p.write_bytes(external_reference(self.source.read_bytes(), self.source.as_uri()))
            request['source']['uri'] = p.as_uri()
        elif kind == 'network-uri': request['source']['uri'] = 'https://invalid.example/qa-only.mp4'
        elif kind == 'out-of-range': request['range']['end_us'] = 5000000
        engine = self.api.create(configuration())
        try:
            if kind == 'network-uri':
                rc, receipt = engine.submit(control('batch_submit', submission_id='remote_rejected', items=[request]))
                assert rc == -1 and receipt is None, (rc, receipt)
                rejected = {'abi_status': rc}
            else:
                rejected = self.submit(engine, request)
                assert not rejected['accepted'] and not rejected.get('job_ids'), rejected
                code = ('INVALID_INPUT' if kind == 'out-of-range' else
                        'UNSUPPORTED_FORMAT' if kind == 'external-mov' else 'INVALID_MEDIA')
                assert any(e['code'] == code for e in rejected['errors']), rejected
            assert not (folder/'bad-output.mp4').exists()
            assert not list(folder.glob('.mediastudio-*'))
            return {'rejection': rejected, 'recovery': self.successful(engine, folder)}
        finally:
            self.close(engine)

    def special_path(self):
        folder = self.root/'中文 空格 # % ; $() \' "'; folder.mkdir()
        special = folder/'媒体 $() ; % # \' " 换行\n.mp4'
        shutil.copy2(self.source, special)
        target = folder/'输出 $() ; % # \' " 换行\n.mp4'
        engine = self.api.create(configuration())
        try:
            receipt = self.submit(engine, trim(special, target))
            assert receipt['accepted'], receipt
            final = engine.wait(receipt['job_ids'][0]); assert final['state'] == 'succeeded', final
            self.run(['/opt/homebrew/bin/ffmpeg', '-v', 'error', '-xerror', '-i', target, '-map', '0', '-f', 'null', '-'])
            assert sha(special) == self.source_hash
            assert not list(folder.glob('.mediastudio-*'))
            return {'receipt': receipt, 'terminal': final, 'output_sha256': sha(target),
                    'full_decode_exit': 0, 'source_unchanged': True}
        finally: self.close(engine)

    def target_race(self, kind):
        folder = self.root/f'race-{kind}'; folder.mkdir()
        wrapper, events, release = holding_encoder(folder/'gate')
        engine = self.api.create(configuration(ffmpeg=wrapper))
        target = folder/'race.mp4'
        try:
            receipt = self.submit(engine, trim(self.source, target, precise=True))
            assert receipt['accepted'], receipt
            witness = wait_event(events, 'staging-ready')[0]
            stage = Path(witness['output']); assert stage.stat().st_size > 0
            assert stage.parent.parent == folder and stat.S_IMODE(stage.parent.stat().st_mode) == 0o700
            assert stage.stat().st_dev == folder.stat().st_dev
            witness['staging_parent_mode'] = '0700'
            witness['same_destination_filesystem'] = True
            if kind == 'file': target.write_bytes(b'QA002 COMPETING TARGET KEEP\n')
            elif kind == 'directory': target.mkdir(); (target/'keep.txt').write_text('KEEP')
            elif kind == 'symlink': target.symlink_to(self.source)
            elif kind == 'hardlink': os.link(self.source, target)
            before = sha(target) if target.is_file() else None
            release.touch()
            final = engine.wait(receipt['job_ids'][0])
            assert final['state'] == 'failed' and final['error']['code'] == 'OUTPUT_EXISTS', final
            assert not stage.exists() and not stage.parent.exists() and not pid_alive(witness['pid'])
            if before: assert sha(target) == before
            if kind == 'directory': assert (target/'keep.txt').read_text() == 'KEEP'
            if kind == 'symlink': assert target.is_symlink()
            return {'receipt': receipt, 'witness': witness, 'terminal': final, 'target_unchanged': True,
                    'staging_removed': True, 'recovery': self.successful(engine, folder, precise=True)}
        finally:
            release.touch(exist_ok=True); self.close(engine)

    def real_disk_full(self):
        mount = self.args.mount.resolve(strict=True)
        usage = shutil.disk_usage(mount)
        assert os.stat(mount).st_dev != os.stat(self.root).st_dev
        assert 200*1024**2 < usage.total <= 400*1024**2 and usage.free > 200*1024**2, usage
        assert not list(mount.glob('qa002-*')), 'Refusing to reuse existing test names'
        wrapper, events, release = pause_before_encoder(self.root/'disk-gate')
        engine = self.api.create(configuration(ffmpeg=wrapper))
        target = mount/'qa002-must-not-publish.mp4'
        fill = mount/'qa002-isolated-fill.bin'
        try:
            receipt = self.submit(engine, trim(self.source, target, precise=True))
            assert receipt['accepted'], receipt
            witness = wait_event(events, 'before-real-encode')[0]
            stage = Path(witness['output']); assert stage.parent.is_dir()
            written = 0; failures = []
            with fill.open('xb', buffering=0) as stream:
                for size in (1048576, 4096, 512, 1):
                    block = b'\xa7'*size
                    while written <= usage.total:
                        try: written += stream.write(block)
                        except OSError as error:
                            assert error.errno == errno.ENOSPC, error
                            failures.append({'errno': error.errno, 'write_size': size, 'written': written})
                            break
                    else: raise AssertionError('Safety bound exceeded')
                os.fsync(stream.fileno())
            exhausted = shutil.disk_usage(mount)
            assert failures and exhausted.free < 4096, exhausted
            release.touch()
            final = engine.wait(receipt['job_ids'][0], timeout=60)
            assert final['state'] == 'failed' and final['error']['code'] == 'OUT_OF_DISK', final
            assert not target.exists() and not stage.parent.exists() and not pid_alive(witness['pid'])
            known_full = self.submit(engine, trim(self.source, mount/'qa002-known-full.mp4'))
            assert not known_full['accepted'] and any(e['code'] == 'OUT_OF_DISK' for e in known_full['errors']), known_full
            fill.unlink()
            recovery = self.successful(engine, mount, precise=True)
            return {'volume_total': usage.total, 'free_before': usage.free, 'free_at_exhaustion': exhausted.free,
                    'real_write_failures': failures, 'witness': witness, 'receipt': receipt, 'terminal': final,
                    'known_full_rejection': known_full, 'staging_removed': True, 'recovery': recovery}
        finally:
            release.touch(exist_ok=True)
            if fill.exists(): fill.unlink()
            self.close(engine)

    def readonly(self):
        mount = self.args.readonly_mount.resolve(strict=True)
        assert os.stat(mount).st_dev != os.stat(self.root).st_dev
        target = mount/'qa002-readonly-output.mp4'
        try:
            with target.open('xb') as stream: stream.write(b'UNEXPECTED')
            raise AssertionError('Test volume is not read-only')
        except OSError as error:
            assert error.errno == errno.EROFS, error
        engine = self.api.create(configuration())
        try:
            receipt = self.submit(engine, trim(self.source, target))
            assert not receipt['accepted'] and any(e['code'] == 'PERMISSION_DENIED' for e in receipt['errors']), receipt
            assert not target.exists() and not list(mount.glob('.mediastudio-*'))
            return {'physical_write_errno': errno.EROFS, 'receipt': receipt, 'recovery': self.successful(engine, self.root)}
        finally: self.close(engine)

    def tool_failure(self):
        folder = self.root/'tool-failure'; folder.mkdir()
        wrapper = folder/'ffmpeg-fail-once'
        marker = folder/'failed-once'
        real = shutil.which('ffmpeg')
        wrapper.write_text(f'''#!{sys.executable}
import os, pathlib, subprocess, sys
args = sys.argv[1:]
marker = pathlib.Path({str(marker)!r})
if args and args[-1].endswith('output.mp4') and '-c:v' in args and not marker.exists():
    code = subprocess.call([{real!r}, *args])
    marker.touch()
    print({str(self.source)!r}, file=sys.stderr)
    sys.exit(37 if code == 0 else code)
os.execv({real!r}, [{real!r}, *args])
''')
        wrapper.chmod(0o700)
        engine = self.api.create(configuration(ffmpeg=wrapper))
        target = folder/'failed.mp4'
        try:
            receipt = self.submit(engine, trim(self.source, target, precise=True))
            assert receipt['accepted'], receipt
            final = engine.wait(receipt['job_ids'][0])
            assert final['state'] == 'failed' and final['error']['code'] == 'PROCESS_FAILED', final
            assert not final.get('result') and not target.exists() and not list(folder.glob('.mediastudio-*'))
            assert marker.exists() and str(self.source) not in json.dumps(final), final
            return {'receipt': receipt, 'terminal': final, 'injected_tool_exit': 37,
                    'private_diagnostic_not_in_public_snapshot': True,
                    'staging_removed': True, 'recovery': self.successful(engine, folder, precise=True)}
        finally: self.close(engine)

    def main(self):
        if self.args.readonly_mount:
            self.check('readonly-volume', self.readonly)
            self.check('tool-nonzero-cleanup-and-recovery', self.tool_failure)
        else:
            for kind in ('same-source', 'existing-file', 'existing-directory', 'symlink', 'dangling-symlink',
                         'hardlink', 'fifo', 'no-search-parent', 'nonwritable-parent', 'unreadable-source'):
                self.check(kind, lambda kind=kind: self.rejected_path(kind))
            for kind in ('corrupt', 'truncated', 'out-of-range', 'network-uri', 'external-playlist', 'external-mov'):
                self.check(kind, lambda kind=kind: self.invalid_input(kind))
            self.check('special-path', self.special_path)
            for kind in ('file', 'directory', 'symlink', 'hardlink'):
                self.check(f'target-race-{kind}', lambda kind=kind: self.target_race(kind))
            if self.args.mount: self.check('real-disk-full-after-acceptance', self.real_disk_full)
        result = {'candidate_library': str(self.api.path), 'library_sha256': sha(self.api.path),
                  'source': str(self.source), 'source_sha256': self.source_hash,
                  'source_unchanged': sha(self.source) == self.source_hash,
                  'checks': self.checks, 'commands': self.commands,
                  'ffi_outputs': self.api.outputs, 'ffi_frees': self.api.frees}
        passed = (all(c['status'] == 'PASS' for c in self.checks) and result['source_unchanged']
                  and self.api.outputs == self.api.frees)
        result['result'] = 'PASS' if passed else 'FAIL'
        with self.args.report.open('x') as stream:
            stream.write(json.dumps(result, indent=2, ensure_ascii=False)+'\n')
        return 0 if passed else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--mount', type=Path)
    parser.add_argument('--readonly-mount', type=Path)
    raise SystemExit(Suite(parser.parse_args()).main())
