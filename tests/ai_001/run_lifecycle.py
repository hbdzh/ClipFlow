#!/usr/bin/env python3
"""Independent host-only ABI test of REAL AI worker cancellation or crash.

No worker replacement, injected hooks, shell command, or unrelated process kill.
The authorized --source must be a 44.1kHz stereo float WAV of >= 30 seconds.
Run each case in a separate QA process. stdout is JSON evidence. On uncertain
termination we retain the private test root and explicitly report the gap.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT/'tests'/'core_004'))
from support import API, configuration


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def processes():
    result = subprocess.run(['/bin/ps', '-ww', '-axo', 'pid=,ppid=,pgid=,command='],
                            capture_output=True, text=True, check=True, timeout=5)
    rows = []
    for line in result.stdout.splitlines():
        values = line.strip().split(None, 3)
        if len(values) == 4 and all(v.isdigit() for v in values[:3]):
            rows.append((int(values[0]), int(values[1]), int(values[2]), values[3]))
    return rows


def phase_observed(pid, root):
    completed = subprocess.run(['/usr/sbin/lsof', '-n', '-P', '-p', str(pid)],
                               capture_output=True, text=True, timeout=5)
    # A deleted feature file is still held by the actual worker's open fd;
    # this proves the worker reached feature allocation, not a specific LSTM
    # instruction, and no Python/ONNX instrumentation is involved.
    return any('/.feature-' in line and str(root) in line for line in completed.stdout.splitlines())


def worker_child(worker, root):
    matches = []
    for pid, ppid, pgid, command in processes():
        if ppid != os.getpid() or str(worker) not in command:
            continue
        try:
            words = shlex.split(command)
            i = words.index('--staging')
            stage = Path(words[i+1])
            real = stage.resolve(strict=True)
            if not real.is_relative_to(root) or not stat.S_ISDIR(os.lstat(stage).st_mode):
                continue
        except (ValueError, IndexError, OSError):
            continue
        matches.append((pid, pgid, stage))
    if len(matches) > 1:
        raise AssertionError('multiple workers match this isolated QA root')
    return matches[0] if matches else None


def is_pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def request(source, destination):
    return {'schema_version': 0, 'operation': 'ai_separate',
            'source': {'uri': source.as_uri()}, 'destination': {'uri': destination.as_uri()},
            'model_id': 'umx_hq_vocals_onnx_v1'}


def main():
    parser = argparse.ArgumentParser()
    for name in ['library', 'python', 'worker', 'model', 'source', 'temp-parent']:
        parser.add_argument('--'+name, type=lambda v: Path(v).absolute(), required=True)
    parser.add_argument('--scenario', choices=['cancel','crash','target_race','source_change'], required=True)
    parser.add_argument('--timeout-ms', type=int, default=600000)
    args = parser.parse_args()
    for p in [args.library, args.python, args.worker, args.model, args.source]:
        if not p.is_file(): raise SystemExit('one of the required inputs is not a regular file')
    # Verify enough processing time to observe the actual inference phase.
    import soundfile as sf
    info = sf.info(args.source)
    if info.samplerate != 44100 or info.channels != 2 or info.subtype != 'FLOAT' or info.frames < 30*44100:
        raise SystemExit('source must be >=30 seconds, 44.1 kHz stereo float WAV')
    root = Path(tempfile.mkdtemp(prefix='ai001-qa-lifecycle-', dir=args.temp_parent)).resolve()
    os.chmod(root, 0o700)
    source = root/'source.wav'
    shutil.copyfile(args.source, source)
    source_before = sha(source)
    source_meta = os.lstat(source)
    if not stat.S_ISREG(source_meta.st_mode) or source_meta.st_nlink != 1:
        raise SystemExit('test-owned source identity unexpected')
    destination = root/'result'
    cfg = configuration()
    cfg['media_backend']['ai_backend'] = {'python':str(args.python), 'worker':str(args.worker),
        'model':str(args.model), 'timeout_ms':args.timeout_ms, 'development_root':str(root)}
    evidence = {'schema':1, 'scenario':args.scenario, 'result':'FAIL',
                'library_sha256':sha(args.library), 'worker_sha256':sha(args.worker),
                'model_sha256':sha(args.model), 'source_sha256':source_before,
                'features_observed':False, 'worker_pid':None, 'worker_pgid':None,
                'notes':['lsof observes actual unlinked feature fd, not a specific LSTM instruction',
                         'test-owned private root and one verified direct child only',
                         'does not prove escaped descendants or production threat model']}
    api = API(args.library)
    engine = None
    job = None
    known = None
    closed = False
    try:
        engine = api.create(cfg)
        rc, submit = engine.submit(request(source, destination))
        if rc != 0 or not submit or not submit.get('job_id'):
            raise AssertionError(('valid request not accepted', rc, submit))
        job = submit['job_id']
        deadline = time.monotonic()+min(args.timeout_ms/1000, 90)
        while time.monotonic() < deadline:
            state = engine.snapshot(job)
            if state['state'] in ('succeeded','failed','cancelled'):
                raise AssertionError(('terminal before feature observation',state))
            hit = worker_child(args.worker, root)
            if hit and phase_observed(hit[0], root):
                known = hit
                evidence.update(features_observed=True,worker_pid=hit[0],worker_pgid=hit[1])
                break
            time.sleep(0.01)
        if not known:
            raise AssertionError('actual worker feature fd not observed before deadline')
        pid, pgid, stage = known
        # Confirm this exact direct-child command still belongs to our private
        # root immediately before sending any signal or requesting cancellation.
        current = worker_child(args.worker, root)
        if not current or current[0] != pid or current[2] != stage:
            raise AssertionError('worker identity changed before test action')
        started = time.monotonic()
        if args.scenario == 'cancel':
            if engine.cancel(job) != 0 or engine.cancel(job) != 0:
                raise AssertionError('cancel/repeated cancel not accepted')
            expected = 'cancelled'
        elif args.scenario == 'crash':
            # SIGKILL is sent only to the direct child verified above and only
            # after its real feature fd is observed. This is not a mock worker.
            os.kill(pid, signal.SIGKILL)
            expected = 'failed'
        elif args.scenario == 'target_race':
            # QA is the competing writer, confined to this private root.
            destination.mkdir(mode=0o700)
            (destination/'owner.txt').write_bytes(b'QA concurrent target: keep')
            expected = 'failed'
        else:
            # Mutate only the test-owned source copy after real processing is
            # observed; never modify the caller's original fixture.
            with source.open('ab') as stream:
                stream.write(b'QA source changed'); stream.flush(); os.fsync(stream.fileno())
            evidence['source_after_qa_mutation_sha256'] = sha(source)
            expected = 'failed'
        final = engine.wait(job, timeout=20)
        elapsed = time.monotonic()-started
        evidence['terminal_state'] = final['state']
        evidence['terminal_error'] = final.get('error')
        evidence['terminal_seconds'] = elapsed
        if final['state'] != expected:
            raise AssertionError(('unexpected terminal state',final))
        if args.scenario == 'cancel' and elapsed > 5:
            raise AssertionError(('cancel cleanup exceeded AC16 5 seconds',elapsed))
        if args.scenario == 'crash' and (final.get('error') or {}).get('code') == 'CLEANUP_FAILED':
            raise AssertionError('normal crash did not confirm cleanup')
        if args.scenario == 'target_race' and (final.get('error') or {}).get('code') != 'OUTPUT_EXISTS':
            raise AssertionError(('target race reported wrong error',final))
        if is_pid_alive(pid): raise AssertionError('direct worker remains alive')
        survivors = [(a,b,c) for a,b,c,_ in processes() if c == pgid]
        if survivors: raise AssertionError(('process group still exists',survivors))
        if stage.exists(): raise AssertionError('stage remains after terminal')
        if args.scenario == 'target_race':
            if not destination.is_dir() or sorted(p.name for p in destination.iterdir()) != ['owner.txt']:
                raise AssertionError('competing destination changed')
            if (destination/'owner.txt').read_bytes() != b'QA concurrent target: keep':
                raise AssertionError('competing destination overwritten')
        elif destination.exists():
            raise AssertionError('unexpected final result remains after failure')
        expected_source = evidence.get('source_after_qa_mutation_sha256',source_before)
        if sha(source) != expected_source:
            raise AssertionError('test source changed')
        allowed = {source,destination} if args.scenario == 'target_race' else {source}
        unexpected = sorted(p.name for p in root.iterdir() if p not in allowed)
        if unexpected: raise AssertionError(('unknown artifacts after terminal',unexpected))
        if engine.destroy() != 0: raise AssertionError('destroy after terminal failed')
        closed = True
        evidence['source_unchanged_from_post_qa_action'] = True
        evidence['stage_absent'] = True
        evidence['target_absent'] = args.scenario != 'target_race'
        evidence['competing_target_preserved'] = args.scenario == 'target_race'
        evidence['direct_pid_and_process_group_gone'] = True
        evidence['result'] = 'PASS'
    except Exception as exc:
        evidence['error_type'] = type(exc).__name__
        evidence['error'] = str(exc)
    finally:
        if engine and not closed:
            try:
                if job:
                    engine.cancel(job)
                    evidence['cleanup_terminal'] = engine.wait(job, timeout=20)['state']
                closed = engine.destroy() == 0
            except Exception as exc:
                evidence['cleanup_error'] = type(exc).__name__
        evidence['ffi_outputs'] = api.outputs
        evidence['ffi_frees'] = api.frees
        if api.outputs != api.frees:
            evidence['result'] = 'FAIL'
            evidence['error'] = 'FFI buffer allocation/free mismatch'
        if evidence['result'] == 'PASS' and closed:
            shutil.rmtree(root)
            evidence['owned_root_removed'] = not root.exists()
        else:
            evidence['retained_root'] = str(root)
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0 if evidence['result'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
