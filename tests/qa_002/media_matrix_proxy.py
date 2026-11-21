#!/usr/bin/env python3
"""Real Core PCM preview transaction QA. Audio is self-authored fixtures only."""
import argparse,json,os,shutil,struct,subprocess,sys,time,traceback
from pathlib import Path
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'tests/core_004'))
from support import API,Commands,configuration,control,sha256

class Suite:
 def __init__(self,a):
  self.a=a;self.api=API(a.library);self.c=Commands();self.rows=[];a.work.mkdir(parents=True,exist_ok=False);a.evidence.parent.mkdir(parents=True,exist_ok=True)
 def request(self,src,dst):return control('prepare_audio_preview',profile='wav_pcm_f32_preview_v1',source={'uri':src.as_uri()},destination={'uri':dst.as_uri()})
 def engine(self,**kw):return self.api.create(configuration(ffmpeg=self.a.tools/'ffmpeg',ffprobe=self.a.tools/'ffprobe',**kw))
 def check(self,name,fn):
  try:r={'case':name,'result':'PASS','detail':fn()}
  except Exception as ex:r={'case':name,'result':'FAIL','error':repr(ex),'traceback':traceback.format_exc()}
  self.rows.append(r);print(name,r['result'],r.get('error',''),flush=True);self.save()
 def save(self):self.a.evidence.write_text(json.dumps({'result':'PASS' if self.rows and all(r['result']=='PASS' for r in self.rows) else 'FAIL','library':str(self.a.library),'library_sha256':sha256(self.a.library),'checks':self.rows,'ffi_outputs':self.api.outputs,'ffi_frees':self.api.frees,'commands':self.c.records},indent=2))
 def success(self,src):
  out=self.a.work/(src.stem+'-'+src.suffix[1:]+'.wav');e=self.engine();before=sha256(src)
  try:
   req=self.request(src,out);rc,r=e.submit(req);assert rc==0 and 'job_id' in r,(rc,r);done=e.wait(r['job_id']);assert done['state']=='succeeded',done
   result=done['result'];assert result['preview_only'] and result['file_created'] and result['profile']=='wav_pcm_f32_preview_v1'
   assert all(result['preview_validation'][k] for k in ['source_identity_unchanged','full_decode','finite_samples','content_mapping_verified'])
   probe=json.loads(self.c.run([shutil.which('ffprobe'),'-v','error','-show_streams','-of','json',out]).stdout);s=probe['streams'][0];assert len(probe['streams'])==1 and s['codec_name']=='pcm_f32le'
   assert int(s['sample_rate'])==result['sample_rate'] and s['channels']==result['channels'] and int(s['duration_ts'])==result['sample_count']
   raw=self.c.run([shutil.which('ffmpeg'),'-v','error','-xerror','-i',out,'-f','f32le','-'],binary=True);assert len(raw)==result['sample_count']*result['channels']*4
   import math;assert all(math.isfinite(v[0]) for v in struct.iter_unpack('<f',raw))
   ref=self.c.run([shutil.which('ffmpeg'),'-v','error','-xerror','-i',src,'-map','0:a:0','-af',f"atrim=end_sample={result['sample_count']}",'-ar',str(result['sample_rate']),'-ac',str(result['channels']),'-c:a','pcm_f32le','-f','f32le','-'],binary=True)
   assert raw==ref,'PCM preview differs from independently decoded source content'
   assert sha256(src)==before
   return {'request':req,'terminal':done,'source_sha256':before,'output_sha256':sha256(out),'independent_decoded_pcm_equal':True,'independent_probe':probe}
  finally:assert e.destroy()==0
 def contract(self):
  src=self.a.fixtures/'libmp3lame-44100-1.mp3';out=self.a.work/'contract.wav';e=self.engine();rows=[]
  try:
   base=self.request(src,out)
   for field,value in [('profile','bad'),('profile',None),('range',{'start_us':0,'end_us':100}),('ffmpeg_args',['-y'])]:
    req=dict(base)
    if value is None:req.pop(field)
    else:req[field]=value
    rc,r=e.submit(req);assert rc==-1,(rc,r);rows.append({'request':req,'rc':rc})
   q=e.submit(control('queue_snapshot'))[1];assert q['active_count']==0 and not q['history'];assert not out.exists();return rows
  finally:assert e.destroy()==0
 def rejected_path(self,kind):
  src=self.a.fixtures/'pcm_s16le-44100-1.wav';out=self.a.work/(kind+'-output.wav');e=self.engine();old=sha256(src);target=None
  try:
   if kind=='same-source':out=src
   elif kind=='existing':out.write_bytes(b'owned target');target=out
   elif kind=='link':target=self.a.work/'link-target';target.write_bytes(b'owned target');out.symlink_to(target)
   elif kind=='bad-source':src=self.a.work/'bad-source.wav';src.write_bytes(b'not audio')
   elif kind=='permission':out.parent.joinpath('denied').mkdir();out=out.parent/'denied'/'preview.wav';out.parent.chmod(0o500)
   elif kind=='nan-source':
    src=self.a.work/'nan-source.wav';src.write_bytes((self.a.fixtures/'pcm_f32le-44100-1.wav').read_bytes());data=bytearray(src.read_bytes());pos=data.index(b'data')+8;data[pos:pos+4]=bytes.fromhex('0000c07f');src.write_bytes(data)
   rc,r=e.submit(self.request(src,out));assert rc==0,(rc,r)
   done=e.wait(r['job_id']) if 'job_id' in r else r
   assert (done.get('state')=='failed' or done.get('accepted') is False),done
   if kind!='same-source':assert not out.exists() or kind in ['existing','link']
   if target:assert target.read_bytes()==b'owned target'
   assert sha256(self.a.fixtures/'pcm_s16le-44100-1.wav')==old
   return {'response':done,'no_replacement':True}
  finally:
   if kind=='permission':out.parent.chmod(0o700)
   assert e.destroy()==0
 def wrapper(self,name,mode,target):
  directory=self.a.work/name;directory.mkdir();path=directory/'ffmpeg-qa.py';marker=directory/'marker.json';real=str(self.a.tools/'ffmpeg')
  path.write_text('#!'+sys.executable+'\n'+f'''import os,sys,json,time,pathlib,subprocess
args=sys.argv[1:]
real={real!r}
mode={mode!r}
marker=pathlib.Path({str(marker)!r})
target=pathlib.Path({str(target)!r})
encode=("pcm_f32le" in args and args[-1]!="-" and "wav" in args)
if not encode:os.execv(real,[real,*args])
p=subprocess.run([real,*args])
if p.returncode:sys.exit(p.returncode)
stage=pathlib.Path(args[-1])
marker.write_text(json.dumps({{"pid":os.getpid(),"stage":str(stage),"bytes":stage.stat().st_size}}))
if mode=="oversized":
 with stage.open("ab") as stream:stream.write(bytes(1048576))
 while True:time.sleep(.02)
if mode in ("hold","quarantine"):
 while True:time.sleep(.02)
if mode=="target-race":target.write_bytes(b"competing target")
if mode in ("nan","wrong-rate","wrong-count","wrong-content"):
 data=bytearray(stage.read_bytes());fmt=data.index(b"fmt ")+8;body=data.index(b"data")+8
 if mode=="nan":data[body:body+4]=bytes.fromhex("0000c07f")
 if mode=="wrong-rate":data[fmt+4:fmt+8]=(32000).to_bytes(4,"little")
 if mode=="wrong-count":data=data[:-4]
 if mode=="wrong-content":data[body:body+4]=bytes.fromhex("0000003f")
 stage.write_bytes(data)
''');path.chmod(0o700);return path,marker
 def fault(self,mode):
  src=self.a.fixtures/'libmp3lame-44100-1.mp3';out=self.a.work/('fault-'+mode+'.wav');wrapper,marker=self.wrapper(mode,'hold' if mode in ['cancel','timeout'] else mode,out)
  e=self.api.create(configuration(ffmpeg=wrapper,ffprobe=self.a.tools/'ffprobe',transcode_timeout_ms=1500 if mode=='timeout' else 600000))
  try:
   rc,r=e.submit(self.request(src,out));assert rc==0 and 'job_id' in r,(rc,r)
   if mode in ['cancel','timeout']:
    until=time.monotonic()+10
    while not marker.exists() and time.monotonic()<until:time.sleep(.005)
    assert marker.exists();witness=json.loads(marker.read_text());assert witness['bytes']>44 and Path(witness['stage']).is_file()
    start=time.monotonic()
    if mode=='cancel':assert e.cancel(r['job_id'])==0
   done=e.wait(r['job_id'])
   if mode=='cancel':assert done['state']=='cancelled' and time.monotonic()-start<=5,done
   else:
    assert done['state']=='failed',done
    assert done['error']['code']==('TIMEOUT' if mode=='timeout' else ('RESOURCE_LIMIT' if mode=='oversized' else ('OUTPUT_EXISTS' if mode=='target-race' else 'OUTPUT_VALIDATION_FAILED'))),done
   assert not out.exists() or (mode=='target-race' and out.read_bytes()==b'competing target')
   witness=json.loads(marker.read_text());assert not Path(witness['stage']).exists()
   try:os.kill(witness['pid'],0)
   except ProcessLookupError:pass
   else:raise AssertionError('Owned proxy worker PID still alive')
   return {'terminal':done,'witness':witness,'staging_removed':True}
  finally:assert e.destroy()==0
 def quarantine(self):
  src=self.a.fixtures/'libmp3lame-44100-1.mp3';out=self.a.work/'quarantine-output.wav';wrapper,marker=self.wrapper('quarantine','quarantine',out)
  e=self.api.create(configuration(ffmpeg=wrapper,ffprobe=self.a.tools/'ffprobe'));stage=None
  try:
   rc,r=e.submit(self.request(src,out));assert rc==0 and 'job_id' in r
   until=time.monotonic()+10
   while not marker.exists() and time.monotonic()<until:time.sleep(.005)
   assert marker.exists();w=json.loads(marker.read_text());stage=Path(w['stage']);assert stage.parent.parent==self.a.work.resolve() and stage.parent.name.startswith('.mediastudio-')
   assert stage.is_file() and stage.stat().st_size>44 and stage.parent.stat().st_uid==os.getuid()
   before=stage.stat();stage.parent.chmod(0o500);assert e.cancel(r['job_id'])==0
   done=e.wait(r['job_id']);assert done['state']=='failed' and done['error']['code']=='CLEANUP_FAILED',done
   rc,q=e.submit(control('queue_snapshot'));assert rc==0 and q['cleanup_unconfirmed'];assert e.destroy()==-7
   rc,denied=e.submit(self.request(src,self.a.work/'must-not-run.wav'));assert rc==0 and denied.get('accepted') is False
   assert not out.exists() and stage.exists()
   self.rows.append({'case':'cleanup-unconfirmed','result':'PASS','terminal':done,'queue':q,'reuse_refused':denied,'destroy_busy':True,'manual_teardown_is_not_product_cleanup':True});self.save()
  finally:
   if stage and stage.parent.parent==self.a.work.resolve():
    stage.parent.chmod(0o700)
    if stage.exists():
     now=stage.stat();assert now.st_ino==before.st_ino and now.st_dev==before.st_dev;stage.unlink()
    stage.parent.rmdir()
 def run(self):
  self.check('contract-profile-fields',self.contract)
  for src in sorted(self.a.fixtures.iterdir()):
   if src.suffix in ['.wav','.flac','.mp3','.m4a'] and src.stem.startswith(('pcm_','flac-','libmp3lame-','aac-')):
    self.check('preview-'+src.name,lambda s=src:self.success(s))
  for kind in ['same-source','existing','link','bad-source','permission','nan-source']:self.check(kind,lambda k=kind:self.rejected_path(k))
  for mode in ['nan','wrong-rate','wrong-count','wrong-content','target-race','cancel','timeout','oversized']:self.check('fault-'+mode,lambda m=mode:self.fault(m))
  child=self.a.work/'quarantine-child';evidence=self.a.evidence.with_name('core-proxy-quarantine.json')
  argv=[sys.executable,'-B',str(Path(__file__).resolve()),'--library',str(self.a.library),'--tools',str(self.a.tools),'--fixtures',str(self.a.fixtures),'--work',str(child),'--evidence',str(evidence),'--quarantine']
  p=subprocess.run(argv,capture_output=True,text=True,timeout=40);assert p.returncode==0,(p.returncode,p.stdout,p.stderr)
  q=json.loads(evidence.read_text());assert q['result']=='PASS';self.rows.append({'case':'isolated-cleanup-unconfirmed','result':'PASS','report':str(evidence)});self.save()
  assert all(r['result']=='PASS' for r in self.rows)

if __name__=='__main__':
 p=argparse.ArgumentParser()
 for n in ['library','work','evidence','tools','fixtures']:p.add_argument('--'+n,type=Path,required=True)
 p.add_argument('--quarantine',action='store_true');a=p.parse_args();a.library=a.library.resolve();a.tools=a.tools.resolve();a.work=a.work.resolve();a.fixtures=a.fixtures.resolve();a.evidence=a.evidence.resolve();s=Suite(a)
 if a.quarantine:s.quarantine()
 else:s.run()
