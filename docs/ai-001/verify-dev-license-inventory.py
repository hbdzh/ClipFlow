#!/usr/bin/env python3
"""Offline evidence check for the AI-001 development wheelhouse; not a release SBOM."""
import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--wheelhouse', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--sndfile', type=Path, required=True)
    args = parser.parse_args()
    baseline = json.loads((Path(__file__).parent / 'worker/dev-environment-inventory.json').read_text())
    expected = baseline['packages']
    actual = {p.name for p in args.wheelhouse.glob('*.whl')}
    records = []
    for package in expected:
        name = package['wheel']
        path = args.wheelhouse / name
        if not path.is_file():
            raise SystemExit('missing wheel: ' + name)
        digest = sha256(path)
        if digest != package['official_wheel_sha256']:
            raise SystemExit('wheel hash mismatch: ' + name)
        with ZipFile(path) as wheel:
            notices = sorted(n for n in wheel.namelist() if not n.endswith('/') and any(
                w in Path(n).name.lower() for w in ('license', 'copying', 'notice')))
        records.append({'name': package['name'], 'version': package['version'],
                        'wheel': name, 'sha256': digest, 'notice_candidates': notices})
    unknown = sorted(actual - {p['wheel'] for p in expected})
    if unknown:
        raise SystemExit('unexpected wheels: ' + ', '.join(unknown))
    observed_model, observed_snd = sha256(args.model), sha256(args.sndfile)
    if observed_model != 'f27742bb52b24cb039614dc76d0764e279df3eb2a9d6daa82c6daee3369ec747':
        raise SystemExit('model hash mismatch')
    if observed_snd != 'e9fd4d76bc457dcfb956617e8d467df85087ce069747fccd78e1e702d8629c14':
        raise SystemExit('sndfile dylib hash mismatch')
    print(json.dumps({'scope': 'offline local development evidence; NOT release SBOM or complete NOTICE',
                      'model_sha256': observed_model, 'sndfile_sha256': observed_snd,
                      'count': len(records), 'wheels': records}, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
