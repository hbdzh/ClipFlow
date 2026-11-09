#!/usr/bin/env python3
"""Guarded local 600s repeated-authorized-sample experiment; NOT production code.

Run on macOS host with /bin/ps access. Polling thresholds are NOT hard limits.
"""
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import numpy as np
import soundfile as sf

RATE=44100
RSS_LIMIT=1536*1024*1024
DISK_LIMIT=1536*1024*1024
FREE_FLOOR=4*1024*1024*1024
DEADLINE=120
POLL=0.1
CHILD=Path(__file__).resolve().parent/'stream_worker_v1.py'
INITIAL_FREE=None

def sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def folder_size(root):
    return sum(p.stat().st_size for p in root.iterdir() if p.is_file())

WRAPPER = ("import os,resource,sys; "
           "resource.setrlimit(resource.RLIMIT_CPU,(120,120)); "
           "resource.setrlimit(resource.RLIMIT_FSIZE,(1073741824,1073741824)); "
           "os.execv(sys.executable,[sys.executable,*sys.argv[1:]])")

def deadline_alarm(signum, frame):
    raise TimeoutError('GLOBAL_WALL_TIMEOUT')

def check_space(root):
    if shutil.disk_usage(root).free<FREE_FLOOR: raise RuntimeError('FREE_FLOOR')
    if INITIAL_FREE is None or INITIAL_FREE-shutil.disk_usage(root).free>DISK_LIMIT: raise RuntimeError('DISK_LIMIT')

def ffprobe(path):
    p=subprocess.run(['/opt/homebrew/bin/ffprobe','-v','error','-show_entries','stream=codec_name,sample_rate,channels,duration','-of','json',str(path)],capture_output=True,text=True,timeout=60,check=True)
    return json.loads(p.stdout)['streams']

def decode(path):
    subprocess.run(['/opt/homebrew/bin/ffmpeg','-v','error','-i',str(path),'-f','null','-'],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=120,check=True)

