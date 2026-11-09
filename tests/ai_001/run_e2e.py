#!/usr/bin/env python3
"""Independent AI-001 ABI end-to-end smoke, no production module imports.

Uses an explicitly supplied authorized normalized 44.1kHz stereo float WAV.
No user input is overwritten. No mock or injected worker counts as an AI pass.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT/'tests'/'core_004'))
from support import API, configuration
from verify_audio import check_audio, sha


def config(args, development_root):
    result = configuration()
    result['media_backend']['ai_backend'] = {
        'python': str(args.python), 'worker': str(args.worker),
        'model': str(args.model), 'timeout_ms': args.timeout_ms,
        'development_root': str(development_root),
    }
    return result


def req(source, destination):
    return {'schema_version': 0, 'operation': 'ai_separate',
            'source': {'uri': source.as_uri()}, 'destination': {'uri': destination.as_uri()},
            'model_id': 'umx_hq_vocals_onnx_v1'}


def snapshot_child_processes(worker):
    """macOS host ps observer for this test's direct child only, never kill others."""
    proc = subprocess.run(['/bin/ps', '-axo', 'pid=,ppid=,command='], capture_output=True,
                          text=True, timeout=5, check=True)
    hits = []
    for row in proc.stdout.splitlines():
        fields = row.strip().split(None, 2)
        if len(fields) == 3 and fields[0].isdigit() and fields[1].isdigit():
            if int(fields[1]) == os.getpid() and str(worker) in fields[2]:
                hits.append(int(fields[0]))
    return hits


