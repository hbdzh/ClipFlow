#!/usr/bin/env python3
"""QA of the currently checked-in worker against three prebuilt reference mixes.

Run with the isolated test environment's Python. This creates fresh case output
in a NEW private directory; it does not modify the prebuilt fixtures or worker.
"""
import hashlib, json, os, subprocess, sys
from pathlib import Path
import numpy as np
import soundfile as sf

repo=Path(__file__).resolve().parents[2]
worker=repo/'docs/ai-001/worker/worker.py'
refscript=repo/'docs/ai-001/compare_audio_upstream.py'
fixture, model, weights, out=map(Path,sys.argv[1:5])
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
 return h.hexdigest()
def run(args):
 return subprocess.run([str(x) for x in args],check=True,capture_output=True,text=True)
def score(reference, estimate):
 r=reference.ravel().astype('float64'); x=estimate.ravel().astype('float64')
 target=(np.sum(r*x)/np.sum(r*r))*r; error=x-target
 return float(10*np.log10(np.sum(target*target)/np.sum(error*error)))
def load(p):
 x,s=sf.read(p,dtype='float32',always_2d=True)
 if s!=44100 or x.shape!=(132000,2) or not np.isfinite(x).all(): raise RuntimeError('unexpected WAV')
 return x
out.mkdir(mode=0o700)
start_worker=sha(worker); start_ref=sha(refscript)
result={'worker_sha256':start_worker,'comparison_script_sha256':start_ref,'qa_script_sha256':sha(Path(__file__)),'model_sha256':sha(model),'upstream_weights_sha256':sha(weights),'cases':[]}
for i in (1,3,5):
 src=fixture/('case-'+str(i)); stage=out/('case-'+str(i)); stage.mkdir(mode=0o700)
 response=run([sys.executable,worker,'--model',model,'--input',src/'mix.wav','--staging',stage])
 meta=json.loads(response.stdout)
 if meta.get('frames')!=132000 or meta.get('outputs')!=['vocals.wav','accompaniment.wav']: raise RuntimeError('unexpected worker response')
 voice=load(src/'voice.wav'); piano=load(src/'piano.wav'); mix=load(src/'mix.wav')
 row={'id':i,'input_sha256':sha(src/'mix.wav'),'voice_reference_sha256':sha(src/'voice.wav'),'piano_reference_sha256':sha(src/'piano.wav'),'worker_response':meta,'stems':{}}
 for name,ref in [('vocals',voice),('accompaniment',piano)]:
  p=stage/(name+'.wav'); signal=load(p)
  probe=json.loads(run(['ffprobe','-v','error','-show_entries','stream=sample_rate,channels,duration,codec_name','-of','json',p]).stdout)['streams'][0]
  run(['ffmpeg','-v','error','-i',p,'-f','null','-'])
  base=score(ref,mix); metric=score(ref,signal)
  row['stems'][name]={'sha256':sha(p),'ffprobe':probe,'full_decode':'PASS','baseline_si_sdr_db':base,'output_si_sdr_db':metric,'improvement_db':metric-base}
 parity=run([sys.executable,refscript,weights,src/'mix.wav',stage])
 row['torch_parity']=[json.loads(x) for x in parity.stdout.splitlines()]
 result['cases'].append(row)
if sha(worker)!=start_worker or sha(refscript)!=start_ref: raise RuntimeError('script changed during validation')
print(json.dumps(result,indent=2))
