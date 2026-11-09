#!/usr/bin/env python3
"""Run existing ABI compatibility and new independent CORE-002 acceptance."""
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys

sys.dont_write_bytecode = True
from support import HERE, ROOT, report


def source_hashes():
    files = [p for base in ['core','contracts'] for p in (ROOT/base).rglob('*')
             if p.is_file() and 'target' not in p.parts and p.suffix in {'.rs','.toml','.lock','.h','.json'}]
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--library',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=HERE/'evidence/qa-run.json')
    args=parser.parse_args();library=args.library.resolve(strict=True)
    records=[];before=source_hashes()
    test_sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(HERE.glob('*.py'))}
    artifact_prefix=args.output.stem
    commands=[
        [sys.executable,'-B','tests/core_001/run_qa.py','--library',str(library),
         '--output',str(HERE/'evidence'/(artifact_prefix+'-compat-abi.json'))],
        [sys.executable,'-B',str(HERE/'test_contract.py'),'--library',str(library),'-v'],
        [sys.executable,'-B',str(HERE/'test_media.py'),'--library',str(library),
         '--output',str(HERE/'evidence'/(artifact_prefix+'-media.json')),'-v'],
    ]
    passed=True
    for argv in commands:
        try:
            p=subprocess.run(argv,cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180)
            records.append({'argv':argv,'returncode':p.returncode,'output':p.stdout})
            print(p.stdout,end='');passed &= p.returncode==0
        except subprocess.TimeoutExpired as error:
            records.append({'argv':argv,'returncode':None,'output':'TIMEOUT after 180 seconds',
                            'partial_output':(error.stdout or b'').decode(errors='replace') if isinstance(error.stdout,bytes) else error.stdout})
            passed=False
    after=source_hashes()
    passed &= before==after
    result={'scope':'CORE-002 independent real-media/ABI and controlled process-failure acceptance',
            'captured_at':datetime.now(timezone(timedelta(hours=8))).isoformat(),
            'platform':platform.system(),'machine':platform.machine(),'os_release':platform.release(),
            'test_source_sha256':test_sources,
            'cli_sha256':hashlib.sha256(library.with_name('media-cli').read_bytes()).hexdigest(),
            'library':str(library),'library_sha256':hashlib.sha256(library.read_bytes()).hexdigest(),
            'source_before':before,'source_after':after,'source_unchanged':before==after,
            'result':'PASS' if passed else 'FAIL','commands':records}
    report(args.output,result)
    raise SystemExit(0 if passed else 1)


if __name__=='__main__':main()