def main():
    signal.signal(signal.SIGALRM, deadline_alarm); signal.alarm(240)
    global INITIAL_FREE
    model,source,temp_parent=map(Path,sys.argv[1:4]); os.umask(0o077)
    if not all(p.is_absolute() for p in (model,source,temp_parent)): raise RuntimeError('paths must be absolute')
    sample=sf.info(source)
    if (sample.frames,sample.samplerate,sample.channels,sample.subtype)!=(60*RATE,RATE,2,'FLOAT'):raise RuntimeError('unexpected 60 second source')
    if shutil.disk_usage(temp_parent).free<FREE_FLOOR+DISK_LIMIT: raise RuntimeError('INSUFFICIENT_INITIAL_FREE')
    model_sha=sha(model); sample_sha=sha(source); observation=[]; start_all=time.monotonic(); INITIAL_FREE=shutil.disk_usage(temp_parent).free
    with tempfile.TemporaryDirectory(prefix='ai001-tenmin-',dir=temp_parent) as td:
        root=Path(td)
        if root.stat().st_mode & 0o077: raise RuntimeError('INSECURE_TEMP')
        target=root/'repeated-input.wav'
        with sf.SoundFile(source) as original, sf.SoundFile(target,'w',samplerate=RATE,channels=2,format='WAV',subtype='FLOAT') as mix:
            for repeat in range(10):
                original.seek(0)
                for _ in range(60):
                    x=original.read(RATE,dtype='float32',always_2d=True)
                    if x.shape!=(RATE,2) or not np.isfinite(x).all():raise RuntimeError('BAD_SOURCE')
                    mix.write(x)
                    check_space(root)
        input_hash=sha(target); check_space(root)
        if sha(source)!=sample_sha or sha(model)!=model_sha:raise RuntimeError('SOURCE_OR_MODEL_CHANGED')
        stage=root/'stage'; stage.mkdir(mode=0o700)
        outlog=root/'child-stdout.txt'; errlog=root/'child-stderr.txt'
        rss_high=0; disk_high=INITIAL_FREE-shutil.disk_usage(root).free; failed=None; start=time.monotonic()
        with outlog.open('w') as out,errlog.open('w') as err:
            proc=subprocess.Popen([sys.executable,'-c',WRAPPER,str(CHILD),'--model',str(model),'--input',str(target),'--staging',str(stage)],stdin=subprocess.DEVNULL,stdout=out,stderr=err)
            try:
                while proc.poll() is None:
                    if time.monotonic()-start>DEADLINE: failed='TIME_LIMIT'; break
                    try:
                        check_space(root)
                        disk_high=max(disk_high,INITIAL_FREE-shutil.disk_usage(root).free)
                        p=subprocess.run(['/bin/ps','-o','rss=','-p',str(proc.pid)],capture_output=True,text=True,timeout=2)
                        if p.returncode or not p.stdout.strip():
                            if proc.poll() is None: failed='RSS_MONITOR_FAILED'
                            break
                        rss=int(p.stdout.strip())*1024; rss_high=max(rss_high,rss)
                        if rss>RSS_LIMIT:failed='RSS_LIMIT'; break
                    except Exception:
                        failed='MONITOR_FAILED'; break
                    time.sleep(POLL)
            finally:
                if proc.poll() is None:proc.kill()
                code=proc.wait(timeout=10)
        if failed: raise RuntimeError(failed)
        if code!=0: raise RuntimeError('CHILD_FAILED '+errlog.read_text()[-1000:])
        child=json.loads(outlog.read_text())
        if child.get('frames')!=RATE*600 or child.get('outputs')!=['vocals.wav','accompaniment.wav']: raise RuntimeError('BAD_RESPONSE')
        check_space(root); disk_high=max(disk_high,INITIAL_FREE-shutil.disk_usage(root).free)
        output={}
        for name in ['vocals.wav','accompaniment.wav']:
            p=stage/name; info=sf.info(p)
            if (info.frames,info.samplerate,info.channels,info.subtype)!=(RATE*600,RATE,2,'FLOAT'): raise RuntimeError('INVALID_OUTPUT')
            with sf.SoundFile(p) as f:
                while True:
                    a=f.read(RATE,dtype='float32',always_2d=True)
                    if len(a)==0:break
                    if not np.isfinite(a).all():raise RuntimeError('NONFINITE')
            output[name]={'sha256':sha(p),'probe':ffprobe(p)}
            decode(p)
        if sha(target)!=input_hash or sha(source)!=sample_sha or sha(model)!=model_sha:raise RuntimeError('SOURCE_OR_MODEL_CHANGED')
        with sf.SoundFile(target) as mix,sf.SoundFile(stage/'vocals.wav') as v,sf.SoundFile(stage/'accompaniment.wav') as a:
            max_err=0.0
            while True:
                x=mix.read(RATE,dtype='float32',always_2d=True)
                if len(x)==0:break
                y=v.read(len(x),dtype='float32',always_2d=True);z=a.read(len(x),dtype='float32',always_2d=True)
                max_err=max(max_err,float(np.max(np.abs((y+z)-x))))
        result={'test':'repeated 60s authorized mix ten times, synthetic 10-minute input, not natural full song','sample_sha256':sample_sha,'model_sha256':model_sha,'input_sha256':input_hash,'input_probe':ffprobe(target),'outputs':output,'source_frames':RATE*600,'child_result':child,'poll_interval_seconds':POLL,'rss_poll_high_bytes':rss_high,'rss_limit_bytes':RSS_LIMIT,'volume_free_delta_sampled_high_bytes':disk_high,'volume_free_delta_limit_bytes':DISK_LIMIT,'free_space_floor_bytes':FREE_FLOOR,'wall_limit_seconds':DEADLINE,'wall_seconds_total':time.monotonic()-start_all,'max_abs_vocals_plus_accompaniment_minus_input':max_err,'note':'RSS/free-volume-delta monitored at 100ms, NOT hard bounds; free delta includes unrelated concurrent volume writes and unlinked mmap files; excludes parent RSS; no whole-track independent reference, no AC21 claim. FSize and CPU are per-process limits.'}
    signal.alarm(0)
    result['private_temporary_directory_removed']=not root.exists()
    result['worker_sha256']=sha(CHILD)
    result['driver_sha256']=sha(Path(__file__))
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
