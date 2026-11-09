#!/usr/bin/env python3
"""Independent Core ABI 600-second fixture run; retain outputs for audition."""
import argparse, hashlib, json, os, platform, subprocess, sys, time
from pathlib import Path
import numpy as np
import soundfile as sf
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tests/core_004'))
from support import API, configuration

def digest(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()
def main():
    p=argparse.ArgumentParser()
    for n in ('library','python','worker','model','root'):p.add_argument('--'+n,type=lambda s:Path(s).absolute(),required=True)
    a=p.parse_args(); src=a.root/'source.wav'; dst=a.root/'result'
    assert src.is_file() and not dst.exists() and (a.root.stat().st_mode & 0o777)==0o700
    cfg=configuration(); cfg['media_backend']['ai_backend']={'python':str(a.python),'worker':str(a.worker),'model':str(a.model),'timeout_ms':600000,'development_root':str(a.root)}
    request={'schema_version':0,'operation':'ai_separate','source':{'uri':src.as_uri()},'destination':{'uri':dst.as_uri()},'model_id':'umx_hq_vocals_onnx_v1'}
    api=API(a.library); eng=api.create(cfg); started=time.monotonic(); source_hash=digest(src); peak=0; pids=set(); job=None
    result={'result':'FAIL','source_sha256':source_hash,'library_sha256':digest(a.library),'worker_sha256':digest(a.worker),'model_sha256':digest(a.model),'os':platform.platform(),'machine':platform.machine()}
    try:
        rc,rep=eng.submit(request)
        if rc != 0 or not rep or 'job_id' not in rep:raise RuntimeError((rc,rep))
        job=rep['job_id']; last=None
        while time.monotonic()-started<630:
            last=eng.snapshot(job)
            ps=subprocess.run(['/bin/ps','-axo','pid=,ppid=,rss=,command='],capture_output=True,text=True,check=True)
            for row in ps.stdout.splitlines():
                fields=row.strip().split(None,3)
                if len(fields)==4 and fields[1]==str(os.getpid()) and str(a.worker) in fields[3]:
                    pids.add(int(fields[0]));peak=max(peak,int(fields[2])*1024)
            if last['state'] in ('succeeded','failed','cancelled'):break
            time.sleep(0.1)
        result['terminal']=last;result['job_wall_seconds']=time.monotonic()-started;result['observed_worker_pids']=sorted(pids);result['sampled_worker_peak_rss_bytes']=peak
        if not last or last['state']!='succeeded' or not pids:raise RuntimeError('real job did not succeed or worker was not observed')
        paths=[dst/'vocals.wav',dst/'accompaniment.wav']; infos=[]
        for path in [src]+paths:
            probe=subprocess.run(['ffprobe','-v','error','-show_entries','stream=codec_name,sample_rate,channels,duration','-of','json',str(path)],capture_output=True,text=True,check=True)
            subprocess.run(['ffmpeg','-v','error','-i',str(path),'-f','null','-'],capture_output=True,check=True)
            infos.append({'sha256':digest(path),'probe':json.loads(probe.stdout)})
        maxerr=0.; peaks=[0.,0.]; n=0
        with sf.SoundFile(src) as s, sf.SoundFile(paths[0]) as v, sf.SoundFile(paths[1]) as m:
            assert s.samplerate==v.samplerate==m.samplerate==44100 and s.channels==v.channels==m.channels==2 and len(s)==len(v)==len(m)==26460000
            while True:
                x=s.read(65536,dtype='float32',always_2d=True); y=v.read(65536,dtype='float32',always_2d=True); z=m.read(65536,dtype='float32',always_2d=True)
                if len(x)==0:break
                assert x.shape==y.shape==z.shape and np.isfinite(y).all() and np.isfinite(z).all()
                maxerr=max(maxerr,float(np.max(np.abs(x-y-z))));peaks[0]=max(peaks[0],float(np.max(np.abs(y))));peaks[1]=max(peaks[1],float(np.max(np.abs(z))));n+=len(x)
        result.update({'frames':n,'input_and_outputs':infos,'reconstruction_max_abs_error':maxerr,'stem_peak':peaks,'source_unchanged':digest(src)==source_hash,'result':'PASS'})
    except Exception as e:
        result['error']=repr(e)
        if job:
            try:eng.cancel(job);eng.wait(job,timeout=30)
            except Exception as ce:result['cleanup_error']=repr(ce)
    finally:
        result['destroy_rc']=eng.destroy(); result['ffi_outputs']=api.outputs;result['ffi_frees']=api.frees
        if result['destroy_rc']!=0 or api.outputs!=api.frees:result['result']='FAIL'
        print(json.dumps(result,indent=2,allow_nan=False))
    sys.exit(0 if result['result']=='PASS' else 1)
if __name__=='__main__':main()
