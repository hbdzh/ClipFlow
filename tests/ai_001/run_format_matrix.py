#!/usr/bin/env python3
"""Check exact development ABI support and required AC19 formats with real generated fixtures."""
import argparse,hashlib,json,os,shutil,subprocess,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'tests/core_004'))
from support import API,configuration

def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for block in iter(lambda:f.read(1048576),b''):h.update(block)
 return h.hexdigest()
def main():
 p=argparse.ArgumentParser()
 for n in ('library','python','worker','model','source','temp-parent'):p.add_argument('--'+n,type=lambda s:Path(s).absolute(),required=True)
 a=p.parse_args();temp=Path(tempfile.mkdtemp(prefix='ai001-format-',dir=a.temp_parent)).resolve();os.chmod(temp,0o700);api=API(a.library)
 cfg=configuration();cfg['media_backend']['ai_backend']={'python':str(a.python),'worker':str(a.worker),'model':str(a.model),'timeout_ms':600000,'development_root':str(temp)}
 e=api.create(cfg);rows=[]
 try:
  for codec,ext in [('pcm_s16le','wav'),('pcm_s24le','wav'),('pcm_f32le','wav'),('flac','flac'),('libmp3lame','mp3'),('aac','m4a')]:
   for rate in (44100,48000):
    for channels in (1,2):
     name=f'{codec}-{rate}-{channels}';src=temp/(name+'.'+ext);dst=temp/(name+'-result')
     cmd=['ffmpeg','-v','error','-i',str(a.source),'-t','3','-ar',str(rate),'-ac',str(channels),'-c:a',codec,'-y',str(src)]
     subprocess.run(cmd,check=True,capture_output=True)
     probe=json.loads(subprocess.run(['ffprobe','-v','error','-show_entries','stream=codec_name,sample_rate,channels,duration','-of','json',str(src)],check=True,capture_output=True,text=True).stdout)
     before=sha(src);req={'schema_version':0,'operation':'ai_separate','source':{'uri':src.as_uri()},'destination':{'uri':dst.as_uri()},'model_id':'umx_hq_vocals_onnx_v1'}
     rc,resp=e.submit(req); row={'case':name,'input_sha256':before,'probe':probe,'ffi_rc':rc}
     if resp and resp.get('job_id'):
      final=e.wait(resp['job_id'],timeout=180);row['state']=final['state'];row['error']=final.get('error')
      if final['state']=='succeeded':
       for stem in ('vocals.wav','accompaniment.wav'):
        subprocess.run(['ffmpeg','-v','error','-i',str(dst/stem),'-f','null','-'],check=True,capture_output=True)
       row['both_stems_full_decode']='PASS'
     else:row['rejection']=resp
     row['source_unchanged']=sha(src)==before;rows.append(row)
  out={'library_sha256':sha(a.library),'worker_sha256':sha(a.worker),'model_sha256':sha(a.model),'fixture_source_sha256':sha(a.source),'cases':rows,'ffi_outputs':api.outputs,'ffi_frees':api.frees}
 finally:
  rc=e.destroy();out['destroy_rc']=rc;out['ffi_outputs']=api.outputs;out['ffi_frees']=api.frees
  if rc==0:shutil.rmtree(temp)
  else:out['retained_root']=str(temp)
 print(json.dumps(out,indent=2,allow_nan=False))
 if out['destroy_rc'] or out['ffi_outputs']!=out['ffi_frees']:sys.exit(1)
if __name__=='__main__':main()
