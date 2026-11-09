#!/usr/bin/env python3
"""Observe and cancel this test's actual Core-owned FFmpeg normalization."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests/core_004'))
from support import API, configuration
from verify_audio import sha


def processes():
    result = subprocess.run(['/bin/ps', '-ww', '-axo', 'pid=,ppid=,pgid=,command='],
                            capture_output=True, text=True, timeout=5, check=True)
    rows = []
    for line in result.stdout.splitlines():
        items = line.strip().split(None, 3)
        if len(items) == 4 and all(x.isdigit() for x in items[:3]):
            rows.append((int(items[0]), int(items[1]), int(items[2]), items[3]))
    return rows


def main():
    parser = argparse.ArgumentParser()
    for name in ('library', 'python', 'worker', 'model', 'source', 'temp-parent'):
        parser.add_argument('--' + name, type=lambda x: Path(x).absolute(), required=True)
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix='ai001-normalization-cancel-', dir=args.temp_parent)).resolve()
    os.chmod(root, 0o700)
    api, engine, job, known, clean = API(args.library), None, None, None, False
    evidence = {'result': 'FAIL', 'library_sha256': sha(args.library),
                'worker_sha256': sha(args.worker), 'model_sha256': sha(args.model)}
    try:
        source, destination = root / 'source.flac', root / 'result'
        # A 600-second real source makes it possible to observe the actual
        # conversion process without hooks or replacing any user file.
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(args.source),
                        '-c:a', 'flac', '-y', str(source)], check=True, timeout=180,
                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        source_hash = sha(source)
        cfg = configuration()
        cfg['media_backend']['ai_backend'] = {'python': str(args.python), 'worker': str(args.worker),
            'model': str(args.model), 'timeout_ms': 600000, 'development_root': str(root)}
        engine = api.create(cfg)
        request = {'schema_version': 0, 'operation': 'ai_separate',
                   'source': {'uri': source.as_uri()}, 'destination': {'uri': destination.as_uri()},
                   'model_id': 'umx_hq_vocals_onnx_v1'}
        rc, response = engine.submit(request)
        if rc or not response or not response.get('job_id'):
            raise AssertionError(('submit rejected', rc, response))
        job = response['job_id']
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            hits = [(pid, pgid) for pid, ppid, pgid, cmd in processes()
                    if ppid == os.getpid() and 'ffmpeg' in cmd and
                    'decoded-native.wav' in cmd and str(root) in cmd]
            if len(hits) > 1:
                raise AssertionError('multiple matching ffmpeg subprocesses')
            if hits:
                known = hits[0]
                break
            state = engine.snapshot(job)
            if state['state'] in ('succeeded', 'failed', 'cancelled'):
                raise AssertionError(('terminal before conversion was observed', state))
            time.sleep(0.005)
        if known is None:
            raise AssertionError('actual ffmpeg conversion not observed')
        started = time.monotonic()
        if engine.cancel(job) != 0 or engine.cancel(job) != 0:
            raise AssertionError('cancel failed')
        terminal = engine.wait(job, timeout=30)
        remaining = [(pid, ppid, pgid) for pid, ppid, pgid, _ in processes() if pgid == known[1]]
        stages = list(root.glob('.mediastudio-ai-*'))
        evidence.update(observed_conversion_pid=known[0], observed_conversion_pgid=known[1],
                        terminal=terminal, cancel_seconds=time.monotonic()-started,
                        process_group_survivors=remaining, source_sha256=source_hash,
                        source_unchanged=sha(source)==source_hash, output_absent=not destination.exists(),
                        staging_absent=not stages)
        if (terminal['state'] != 'cancelled' or remaining or not evidence['source_unchanged'] or
                not evidence['output_absent'] or not evidence['staging_absent']):
            raise AssertionError('cancelled conversion did not confirm cleanup')
        clean = engine.destroy() == 0
        evidence['result'] = 'PASS' if clean else 'FAIL'
    except Exception as exc:
        evidence['error'] = repr(exc)
    finally:
        if engine and not engine.closed:
            if job:
                try:
                    engine.cancel(job)
                    engine.wait(job, timeout=30)
                except Exception:
                    pass
            clean = engine.destroy() == 0
        evidence['ffi_outputs'], evidence['ffi_frees'] = api.outputs, api.frees
        if api.outputs != api.frees:
            evidence['result'] = 'FAIL'
        if clean and not list(root.glob('.mediastudio-ai-*')):
            shutil.rmtree(root)
        else:
            evidence['retained_root'] = str(root)
        print(json.dumps(evidence, indent=2, ensure_ascii=False, allow_nan=False))
    if evidence['result'] != 'PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
