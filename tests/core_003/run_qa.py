#!/usr/bin/env python3
"""Execute independent CORE-003 gates and retain every actual process result."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
sys.dont_write_bytecode=True
from support import HERE,ROOT,digest,report


def sources():
    # Compile inputs plus normative public contracts, not generated owner evidence
    # or post-run progress reports in core package roots.
    files=[]
    for base in ['core','contracts']:
        for p in (ROOT/base).rglob('*'):
            if not p.is_file() or any(x in p.parts for x in {'build','target','evidence','__pycache__'}):continue
            suffixes={'.rs','.toml','.lock'} if base=='core' else {'.h','.md','.json'}
            if p.suffix in suffixes:files.append(p)
    return {str(p.relative_to(ROOT)):digest(p) for p in sorted(files)}


def tests():
    return {str(p.relative_to(ROOT)):digest(p) for p in sorted(HERE.glob('*.py'))}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--library',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=HERE/'evidence/qa-run.json');parser.add_argument('--skip-fixture-precheck',action='store_true')
    args=parser.parse_args();library=args.library.resolve(strict=True);before=sources();test_before=tests();binary_before={'library':digest(library),'CLI':digest(library.parent/'media-cli')};commands=[];passed=True
    suite=[]
    if not args.skip_fixture_precheck:suite.append([sys.executable,'-B',HERE/'precheck.py'])
    suite += [[sys.executable,'-B',HERE/'test_contract.py','--library',library,'--output',args.output.with_name(args.output.stem+'-contract.json')],
              [sys.executable,'-B',HERE/'test_media.py','--library',library,'--output',args.output.with_name(args.output.stem+'-media.json')]]
    for argv in suite:
        argv=[str(a) for a in argv];start=time.monotonic()
        p=subprocess.run(argv,cwd=ROOT,capture_output=True,text=True,timeout=600)
        commands.append({'argv':argv,'returncode':p.returncode,'elapsed_seconds':time.monotonic()-start,'stdout':p.stdout,'stderr':p.stderr})
        print(p.stdout,end='');print(p.stderr,end='');passed &= p.returncode==0
    after=sources();test_after=tests();binary_after={'library':digest(library),'CLI':digest(library.parent/'media-cli')};stable=before==after and test_before==test_after and binary_before==binary_after;value={'scope':'CORE-003 independent C ABI/CLI/physical media acceptance on actual macOS host; no native UI/other platform claim',
        'result':'PASS' if passed and stable else 'FAIL','source_before':before,'source_after':after,'core_contracts_unchanged_during_run':before==after,
        'library_sha256':digest(library),'CLI_sha256':digest(library.parent/'media-cli'),
        'test_source_before':test_before,'test_source_after':test_after,'test_sources_unchanged':test_before==test_after,'binary_before':binary_before,'binary_after':binary_after,'binaries_unchanged':binary_before==binary_after,
        'commands':commands,'limits':'No subjective listening or complete visual playback, no native UI, Windows/HOS/Intel, release/signing, long-duration heap or enemy filesystem proof'}
    report(args.output,value);print('QA result:',value['result'],'Core/contracts stable:',before==after);raise SystemExit(value['result']!='PASS')


if __name__=='__main__':main()
