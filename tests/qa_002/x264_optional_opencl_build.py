#!/usr/bin/env python3
"""Isolated OpenCL-enabled test library; never an application component."""
import argparse
import hashlib
import json
import subprocess
import tarfile
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work', type=Path, required=True)
    p.add_argument('--report', type=Path, required=True)
    a = p.parse_args()
    root = Path(__file__).resolve().parents[2]
    admission = root / 'docs/qa-002/evidence/x264-optional-abort-20261116/integration-build-admission.json'
    approved = json.loads(admission.read_text())
    assert approved['decision'] == 'APPROVE_FRESH_LOCAL_V5_BUILD_AND_BOUNDED_ORDINARY_VALIDATION'
    archive = root / 'apps/macos/build/MAC-005/sources/x264-31e19f92.tar.gz'
    assert sha(archive) == 'd053c9d86988d6bc78237ca5205865c5ddf99c98ef4cd9927eec8f6d388f6dd9'
    assert not a.report.exists()
    a.work = a.work.resolve()
    a.work.mkdir(parents=True, exist_ok=False)
    commands = []

    def run(argv, cwd):
        r = subprocess.run(list(map(str, argv)), cwd=cwd, stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=300)
        commands.append({'argv': list(map(str, argv)), 'cwd': str(cwd), 'exit': r.returncode,
                         'stdout': r.stdout.decode(errors='replace'), 'stderr': r.stderr.decode(errors='replace')})
        assert r.returncode == 0, commands[-1]

    with tarfile.open(archive) as t:
        t.extractall(a.work, filter='data')
    source = a.work / 'x264-31e19f92f00c7003fa115047ce50978bc98c3a0d'
    for item in approved['patch_order']:
        patch = root / 'apps/macos/patches' / item['name']
        assert sha(patch) == item['sha256']
        run(['patch', '--batch', '--forward', '-p1', '-i', patch], source)
    actual = {n: sha(source/n) for n in approved['final10_source_files']}
    assert actual == approved['final10_source_files']
    options = [f'--prefix={a.work / "install"}', '--enable-static', '--disable-cli']
    # OpenCL is enabled by this pinned configure's default, checked below.
    run(['./configure', *options], source)
    # Multi-depth x264 intentionally enables OpenCL only in its 8-bit objects.
    assert '#define HAVE_OPENCL (BIT_DEPTH==8)' in (source/'config.h').read_text()
    run(['make', '-j4'], source)
    run(['make', 'install'], source)
    library = a.work/'install/lib/libx264.a'
    a.report.write_text(json.dumps({'result': 'BUILD_PASS_NOT_RUNTIME_ACCEPTANCE',
        'scope': __doc__, 'archive_sha256': sha(archive), 'admission_sha256': sha(admission),
        'script_sha256': sha(__file__), 'source': str(source), 'final10_source_files': actual,
        'configure_options': options, 'opencl_enabled': True, 'commands': commands,
        'config_h_sha256': sha(source/'config.h'), 'library': str(library),
        'library_sha256': sha(library), 'production_configuration_unchanged': True}, indent=2)+'\n')


if __name__ == '__main__':
    main()
