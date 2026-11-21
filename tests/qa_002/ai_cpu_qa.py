#!/usr/bin/env python3
"""QA-002 AC19 actual final signed Core/native CPU worker; no GUI claim."""
import argparse,hashlib,json,os,shutil,struct,subprocess,sys,threading,time,traceback
from pathlib import Path
import numpy as np
import soundfile as sf
ROOT=Path(__file__).resolve().parents[2]
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'tests/core_004'))
from support import API,configuration
if not __debug__:raise SystemExit('Assertions required; do not use -O')
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def write(p,d):Path(p).write_text(json.dumps(d,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def signal(n,rate=44100,channels=2,peak=.3):
 t=np.arange(n,dtype=np.float64)/rate;l=.12*np.sin(2*np.pi*347*t)+.04*np.sin(2*np.pi*691*t);r=.1*np.sin(2*np.pi*563*t)+.03*np.cos(2*np.pi*17*t);x=np.column_stack((l,r)) if channels==2 else l[:,None]
 if n:x[0,:]=peak;x[-1,:]=-peak
 return x.astype(np.float32)
class Suite:
 def __init__(self,a):
  self.a=a;self.test_script_sha256=sha(Path(__file__));self.work=a.work.resolve();self.work.mkdir(mode=0o700,parents=True,exist_ok=False);self.e=a.evidence.resolve();self.e.mkdir(parents=True,exist_ok=True);self.rows=[];self.commands=[];self.ffmpeg=Path('/opt/homebrew/bin/ffmpeg');self.ffprobe=Path('/opt/homebrew/bin/ffprobe');app=a.app.resolve();self.library=app/'Contents/Frameworks/libmedia_ffi.dylib';self.worker=a.worker.resolve();self.files=[self.library,self.worker,app/'Contents/Frameworks/libonnxruntime.dylib',app/'Contents/Resources/ai/model.onnx',*sorted((app/'Contents/Resources/ai/graphs').glob('*.onnx'))];self.before={str(p):sha(p) for p in self.files};self.api=API(self.library);cfg=configuration(ffmpeg=a.backend_ffmpeg or self.ffmpeg,ffprobe=a.backend_ffprobe or self.ffprobe);cfg['media_backend']['ai_backend']={'native_worker':str(self.worker),'native_graphs':str(app/'Contents/Resources/ai/graphs'),'native_runtime':str(app/'Contents/Frameworks/libonnxruntime.dylib'),'model':str(app/'Contents/Resources/ai/model.onnx'),'timeout_ms':600000,'development_root':str(self.work)};self.engine=self.api.create(cfg);self.config=cfg
 def cmd(self,argv,timeout=180,binary=False):
  r=subprocess.run(list(map(str,argv)),stdin=subprocess.DEVNULL,capture_output=True,timeout=timeout);self.commands.append({'argv':list(map(str,argv)),'exit':r.returncode,'stderr':r.stderr.decode(errors='replace'),'stdout_bytes':len(r.stdout),'stdout_sha256':hashlib.sha256(r.stdout).hexdigest()});assert r.returncode==0,self.commands[-1];return r.stdout if binary else r.stdout.decode()
 def ff(self,*args):return self.cmd([self.ffmpeg,'-nostdin','-v','error',*args],binary=True)
 def monitoring(self,stop,rows):
  while not stop.is_set():
   r=subprocess.run(['/bin/ps','-ww','-axo','pid=,ppid=,rss=,pcpu=,command='],capture_output=True,text=True)
   if r.returncode:rows.append({'error':'ps unavailable','exit':r.returncode});return
   for line in r.stdout.splitlines():
    v=line.strip().split(None,4)
    if len(v)==5 and v[1]==str(os.getpid()) and str(self.worker) in v[4]:rows.append({'pid':int(v[0]),'rss_bytes':int(v[2])*1024,'pcpu':float(v[3]),'command':v[4]})
   stop.wait(.03)
 def physical(self,p,n):
  probe=json.loads(self.cmd([self.ffprobe,'-v','error','-show_streams','-of','json',p]))['streams'];assert len(probe)==1 and probe[0]['codec_name']=='pcm_f32le' and probe[0]['sample_rate']=='44100' and probe[0]['channels']==2,probe
  with sf.SoundFile(p) as f:
   assert (f.samplerate,f.channels,f.subtype,len(f))==(44100,2,'FLOAT',n);peak=0.
   while True:
    x=f.read(65536,dtype='float32',always_2d=True)
    if not len(x):break
    assert np.isfinite(x).all();peak=max(peak,float(np.max(np.abs(x))))
  self.cmd([self.ffmpeg,'-nostdin','-v','error','-xerror','-i',p,'-map','0:a:0','-f','null','-']);return {'sha256':sha(p),'frames':n,'rate':44100,'channels':2,'codec':'pcm_f32le','peak_abs':peak,'full_decode_exit':0}
 def ok(self,source,n,reference,mono=False,silence=False,observe=False):
  dest=self.work/(source.stem+'-result');before=sha(source);started=time.monotonic();seen=[];stop=threading.Event();thread=threading.Thread(target=self.monitoring,args=(stop,seen)) if observe else None
  if thread:thread.start()
  try:
   rc,receipt=self.engine.submit({'schema_version':0,'operation':'ai_separate','source':{'uri':source.as_uri()},'destination':{'uri':dest.as_uri()},'model_id':'umx_hq_vocals_onnx_v1'});assert rc==0 and receipt and receipt.get('job_id'),(rc,receipt);terminal=self.engine.wait(receipt['job_id'],timeout=630);assert terminal['state']=='succeeded',terminal
  finally:
   stop.set()
   if thread:thread.join(timeout=2)
  assert sorted(p.name for p in dest.iterdir())==['accompaniment.wav','vocals.wav'];outputs={s:self.physical(dest/(s+'.wav'),n) for s in ['vocals','accompaniment']};error=first=last=mono_error=0.;count=0
  with sf.SoundFile(reference) as r,sf.SoundFile(dest/'vocals.wav') as v,sf.SoundFile(dest/'accompaniment.wav') as b:
   assert len(r)==len(v)==len(b)==n and r.channels==2
   while True:
    x=r.read(65536,dtype='float32',always_2d=True);y=v.read(65536,dtype='float32',always_2d=True);z=b.read(65536,dtype='float32',always_2d=True);assert x.shape==y.shape==z.shape
    if not len(x):break
    combined=y.astype(np.float64)+z.astype(np.float64);err=np.abs(combined-x.astype(np.float64));error=max(error,float(err.max()))
    if count==0:first=float(err[0].max())
    last=float(err[-1].max());count+=len(x)
    if mono:mono_error=max(mono_error,float(np.max(np.abs(combined[:,0]-combined[:,1]))))
  assert count==n and error<=3e-6,(count,error)
  if mono:assert mono_error<=3e-6,mono_error
  if silence:assert max(x['peak_abs'] for x in outputs.values())<=1e-6,outputs
  if observe:assert any('pid' in x for x in seen),seen
  assert sha(source)==before and not list(self.work.glob('.mediastudio-ai-*'))
  return {'source_sha256':before,'receipt':receipt,'terminal':terminal,'elapsed_seconds':time.monotonic()-started,'outputs':outputs,'reconstruction_max_error':error,'first_error':first,'last_error':last,'mono_unity_copy_error':mono_error if mono else None,'source_unchanged':True,'worker_observed':{'pids':sorted({x['pid'] for x in seen if 'pid' in x}),'sampled_peak_rss_bytes':max([x['rss_bytes'] for x in seen if 'rss_bytes' in x] or [0]),'samples':seen} if observe else None}
 def reject(self,source):
  dest=self.work/(source.stem+'-rejected-result');before=sha(source);rc,receipt=self.engine.submit({'schema_version':0,'operation':'ai_separate','source':{'uri':source.as_uri()},'destination':{'uri':dest.as_uri()},'model_id':'umx_hq_vocals_onnx_v1'});terminal=None
  if receipt and receipt.get('job_id'):terminal=self.engine.wait(receipt['job_id'],timeout=60);assert terminal['state']=='failed',terminal
  else:assert rc==0 and receipt and receipt.get('accepted') is False,(rc,receipt)
  assert not dest.exists() and sha(source)==before and not list(self.work.glob('.mediastudio-ai-*'));return {'source_sha256':before,'receipt':receipt,'terminal':terminal,'source_unchanged':True,'no_result_or_staging':True}
 def case(self,name,fn):
  try:self.rows.append({'case':name,'result':'PASS','detail':fn()})
  except Exception:self.rows.append({'case':name,'result':'FAIL','error':traceback.format_exc()})
  print(name,self.rows[-1]['result'],flush=True);write(self.e/'cpu-results-in-progress.json',{'cases':self.rows})
 def matrix(self):
  base=self.work/'matrix-base.wav';sf.write(base,signal(132300),44100,subtype='FLOAT')
  for codec,ext in [('pcm_s16le','wav'),('pcm_s24le','wav'),('pcm_f32le','wav'),('flac','flac'),('libmp3lame','mp3'),('aac','m4a')]:
   for rate in (44100,48000):
    for channels in (1,2):
     name=f'matrix-{codec}-{rate}-{channels}';src=self.work/(name+'.'+ext);self.ff('-i',base,'-ar',str(rate),'-ac',str(channels),'-c:a',codec,src)
     def run(src=src,rate=rate,channels=channels):
      native=np.frombuffer(self.ff('-i',src,'-map','0:a:0','-c:a','pcm_f32le','-f','f32le','pipe:1'),dtype='<f4');assert len(native)%channels==0;frames=len(native)//channels;expected=(frames*44100+rate//2)//rate;filt='pan=stereo|c0=c0|c1=c0,aresample=44100' if channels==1 else 'aresample=44100';normalized=np.frombuffer(self.ff('-i',src,'-map','0:a:0','-af',filt,'-ar','44100','-ac','2','-c:a','pcm_f32le','-f','f32le','pipe:1'),dtype='<f4').reshape(-1,2);assert abs(len(normalized)-expected)<=1;adjustment=expected-len(normalized)
      if adjustment>0:normalized=np.pad(normalized,((0,adjustment),(0,0)))
      ref=self.work/(src.stem+'-reference.wav');sf.write(ref,normalized[:expected],44100,subtype='FLOAT');d=self.ok(src,expected,ref,mono=channels==1);d.update(decoded_source_frames=frames,expected_frames=expected,reference_tail_adjustment=adjustment);return d
     self.case(name,run)
 def durations(self):
  for sec in [1,30,600]:
   src=self.work/f'duration-{sec}.wav'
   with sf.SoundFile(src,'w',samplerate=44100,channels=2,subtype='FLOAT') as f:
    block=signal(44100)
    for _ in range(sec):f.write(block)
   self.case(f'duration-{sec}',lambda src=src,sec=sec:self.ok(src,sec*44100,src,observe=True))
  for sec in [1,30]:
   src=self.work/f'silence-{sec}.wav';sf.write(src,np.zeros((sec*44100,2),np.float32),44100,subtype='FLOAT');self.case(f'silence-{sec}',lambda src=src,sec=sec:self.ok(src,sec*44100,src,silence=True))
 def boundaries(self):
  for n,valid in [(0,False),(2048,False),(2049,True),(26460001,False)]:
   src=self.work/f'frames-{n}.wav'
   if n>1000000:
    size=n*8
    with src.open('wb') as f:f.write(struct.pack('<4sI4s4sIHHIIHH4sI',b'RIFF',36+size,b'WAVE',b'fmt ',16,3,2,44100,352800,8,32,b'data',size));f.truncate(44+size)
   else:sf.write(src,signal(n),44100,subtype='FLOAT')
   self.case(f'frames-{n}',lambda src=src,n=n,valid=valid:self.ok(src,n,src) if valid else self.reject(src))
  for value,name,valid in [(1.4,'headroom',True),(2.,'exact-two',True),(float(np.nextafter(np.float32(2),np.float32(np.inf))),'nextafter-two',False),(float('nan'),'nan',False),(float('inf'),'inf',False),(-float('inf'),'minus-inf',False)]:
   src=self.work/f'peak-{name}.wav';x=signal(44100);x[2048,0]=value;sf.write(src,x,44100,subtype='FLOAT');self.case(f'peak-{name}',lambda src=src,valid=valid:self.ok(src,44100,src) if valid else self.reject(src))
  for rate,channels in [(32000,2),(96000,2),(44100,6)]:
   src=self.work/f'unsupported-{rate}-{channels}.wav';self.ff('-f','lavfi','-i',f'anullsrc=r={rate}:cl='+('5.1' if channels==6 else 'stereo'),'-t','1','-c:a','pcm_s16le',src);self.case(f'unsupported-{rate}-{channels}',lambda src=src:self.reject(src))
  if (self.work/'duration-1.wav').exists():
   src=self.work/'truncated.wav';src.write_bytes((self.work/'duration-1.wav').read_bytes()[:100]);self.case('truncated',lambda:self.reject(src))
  src=self.work/'over-600-48k.flac';self.ff('-f','lavfi','-i','anullsrc=r=48000:cl=mono','-t','600.5','-c:a','flac',src);self.case('normalized-over-600',lambda:self.reject(src))
 def extra_boundaries(self):
  base=self.work/'extra-base.wav';sf.write(base,signal(44100),44100,subtype='FLOAT')
  valid=self.work/'wavex-valid.wav';sf.write(valid,signal(44100),44100,format='WAVEX',subtype='FLOAT');self.case('wavex-valid',lambda:self.ok(valid,44100,valid))
  invalid=self.work/'wavex-back-mask.wav';data=bytearray(valid.read_bytes());fmt=data.index(b'fmt ');assert struct.unpack_from('<H',data,fmt+8)[0]==0xfffe;struct.pack_into('<I',data,fmt+28,0x30);invalid.write_bytes(data);self.case('wavex-back-mask',lambda:self.reject(invalid))
  for codec in ['pcm_u8','pcm_f64le']:
   source=self.work/(codec+'.wav');self.ff('-i',base,'-c:a',codec,source);self.case(codec+'-unsupported',lambda source=source:self.reject(source))
  multi=self.work/'two-audio.m4a';self.ff('-i',base,'-map','0:a:0','-map','0:a:0','-c:a','aac',multi);self.case('two-audio-streams',lambda:self.reject(multi))
  bad=self.work/'not-media.wav';bad.write_bytes(b'not audio');self.case('not-media',lambda:self.reject(bad))
  for frames,expected,valid in [(2229,2048,False),(2230,2049,True)]:
   source=self.work/f'normalized-min-{frames}.wav';sf.write(source,signal(frames,48000,1),48000,subtype='PCM_16')
   def check(source=source,expected=expected,valid=valid):
    if not valid:return self.reject(source)
    x=np.frombuffer(self.ff('-i',source,'-af','pan=stereo|c0=c0|c1=c0,aresample=44100','-ar','44100','-ac','2','-c:a','pcm_f32le','-f','f32le','pipe:1'),dtype='<f4').reshape(-1,2);assert abs(len(x)-expected)<=1
    if len(x)<expected:x=np.pad(x,((0,expected-len(x)),(0,0)))
    reference=self.work/f'reference-min-{expected}.wav';sf.write(reference,x[:expected],44100,subtype='FLOAT');return self.ok(source,expected,reference,mono=True)
   self.case(f'normalized-min-{frames}',check)
 def finish(self):
  rc=self.engine.destroy();assert rc==0,rc;stable=self.before=={str(p):sha(p) for p in self.files};assert stable and self.api.outputs==self.api.frees and sha(Path(__file__))==self.test_script_sha256;d={'scope':'Actual final signed Core via C ABI; code/data-identical final native CPU worker with test-only non-inherit signature, final graph/model/runtime bytes; not sandbox GUI or quality/listening acceptance','test_script_sha256':self.test_script_sha256,'config':self.config,'component_hashes':self.before,'component_bytes_unchanged':stable,'cases':self.rows,'commands':self.commands,'ffi_outputs':self.api.outputs,'ffi_frees':self.api.frees,'result':'PASS' if all(x['result']=='PASS' for x in self.rows) else 'FAIL'};write(self.e/'cpu-results.json',d);return d['result']=='PASS'
def main():
 p=argparse.ArgumentParser();p.add_argument('--app',required=True,type=Path);p.add_argument('--worker',required=True,type=Path);p.add_argument('--work',required=True,type=Path);p.add_argument('--evidence',required=True,type=Path);p.add_argument('--only',nargs='+',default=['matrix','durations','boundaries']);p.add_argument('--backend-ffmpeg',type=Path);p.add_argument('--backend-ffprobe',type=Path);a=p.parse_args();s=Suite(a)
 try:
  for group in a.only:getattr(s,group)()
  good=s.finish()
 except BaseException:
  for job in s.engine.jobs:
   try:s.engine.cancel(job);s.engine.wait(job,timeout=30)
   except Exception:pass
  s.engine.destroy();raise
 raise SystemExit(0 if good else 1)
if __name__=='__main__':main()
