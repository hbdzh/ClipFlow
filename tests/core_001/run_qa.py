#!/usr/bin/env python3
"""Compile the real public header, link a C caller, then run Python ABI tests."""
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    library = args.library.resolve(strict=True)
    cc, cxx = shutil.which('clang') or shutil.which('cc'), shutil.which('clang++') or shutil.which('c++')
    if not cc or not cxx:
        raise SystemExit('BLOCKED: native C and C++ compilers are required')
    records = []

    def run(argv):
        try:
            result = subprocess.run(argv, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        except subprocess.TimeoutExpired:
            records.append({'argv': list(map(str, argv)), 'returncode': None, 'output': 'TIMEOUT after 60 seconds'})
            print('TIMEOUT after 60 seconds')
            return False
        records.append({'argv': list(map(str, argv)), 'returncode': result.returncode, 'output': result.stdout})
        print(result.stdout, end='')
        return result.returncode == 0

    passed = True
    with tempfile.TemporaryDirectory(prefix='mediastudio-core001-') as tmp:
        binary = str(Path(tmp) / 'abi-smoke')
        passed &= run([cc, '-std=c11', '-Wall', '-Wextra', '-Werror', '-I', str(ROOT / 'contracts'),
                       str(HERE / 'abi_smoke.c'), str(library), '-Wl,-rpath,' + str(library.parent), '-o', binary])
        passed &= run([cxx, '-std=c++17', '-Wall', '-Wextra', '-Werror', '-fsyntax-only',
                       '-I', str(ROOT / 'contracts'), str(HERE / 'abi_header.cpp')])
        if Path(binary).is_file():
            passed &= run([binary])
        else:
            passed = False
        passed &= run([sys.executable, str(HERE / 'test_ffi.py'), '--library', str(library), '-v'])
    report = {'scope': 'CORE-001 real C ABI with mock jobs; not real media or native UI verification',
              'captured_at': datetime.now(timezone(timedelta(hours=8))).isoformat(),
              'platform': platform.system(), 'machine': platform.machine(),
              'os_version': platform.mac_ver()[0] or platform.release(),
              'library': str(library), 'library_sha256': hashlib.sha256(library.read_bytes()).hexdigest(),
              'public_header_sha256': hashlib.sha256((ROOT / 'contracts/media_core_v0.h').read_bytes()).hexdigest(),
              'result': 'PASS' if passed else 'FAIL', 'commands': records}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print('CORE-001 independent ABI QA:', report['result'])
    raise SystemExit(0 if passed else 1)


if __name__ == '__main__':
    main()
