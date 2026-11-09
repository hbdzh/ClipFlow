#!/usr/bin/env python3
"""Run the same Swift bridge self-test plus a disposable missing-dylib negative."""
from pathlib import Path
from datetime import datetime, timezone, timedelta
import hashlib
import json
import shutil
import subprocess
import tempfile
import sys

sys.dont_write_bytecode = True
from build_spike import assert_safe_output, check_layout

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
APP = HERE / 'build/MediaStudioSpike.app'
BINARY = APP / 'Contents/MacOS/MediaStudioSpike'


def main():
    check_layout()
    records = []
    def run(argv):
        p = subprocess.run(list(map(str, argv)), cwd=ROOT, text=True, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=60)
        records.append({'argv': list(map(str, argv)), 'returncode': p.returncode, 'output': p.stdout})
        return p
    path_checks = run([sys.executable, HERE / 'test_build_paths.py', '-v'])
    assert path_checks.returncode == 0, path_checks.stdout
    normal = run([BINARY, '--self-test'])
    assert normal.returncode == 0, normal.stdout
    report = json.loads(normal.stdout)
    assert report['bridge_diagnostics']['all_calls_off_main_thread'] is True
    assert report['bridge_diagnostics']['output_calls'] == report['bridge_diagnostics']['buffer_release_calls']
    assert report['lifecycle_iterations'] == 200
    original_library = APP / 'Contents/Frameworks/libmedia_ffi.dylib'
    original_hash = hashlib.sha256(original_library.read_bytes()).hexdigest()
    # Only the copied temporary app is damaged. Never mutate the usable bundle.
    with tempfile.TemporaryDirectory(prefix='mediastudio-mac001-negative-') as td:
        broken = Path(td) / 'MissingCore.app'
        shutil.copytree(APP, broken)
        (broken / 'Contents/Frameworks/libmedia_ffi.dylib').unlink()
        missing = run([broken / 'Contents/MacOS/MediaStudioSpike', '--self-test'])
        assert missing.returncode != 0, 'Missing dylib must not fake a successful startup'
        assert 'libmedia_ffi.dylib' in missing.stdout, missing.stdout
    assert hashlib.sha256(original_library.read_bytes()).hexdigest() == original_hash
    data = {'result': 'PASS', 'captured_at': datetime.now(timezone(timedelta(hours=8))).isoformat(),
            'scope': 'real Swift/Rust bridge self-test and dyld startup failure; no GUI/media test',
            'negative_scope': 'missing dylib fails before Swift entry point; not an in-UI recovery test',
            'commands': records, 'self_test': report,
            'usable_bundle_preserved': True,
            'source_digest': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in sorted((HERE / 'Sources').glob('*.swift'))}}
    assert_safe_output(HERE, HERE / 'evidence/ui-001/legacy-self-test.json')
    (HERE / 'evidence/ui-001/legacy-self-test.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    print('MAC-001 self-test:', len(report['checks']), 'checks PASS;', report['lifecycle_iterations'], 'lifecycle iterations')
    print('Missing-dylib startup negative: PASS; original app preserved')


if __name__ == '__main__':
    main()
