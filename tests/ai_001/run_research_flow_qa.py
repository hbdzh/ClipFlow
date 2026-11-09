#!/usr/bin/env python3
"""QA rerun of author's real worker/transaction research test (not ABI)."""
import argparse,hashlib,json,os,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def main():
 p=argparse.ArgumentParser()
 for key in ('python','worker','model','input'):p.add_argument('--'+key,type=lambda s:Path(s).absolute(),required=True)
 a=p.parse_args()
 paths={'test':ROOT/'core/media-ai/tests/research_flow.rs','transaction':ROOT/'core/media-ai/src/lib.rs','runner':ROOT/'core/media-ai/src/runner.rs','worker':a.worker,'model':a.model,'input':a.input}
 before={k:sha(v) for k,v in paths.items()}
 env=os.environ.copy();env.update(AI001_PYTHON=str(a.python),AI001_STREAM_WORKER=str(a.worker),AI001_MODEL=str(a.model),AI001_INPUT=str(a.input))
 cmd=['cargo','test','--manifest-path',str(ROOT/'core/Cargo.toml'),'--offline','-p','media-ai','--test','research_flow','--','--ignored','--nocapture']
 start=time.monotonic();r=subprocess.run(cmd,cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90,text=True)
 after={k:sha(v) for k,v in paths.items()}
 result={'result':'PASS' if r.returncode==0 and before==after else 'FAIL','returncode':r.returncode,
         'elapsed_seconds':time.monotonic()-start,'before_sha256':before,'after_sha256':after,
         'stdout':r.stdout,'stderr':r.stderr,'scope':'trusted-temp local component experiment; actual worker staging and directory rename; failure injection uses separate fake process; no ABI/shared job or independent WAV validation'}
 print(json.dumps(result,indent=2));raise SystemExit(0 if result['result']=='PASS' else 1)
if __name__=='__main__':main()
