"""Independent MAC-002 QA helpers. No imports from production Swift/Core validators."""
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]


def captured_at():return datetime.now(timezone(timedelta(hours=8))).isoformat()
def sha256(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def report(path,value):
    path=Path(path)
    if not path.is_absolute():path=ROOT/path
    if HERE not in path.resolve().parents or '..' in path.relative_to(HERE).parts:
        raise ValueError('MAC002 QA evidence stays in its owned test subtree')
    current=HERE
    for part in path.relative_to(HERE).parts:
        current/=part
        if current.is_symlink():raise ValueError('No symlink evidence path')
        if current.exists() and current==path and (not current.is_file() or current.stat().st_nlink!=1):
            raise ValueError('No hardlink/special evidence file')
    path.parent.mkdir(exist_ok=True,parents=True)
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=path.parent,prefix='.mac002-',delete=False) as f:
        temporary=Path(f.name);json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')
    temporary.replace(path)


class Commands:
    def __init__(self):self.records=[]
    def run(self,argv,expected=0,timeout=180,env=None):
        argv=list(map(str,argv));start=time.monotonic()
        p=subprocess.Popen(argv,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                           text=True,env=env,start_new_session=True)
        try:out,err=p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            import signal
            os.killpg(p.pid,signal.SIGKILL);out,err=p.communicate()
            self.records.append({'argv':argv,'returncode':None,'timeout_seconds':timeout,'stdout':out,'stderr':err})
            raise
        self.records.append({'argv':argv,'returncode':p.returncode,'elapsed_seconds':time.monotonic()-start,'stdout':out,'stderr':err})
        if expected is not None and p.returncode!=expected:raise AssertionError((argv,p.returncode,out,err))
        return subprocess.CompletedProcess(argv,p.returncode,out,err)


def source_hashes():
    result={}
    for base in ['apps/macos','core','contracts']:
        for p in sorted((ROOT/base).rglob('*')):
            if not p.is_file() or any(n in p.parts for n in {'build','target','evidence','__pycache__'}):continue
            include=(base=='apps/macos' and p.suffix in {'.swift','.py','.h','.modulemap'}) or (base=='core' and p.suffix in {'.rs','.toml','.lock'}) or (base=='contracts' and p.suffix in {'.h','.json','.md'})
            if include:result[str(p.relative_to(ROOT))]=sha256(p)
    return result
