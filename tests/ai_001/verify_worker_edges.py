#!/usr/bin/env python3
"""Independent accepted/rejected boundary checks of real streaming worker."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import numpy as np
import soundfile as sf
from verify_audio import check_audio, sha
ROOT=Path(__file__).resolve().parents[2]

def main():
    p=argparse.ArgumentParser()
    for field in ('python','worker','model','weights','one-second','thirty-second','temp-parent'):
        p.add_argument('--'+field,type=lambda s:Path(s).absolute(),required=True)
    a=p.parse_args()
    ffmpeg,ffprobe=shutil.which('ffmpeg'),shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:raise ValueError('missing ffmpeg')
    expected_worker=sha(a.worker); checks=[]
    with tempfile.TemporaryDirectory(prefix='ai001-worker-edges-',dir=a.temp_parent) as tmp:
        root=Path(tmp);root.chmod(0o700)
        def call(source,case):
            stage=root/case;stage.mkdir(mode=0o700)
            result=subprocess.run([str(a.python),str(a.worker),'--model',str(a.model),'--input',str(source),'--staging',str(stage)],
                                  stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=120)
            return stage,result,json.loads(result.stdout)
        for n in (2048,2049):
            samples=np.zeros((n,2),dtype='float32');samples[1024,:]=0.2
            src=root/f'frames-{n}.wav';sf.write(src,samples,44100,subtype='FLOAT')
            stage,proc,response=call(src,f'stage-{n}')
            if n==2048:
                if proc.returncode==0 or response.get('error')!='INVALID_INPUT' or list(stage.iterdir()):raise AssertionError('2048-frame input must be rejected before output')
                checks.append({'case':'2048-frame-rejection','result':'PASS','response':response})
            else:
                if proc.returncode or response.get('frames')!=n:raise AssertionError(('2049-frame failure',response))
                refs=[]
                for name in ('vocals','accompaniment'):
                    m=check_audio(stage/(name+'.wav'),ffprobe,ffmpeg)
                    if m['frames']!=n:raise AssertionError('frame count changed')
                    refs.append(m)
                out=subprocess.run([str(a.python),str(ROOT/'docs/ai-001/compare_audio_upstream.py'),str(a.weights),str(src),str(stage)],capture_output=True,text=True,check=True,timeout=120)
                parity=[json.loads(x) for x in out.stdout.splitlines() if x.strip()]
                if len(parity)!=2 or any(q['relative_rms_error']>1e-4 for q in parity):raise AssertionError(('2049 parity',parity))
                checks.append({'case':'2049-frame-pulse','result':'PASS','response':response,'output':refs,'torch_reference':parity})
        for label,src in [('one-second',a.one_second),('thirty-second',a.thirty_second)]:
            src_hash=sha(src);stage,proc,response=call(src,'stage-'+label)
            if proc.returncode:raise AssertionError((label,response))
            with sf.SoundFile(src) as audio:n=audio.frames
            if response.get('frames')!=n:raise AssertionError('worker reported different length')
            values=[check_audio(stage/(name+'.wav'),ffprobe,ffmpeg) for name in ('vocals','accompaniment')]
            if any(z['frames']!=n for z in values):raise AssertionError('incorrect decoded frames')
            out=subprocess.run([str(a.python),str(ROOT/'docs/ai-001/compare_audio_upstream.py'),str(a.weights),str(src),str(stage)],capture_output=True,text=True,check=True,timeout=120)
            parity=[json.loads(x) for x in out.stdout.splitlines() if x.strip()]
            if len(parity)!=2 or any(q['relative_rms_error']>1e-4 for q in parity):raise AssertionError((label,parity))
            if sha(src)!=src_hash:raise AssertionError('source changed')
            checks.append({'case':label,'result':'PASS','input_sha256':src_hash,'output':values,'torch_reference':parity})
        if sha(a.worker)!=expected_worker:raise AssertionError('worker drift')
    print(json.dumps({'result':'PASS','worker_sha256':expected_worker,'model_sha256':sha(a.model),'checks':checks,
                      'temporary_directory_removed':not root.exists(), 'not_run':['Core path','10 minute actual worker','human listening']},indent=2,allow_nan=False))
if __name__=='__main__':main()
