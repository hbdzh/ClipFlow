#!/usr/bin/env python3
"""AC17/18 independent real-native-worker C ABI checks; no GUI claim."""
import argparse, copy, errno, hashlib, json, math, os, shutil, stat, struct
import subprocess, sys, threading, time, traceback
from pathlib import Path

sys.dont_write_bytecode = True
if not __debug__: raise SystemExit('Assertions are required; do not use -O')
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tests/core_004'))
from support import API, configuration, control
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,d):Path(p).write_text(json.dumps(d,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def rows(p):
 try:return [json.loads(x) for x in Path(p).read_text().splitlines() if x]
 except FileNotFoundError:return []
def pcm(p,n=132300):
 data=b''.join(struct.pack('<ff',.1*math.sin(i*2*math.pi*440/44100),.07*math.sin(i*2*math.pi*660/44100)) for i in range(n))
 p.write_bytes(b'RIFF'+struct.pack('<I',len(data)+36)+b'WAVEfmt '+struct.pack('<IHHIIHH',16,3,2,44100,352800,8,32)+b'data'+struct.pack('<I',len(data))+data)
def request(source,destination):return {'schema_version':0,'operation':'ai_separate','source':{'uri':source.as_uri()},'destination':{'uri':destination.as_uri()},'model_id':'umx_hq_vocals_onnx_v1'}
class Suite:
 def __init__(self,a):
  self.a=a;self.work=a.work.resolve();self.work.mkdir(mode=0o700,parents=True,exist_ok=False);self.commands=[];self.checks=[];self.api=API(a.library or a.app/'Contents/Frameworks/libmedia_ffi.dylib');self.model=a.app/'Contents/Resources/ai/model.onnx';self.graphs=a.app/'Contents/Resources/ai/graphs';self.runtime=a.app/'Contents/Frameworks/libonnxruntime.dylib';self.base_source=self.work/'source.wav';pcm(self.base_source);self.source_sha=sha(self.base_source);self.candidate={str(p.relative_to(a.app)):sha(p) for p in a.app.rglob('*') if p.is_file()};self.qa_sources={str(p):sha(p) for p in [Path(__file__).resolve(),Path(__file__).with_name('ai_atomic_worker.c').resolve(),Path(__file__).with_name('ai_atomic_second_open.c').resolve()]}
 def run(self,args,**kw):
  args=list(map(str,args));r=subprocess.run(args,capture_output=True,text=True,**kw);self.commands.append({'argv':args,'exit':r.returncode,'stdout':r.stdout,'stderr':r.stderr});r.check_returncode();return r
 def config(self,root,worker=None,**over):
  a={'native_worker':str(worker or self.a.worker),'native_graphs':str(self.graphs),'native_runtime':str(self.runtime),'model':str(self.model),'timeout_ms':60000,'development_root':str(root)};a.update(over)
  return configuration(ffmpeg=shutil.which('ffmpeg'),ffprobe=shutil.which('ffprobe'),ai_backend=a)
 def root(self,name,mount=False):
  p=(self.a.mount if mount else self.work)/('qa002-'+(self.work.name+'-' if mount else '')+name);p.mkdir(mode=0o700,exist_ok=False);s=p/'source.wav';shutil.copy2(self.base_source,s);return p,s
 def wrapper(self,name):
  folder=self.work/('tool-'+name);folder.mkdir();audit=folder/'audit.jsonl';release=folder/'release';h=folder/'ai_atomic_config.h'
  library=folder/'second-open.dylib'
  h.write_text('#define REAL_WORKER '+json.dumps(str(self.a.worker))+'\n#define FAULT_MODE '+json.dumps(name)+'\n#define AUDIT_PATH '+json.dumps(str(audit))+'\n#define RELEASE_PATH '+json.dumps(str(release))+'\n#define SECOND_OPEN_LIBRARY '+json.dumps(str(library))+'\n');binary=folder/'worker-fault'
  if name in {'second-create','second-write'}:
   self.run(['xcrun','clang','-std=c11','-Wall','-Wextra','-Werror','-dynamiclib','-I',folder,Path(__file__).with_name('ai_atomic_second_open.c'),'-o',library]);self.run(['codesign','--force','--sign','-','--timestamp=none',library])
  self.run(['xcrun','clang','-std=c11','-Wall','-Wextra','-Werror','-O2','-I',folder,Path(__file__).with_name('ai_atomic_worker.c'),'-o',binary]);self.run(['codesign','--force','--sign','-','--timestamp=none',binary]);return binary,audit,release
 def submit(self,e,s,d):
  rc,v=e.submit(request(s,d));assert rc==0,(rc,v)
  if v.get('job_id'):return e.wait(v['job_id'],timeout=90)
  return v
 def gate(self,e,j,audit):
  end=time.monotonic()+50
  while time.monotonic()<end:
   values=rows(audit)
   if any(v['phase']=='gate-ready' for v in values):return values
   t=e.snapshot(j);assert t['state'] not in {'failed','succeeded','cancelled'},t;time.sleep(.005)
  raise AssertionError('real native worker fault gate not observed')
 def resources_clean(self,e,root,source):
  rc,v=e.submit(control('queue_snapshot'));assert rc==0;assert v['active_count']==0 and not v['cleanup_unconfirmed'],v
  assert v['resources']['global_reserved_storage_bytes']==0,v
  assert not list(root.glob('.mediastudio-ai-*'));assert sha(source)==self.source_sha
  return v
 def recover(self,e,root,source):
  d=root/'recovered';v=self.submit(e,source,d);assert v['state']=='succeeded',v;assert sorted(p.name for p in d.iterdir())==['accompaniment.wav','vocals.wav']
  return {'state':v['state'],'files':self.verify_outputs(d),'queue':self.resources_clean(e,root,source)}
 def verify_outputs(self,d):
  checks=[]
  for p in sorted(d.iterdir()):
   r=self.run([shutil.which('ffprobe'),'-v','error','-show_entries','stream=codec_name,sample_rate,channels,duration','-of','json',p]);probe=json.loads(r.stdout);assert probe['streams'][0]['codec_name']=='pcm_f32le' and probe['streams'][0]['sample_rate']=='44100' and probe['streams'][0]['channels']==2
   data=p.read_bytes();assert data[:4]==b'RIFF' and data[8:16]==b'WAVEfmt ' and data[36:40]==b'data';count=struct.unpack('<I',data[40:44])[0];assert count==132300*8 and len(data)==count+44
   values=[v[0] for v in struct.iter_unpack('<f',data[44:])];assert all(math.isfinite(v) for v in values)
   self.run([shutil.which('ffmpeg'),'-nostdin','-v','error','-xerror','-i',p,'-map','0:a:0','-f','null','-']);checks.append({'name':p.name,'sha256':sha(p),'probe':probe,'frames':count//8,'all_finite':True,'decode_exit':0})
  return checks
 def success_atomic(self):
  root,s=self.root('success');e=self.api.create(self.config(root));d=root/'result';observations=[];stop=threading.Event()
  def observe():
   while not stop.is_set():
    if d.exists():observations.append(sorted(p.name for p in d.iterdir()))
    time.sleep(.0005)
  t=threading.Thread(target=observe);t.start()
  try:
   v=self.submit(e,s,d);assert v['state']=='succeeded',v;assert sorted(x.name for x in d.iterdir())==['accompaniment.wav','vocals.wav'];assert all(x==['accompaniment.wav','vocals.wav'] for x in observations),observations
   q=self.resources_clean(e,root,s);return {'terminal':v,'published_observations':len(observations),'no_observed_partial_group':True,'same_volume':os.stat(d).st_dev==os.stat(root).st_dev,'files':self.verify_outputs(d),'queue':q}
  finally:stop.set();t.join();assert e.destroy()==0
 def fault(self,name):
  root,s=self.root(name);binary,audit,release=self.wrapper(name);e=self.api.create(self.config(root,binary));d=root/'result';protected=None
  try:
   rc,v=e.submit(request(s,d));assert rc==0 and v.get('job_id'),(rc,v);j=v['job_id']
   if name in {'target-race','cancel-ready'}:
    self.gate(e,j,audit)
    if name=='target-race':d.mkdir();protected=d/'keep.txt';protected.write_text('KEEP USER CONTENT');release.write_text('release')
    else:assert e.cancel(j)==0;assert e.cancel(j)==0
   final=e.wait(j,timeout=90);expected={'second-create':'PROCESS_FAILED','second-write':'PROCESS_FAILED','second-invalid':'OUTPUT_VALIDATION_FAILED','target-race':'OUTPUT_EXISTS','cancel-ready':'CANCELLED'}[name]
   assert final['state']==('cancelled' if name=='cancel-ready' else 'failed') and final['error']['code']==expected,final
   if protected:assert protected.read_text()=='KEEP USER CONTENT' and sorted(p.name for p in d.iterdir())==['keep.txt']
   else:assert not d.exists()
   witness=rows(audit)
   if name=='second-create':assert any(v['phase']=='real-second-open-permission-denial' and v['first_stem_existed'] and v['open_result']==-1 and v['errno']==errno.EACCES and v['restored_directory_mode'] for v in witness),witness
   if name=='second-write':assert any(v['phase']=='real-second-write-ebadf' and v['second_stem_bytes_before_failure']>=8192 and v['write_result']==-1 and v['errno']==errno.EBADF for v in witness),witness
   if name=='second-invalid':assert any(v['phase']=='second-output-corrupted-nan' and v['vocals_bytes']>44 and v['accompaniment_bytes']>44 for v in witness),witness
   self.resources_clean(e,root,s);evidence={'terminal':final,'witness':witness,'no_half_group':True,'same_engine_recovery':self.recover(e,root,s)}
   return evidence
  finally:
   release.touch(exist_ok=True)
   for j in e.jobs:
    try:e.cancel(j);e.wait(j,timeout=10)
    except Exception:pass
   assert e.destroy()==0
 def existing_target(self):
  root,s=self.root('existing');e=self.api.create(self.config(root));d=root/'result';d.mkdir();(d/'keep.txt').write_text('KEEP USER CONTENT')
  try:
   v=self.submit(e,s,d);assert not v.get('job_id') and v['errors'][0]['code']=='OUTPUT_EXISTS',v;assert (d/'keep.txt').read_text()=='KEEP USER CONTENT';return {'rejection':v,'same_engine_recovery':self.recover(e,root,s)}
  finally:assert e.destroy()==0
 def dependency(self):
  bad_model=self.work/'bad-model.onnx';bad_model.write_bytes(b'not the approved model');bad_runtime=self.work/'bad-runtime.dylib';bad_runtime.write_bytes(b'not a runtime');bad_graphs=self.work/'bad-graphs';shutil.copytree(self.graphs,bad_graphs);(bad_graphs/'post.onnx').write_bytes(b'bad graph hash')
  cases=[('worker-missing',{'native_worker':str(self.work/'missing-worker')},'DEPENDENCY_MISSING'),('runtime-missing',{'native_runtime':str(self.work/'missing-runtime')},'DEPENDENCY_MISSING'),('model-missing',{'model':str(self.work/'missing-model')},'DEPENDENCY_MISSING'),('model-hash',{'model':str(bad_model)},'MODEL_INVALID'),('graphs-missing',{'native_graphs':str(self.work/'missing-graphs')},'DEPENDENCY_MISSING'),('graph-hash',{'native_graphs':str(bad_graphs)},'PROCESS_FAILED'),('runtime-invalid',{'native_runtime':str(bad_runtime)},'PROCESS_FAILED'),('worker-invalid-response',{'native_worker':'/usr/bin/true'},'PROCESS_FAILED')];out=[]
  for name,over,expected in cases:
   root,s=self.root(name);e=self.api.create(self.config(root,**over));d=root/'result'
   try:
    v=self.submit(e,s,d);code=v.get('error',{}).get('code') if v.get('job_id') else v.get('errors',[{}])[0].get('code');assert code==expected,(name,v);assert not d.exists();q=self.resources_clean(e,root,s);out.append({'case':name,'response':v,'no_output':True,'queue':q})
   finally:assert e.destroy()==0
  return out
 def full_disk(self):
  mount=self.a.mount.resolve();usage=shutil.disk_usage(mount);assert os.stat(mount).st_dev!=os.stat(self.work).st_dev and 200*1024**2<=usage.total<=400*1024**2 and shutil.disk_usage(self.work).free>1024**3
  root,s=self.root('full-disk',True);binary,audit,release=self.wrapper('disk-full');e=self.api.create(self.config(root,binary));d=root/'result';fill=mount/'qa002-owned-ai-filler';assert not fill.exists();exhausted=[];written=0
  try:
   rc,v=e.submit(request(s,d));assert rc==0 and v.get('job_id'),v;j=v['job_id'];self.gate(e,j,audit)
   with fill.open('xb',buffering=0) as f:
    for block in [1024**2,4096,512,1]:
     data=b'\0'*block
     while True:
      try:n=f.write(data);written+=n
      except OSError as err:
       assert err.errno==errno.ENOSPC;exhausted.append({'errno':err.errno,'block_bytes':block,'written':written});break
   free=shutil.disk_usage(mount).free;assert free==0;release.write_text('release');final=e.wait(j,timeout=90);assert final['state']=='failed' and final['error']['code']=='OUT_OF_DISK' and not d.exists(),final
   assert not list(root.glob('.mediastudio-ai-*'));fill.unlink();return {'terminal':final,'real_enospc':exhausted,'written_bytes':written,'free_bytes_at_release':free,'worker':rows(audit),'source_unchanged':sha(s)==self.source_sha,'published':d.exists(),'free_after_cleanup':shutil.disk_usage(mount).free,'same_engine_recovery':self.recover(e,root,s)}
  finally:
   release.touch(exist_ok=True)
   if fill.exists():fill.unlink()
   for j in e.jobs:
    try:e.cancel(j);e.wait(j,timeout=10)
    except Exception:pass
   assert e.destroy()==0
 def check(self,name,fn):
  try:detail=fn();self.checks.append({'name':name,'result':'PASS','detail':detail})
  except Exception:self.checks.append({'name':name,'result':'FAIL','error':traceback.format_exc()})
  print(name,self.checks[-1]['result'],flush=True)
 def main(self):
  self.check('success_atomic',self.success_atomic)
  for name in ['second-create','second-write','second-invalid','target-race','cancel-ready']:self.check(name,lambda name=name:self.fault(name))
  self.check('existing_target',self.existing_target);self.check('dependency_boundaries',self.dependency);self.check('real_full_disk',self.full_disk)
  assert self.api.outputs==self.api.frees;assert sha(self.base_source)==self.source_sha;assert self.candidate=={str(p.relative_to(self.a.app)):sha(p) for p in self.a.app.rglob('*') if p.is_file()};assert self.qa_sources=={p:sha(p) for p in self.qa_sources}
  result='PASS' if all(x['result']=='PASS' for x in self.checks) else 'FAIL';write(self.a.report,{'result':result,'scope':'Actual final signed Core with same-machine-code native worker re-signed without sandbox inheritance; filesystem fault wrappers; not GUI or final native helper signature evidence','app':str(self.a.app),'library':str(self.api.path),'core_sha256':sha(self.api.path),'standalone_worker_sha256':sha(self.a.worker),'source_sha256':self.source_sha,'qa_source_hashes':self.qa_sources,'qa_sources_unchanged':True,'checks':self.checks,'ffi_outputs':self.api.outputs,'ffi_frees':self.api.frees,'candidate_file_count':len(self.candidate),'candidate_all_files_unchanged':True,'commands':self.commands});return 0 if result=='PASS' else 1
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--app',type=Path,required=True);p.add_argument('--library',type=Path);p.add_argument('--worker',type=Path,required=True);p.add_argument('--work',type=Path,required=True);p.add_argument('--mount',type=Path,required=True);p.add_argument('--report',type=Path,required=True);a=p.parse_args();assert not a.report.exists();a.report.parent.mkdir(parents=True,exist_ok=True);raise SystemExit(Suite(a).main())
