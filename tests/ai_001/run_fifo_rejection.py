#!/usr/bin/env python3
"""Bounded real-ABI FIFO rejection; isolate a possible blocking open in a child."""
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


def main():
    parser = argparse.ArgumentParser()
    for name in ('library', 'python', 'worker', 'model', 'temp-parent'):
        parser.add_argument('--' + name, type=lambda x: Path(x).absolute(), required=True)
    parser.add_argument('--child-root', type=lambda x: Path(x).absolute())
    args = parser.parse_args()
    if args.child_root:
        root = args.child_root
        api = API(args.library)
        cfg = configuration()
        cfg['media_backend']['ai_backend'] = {'python': str(args.python), 'worker': str(args.worker),
            'model': str(args.model), 'timeout_ms': 600000, 'development_root': str(root)}
        engine = api.create(cfg)
        request = {'schema_version': 0, 'operation': 'ai_separate',
                   'source': {'uri': (root / 'input.fifo').as_uri()},
                   'destination': {'uri': (root / 'output').as_uri()},
                   'model_id': 'umx_hq_vocals_onnx_v1'}
        start = time.monotonic()
        rc, response = engine.submit(request)
        elapsed = time.monotonic() - start
        if response and response.get('job_id'):
            engine.cancel(response['job_id'])
            engine.wait(response['job_id'], timeout=10)
        destroy = engine.destroy()
        good = (rc == 0 and response and response.get('accepted') is False and
                not response.get('job_id') and elapsed < 3 and destroy == 0 and
                not (root / 'output').exists() and not list(root.glob('.mediastudio-ai-*')) and
                api.outputs == api.frees)
        print(json.dumps({'result': 'PASS' if good else 'FAIL', 'ffi_rc': rc,
                          'response': response, 'elapsed_seconds': elapsed,
                          'destroy_rc': destroy, 'ffi_outputs': api.outputs, 'ffi_frees': api.frees}))
        raise SystemExit(0 if good else 1)
    root = Path(tempfile.mkdtemp(prefix='ai001-fifo-', dir=args.temp_parent)).resolve()
    os.chmod(root, 0o700)
    os.mkfifo(root / 'input.fifo', 0o600)
    command = [str(args.python), '-B', str(Path(__file__).resolve()),
               '--library', str(args.library), '--python', str(args.python), '--worker', str(args.worker),
               '--model', str(args.model), '--temp-parent', str(args.temp_parent), '--child-root', str(root)]
    result = {'result': 'FAIL', 'library_sha256': sha(args.library), 'timeout_seconds': 5}
    try:
        outcome = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, timeout=5)
        result['exit_code'] = outcome.returncode
        result['child'] = json.loads(outcome.stdout)
        result['result'] = 'PASS' if outcome.returncode == 0 and result['child']['result'] == 'PASS' else 'FAIL'
    except subprocess.TimeoutExpired:
        result['timeout'] = True
    finally:
        if not list(root.glob('.mediastudio-ai-*')):
            shutil.rmtree(root)
        else:
            result['retained_root'] = str(root)
    print(json.dumps(result, indent=2))
    if result['result'] != 'PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
