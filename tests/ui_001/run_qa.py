#!/usr/bin/env python3
"""Run independent native self-test acceptance, preserving exact process output."""
import argparse
from datetime import datetime,timezone,timedelta
import json
from pathlib import Path
import subprocess
import sys
sys.dont_write_bytecode=True
from support import HERE,ROOT,sha256,write_report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--binary',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=HERE/'evidence/qa-run.json')
    args=parser.parse_args();binary=args.binary.resolve(strict=True)
    evidence=args.output.with_name(args.output.stem+'-native.json')
    argv=[sys.executable,'-B',str(HERE/'test_native.py'),'--binary',str(binary),'--output',str(evidence),'-v']
    p=subprocess.run(argv,cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=240)
    print(p.stdout,end='')
    write_report(args.output,{'captured_at':datetime.now(timezone(timedelta(hours=8))).isoformat(),
        'scope':'UI-001 independent actual native app self-test and physical media QA; no CUA claim',
        'result':'PASS' if p.returncode==0 else 'FAIL','binary_sha256':sha256(binary),
        'argv':argv,'returncode':p.returncode,'output':p.stdout,'native_evidence':str(evidence)})
    raise SystemExit(p.returncode)


if __name__=='__main__':main()
