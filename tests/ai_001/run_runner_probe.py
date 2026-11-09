#!/usr/bin/env python3
"""Build and run an independent, fixed-command runner sanity probe."""
import hashlib,json,os,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
source=ROOT/'core/media-ai/src/runner.rs'; before=sha(source)
subprocess.run(['cargo','build','--manifest-path',str(ROOT/'core/Cargo.toml'),'--offline','-p','media-ai'],check=True,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
rlibs=list((ROOT/'core/target/debug/deps').glob('libmedia_ai-*.rlib'))
if not rlibs:raise RuntimeError('rlib not found')
lib=max(rlibs,key=lambda p:p.stat().st_mtime_ns)
with tempfile.TemporaryDirectory(prefix='ai001-qa-runner-') as td:
 exe=Path(td)/'runner-probe'
 subprocess.run(['rustc','--edition','2024',str(ROOT/'tests/ai_001/runner_probe.rs'),'-L',f'dependency={lib.parent}','--extern',f'media_ai={lib}','-o',str(exe)],check=True,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 env=os.environ.copy();env['AI001_QA_SENTINEL']='private-sentinel';env['PYTHONPATH']='/untrusted/fake/qa'
 result=subprocess.run([str(exe)],cwd=ROOT,env=env,capture_output=True,text=True,timeout=15)
 if result.returncode:raise RuntimeError(f"runner probe failed: {result.returncode}; {result.stderr}")
 pipe_exe=Path(td)/'runner-pipe-probe'
 subprocess.run(['rustc','--edition','2024',str(ROOT/'tests/ai_001/runner_pipe_probe.rs'),'-L',f'dependency={lib.parent}','--extern',f'media_ai={lib}','-o',str(pipe_exe)],check=True,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 pipe=subprocess.run([str(pipe_exe)],cwd=ROOT,env=env,capture_output=True,text=True,timeout=15)
 if pipe.returncode:raise RuntimeError(f'pipe probe failed: {pipe.returncode}; {pipe.stderr}')
 if sha(source)!=before:raise RuntimeError('source changed during run')
 print(json.dumps({'result':'PASS','runner_sha256':before,'qa_source_sha256':sha(ROOT/'tests/ai_001/runner_probe.rs'),'rlib_sha256':sha(lib),'pipe_qa_source_sha256':sha(ROOT/'tests/ai_001/runner_pipe_probe.rs'),'stdout':[result.stdout.strip(),pipe.stdout.strip()],'scope':'independent env stripping/combined diagnostic bound; macOS host, no real AI job'},indent=2))
