#!/usr/bin/env python3
"""Reproduce owned fixture generation, safety negatives and independent reference checks."""
import json
import os
import platform
from pathlib import Path
import subprocess
import sys
import time

sys.dont_write_bytecode = True
from common import HERE, ROOT, EVIDENCE, sha256, timestamp, write_json


def source_hashes():
    return {str(p.relative_to(ROOT)): sha256(p) for p in sorted(HERE.iterdir())
            if p.is_file() and p.suffix in ('.py', '.json')}


def main():
    before = source_hashes()
    records = []
    passed = True
    steps = [
        [sys.executable, str(HERE / 'test_safety.py')],
        [sys.executable, '-O', str(HERE / 'test_safety.py')],
        [sys.executable, str(HERE / 'generate.py')],
        [sys.executable, str(HERE / 'validate.py')],
    ]
    for argv in steps:
        started = time.monotonic()
        try:
            process = subprocess.run(argv, cwd=ROOT, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60,
                env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
            record = {'argv': argv, 'returncode': process.returncode, 'stdout': process.stdout,
                      'stderr': process.stderr, 'elapsed_seconds': round(time.monotonic() - started, 6)}
            passed = process.returncode == 0
        except subprocess.TimeoutExpired:
            record = {'argv': argv, 'returncode': None, 'result': 'TIMEOUT after 60s'}
            passed = False
        records.append(record)
        print(('PASS: ' if passed else 'FAIL: ') + ' '.join(argv), flush=True)
        if not passed:
            break
    after = source_hashes()
    if before != after:
        passed = False
    report = {'captured_at': timestamp(), 'scope': 'QA-001 input/FFmpeg-reference suite; Core media pipeline remains BLOCKED',
              'result': 'PASS' if passed else 'FAIL', 'system': platform.system(), 'machine': platform.machine(),
              'os_version': platform.mac_ver()[0], 'source_hashes_before': before, 'source_hashes_after': after,
              'sources_stable_during_run': before == after, 'commands': records}
    write_json(EVIDENCE / 'suite-run.json', report)
    raise SystemExit(0 if passed else 1)


if __name__ == '__main__':
    main()