def main():
    parser = argparse.ArgumentParser()
    for name in ['library', 'python', 'worker', 'model', 'source', 'temp-parent']:
        parser.add_argument('--'+name, type=lambda p: Path(p).absolute(), required=True)
    parser.add_argument('--timeout-ms', type=int, default=600000)
    parser.add_argument('--torch-weights', type=lambda p: Path(p).absolute())
    args = parser.parse_args()
    for path in (args.library, args.python, args.worker, args.model, args.source):
        if not path.is_file():
            raise SystemExit('required input is unavailable')
    ffprobe, ffmpeg = shutil.which('ffprobe'), shutil.which('ffmpeg')
    if not ffprobe or not ffmpeg:
        raise SystemExit('ffmpeg/ffprobe unavailable')
    source_before = sha(args.source)
    root = Path(tempfile.mkdtemp(prefix='mediastudio-ai001-qa-', dir=args.temp_parent))
    os.chmod(root, 0o700)
    root = root.resolve()
    # The audited development contract accepts one direct source leaf. Copy
    # the authorized fixture into our own private root rather than asking Core
    # to read the original caller-owned file outside that boundary.
    source = root/'source.wav'
    shutil.copyfile(args.source, source)
    if sha(source) != source_before:
        raise SystemExit('test fixture copy did not preserve bytes')
    api = API(args.library)
    engine = None
    job = None
    stopped = False
    traces = []
    partial_views = []
    seen = threading.Event()
    observer_done = threading.Event()
    target = root/'result'

    def watch():
        while not observer_done.is_set():
            if target.exists():
                try:
                    names = sorted(p.name for p in target.iterdir())
                    if names != ['accompaniment.wav', 'vocals.wav']:
                        partial_views.append(names)
                except OSError as exc:
                    partial_views.append([type(exc).__name__])
            time.sleep(0.001)

    evidence = {'schema': 1, 'kind': 'independent_real_abi_smoke', 'result': 'FAIL',
                'library_sha256': sha(args.library), 'worker_sha256': sha(args.worker),
                'model_sha256': sha(args.model), 'source_sha256': source_before,
                'checks': [], 'notes': ['watcher polling cannot prove every atomic interleaving',
                                        'no claims about untested formats, listener ratings or other platforms']}
    observer = threading.Thread(target=watch, daemon=True)
    try:
        engine = api.create(config(args, root))
        # Existing target must never be replaced, including its contents.
        existing = root/'already exists'
        existing.mkdir()
        marker = existing/'owner.txt'
        marker.write_bytes(b'QA existing target: preserve')
        marker_hash = sha(marker)
        rc, rejected = engine.submit(req(source, existing))
        if rc != 0 or rejected.get('accepted') is not False:
            raise AssertionError(('existing-target not rejected', rc, rejected))
        if rejected['errors'][0]['code'] != 'OUTPUT_EXISTS' or sha(marker) != marker_hash:
            raise AssertionError('existing destination altered or wrong error')
        evidence['checks'].append({'name': 'existing-destination', 'result': 'PASS'})
        before = time.monotonic()
        observer.start()
        rc, response = engine.submit(req(source, target))
        if rc != 0 or not response or 'job_id' not in response:
            raise AssertionError(('valid submit failed', rc, response))
        job = response['job_id']
        pids = set()
        deadline = time.monotonic() + args.timeout_ms/1000 + 30
        while time.monotonic() < deadline:
            value = engine.snapshot(job)
            traces.append({'state': value['state'], 'time_seconds': time.monotonic()-before})
            for pid in snapshot_child_processes(args.worker):
                pids.add(pid)
            if value['state'] in ('succeeded', 'failed', 'cancelled'):
                break
            time.sleep(0.01)
        else:
            raise AssertionError('job timeout in QA observer')
        observer_done.set()
        observer.join(timeout=3)
        if value['state'] != 'succeeded':
            raise AssertionError(('actual AI job did not succeed', value))
        if not pids:
            raise AssertionError('no actual worker process observed; not a real AI pass')
        if snapshot_child_processes(args.worker):
            raise AssertionError('direct worker still alive at terminal')
        if partial_views:
            raise AssertionError(('observed partial target directory', partial_views[:5]))
        if sha(args.source) != source_before or sha(source) != source_before:
            raise AssertionError('source changed')
        files = {name: check_audio(target/(name+'.wav'), ffprobe, ffmpeg)
                 for name in ['vocals', 'accompaniment']}
        with __import__('soundfile').SoundFile(source) as source_file:
            if any(value['frames'] != len(source_file) for value in files.values()):
                raise AssertionError('output sample count mismatch')
        if {p.name for p in target.iterdir()} != {'vocals.wav', 'accompaniment.wav'}:
            raise AssertionError('unknown final artifacts')
        item = {'name': 'real-worker-and-stem-decode', 'result': 'PASS',
                'frames': files['vocals']['frames'], 'files': files, 'worker_pids': sorted(pids),
                'states': traces, 'elapsed_seconds': time.monotonic()-before}
        if args.torch_weights:
            cmd = [str(args.python), str(ROOT/'docs'/'ai-001'/'compare_audio_upstream.py'),
                   str(args.torch_weights), str(source), str(target)]
            outcome = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                                     text=True, timeout=180, check=True)
            comparisons = [json.loads(row) for row in outcome.stdout.splitlines() if row.strip()]
            if len(comparisons) != 2 or any(c['relative_rms_error'] > 1e-4 for c in comparisons):
                raise AssertionError(('independent reference mismatch', comparisons))
            item['torch_cpu_reference'] = comparisons
        else:
            item['torch_cpu_reference'] = 'NOT_RUN'
        evidence['checks'].append(item)
        if engine.destroy() != 0:
            raise AssertionError('destroy did not complete')
        stopped = True
        evidence['result'] = 'PASS'
    except Exception as failure:
        evidence['error_type'] = type(failure).__name__
        evidence['error'] = str(failure)
        evidence['traceback'] = traceback.format_exc()
    finally:
        observer_done.set()
        if observer.is_alive():
            observer.join(timeout=3)
        if engine and not stopped:
            try:
                if job:
                    engine.cancel(job)
                    terminal = engine.wait(job, timeout=20)
                    evidence['cleanup_terminal'] = terminal['state']
                stopped = engine.destroy() == 0
            except Exception as failure:
                evidence['cleanup_error'] = str(failure)
        evidence['source_unchanged'] = sha(args.source) == source_before
        evidence['ffi_outputs'] = api.outputs
        evidence['ffi_frees'] = api.frees
        if api.outputs != api.frees:
            evidence['result'] = 'FAIL'
            evidence['error'] = 'FFI buffer release mismatch'
        # Retain failure artifacts; do not erase uncertain/still-owned staging.
        if evidence['result'] == 'PASS' and stopped:
            shutil.rmtree(root)
            evidence['test_root_removed_after_success'] = not root.exists()
        else:
            evidence['retained_failure_root'] = str(root)
    print(json.dumps(evidence, indent=2, ensure_ascii=False, allow_nan=False))
    raise SystemExit(0 if evidence['result'] == 'PASS' else 1)


if __name__ == '__main__':
    main()
