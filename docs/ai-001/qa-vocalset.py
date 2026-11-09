#!/usr/bin/env python3
"""Independent experiment preparation/evaluation; files remain in a local directory.

Usage: python qa-vocalset.py <solo.wav> <piano.wav> <output-dir> prepare
       python qa-vocalset.py <solo.wav> <piano.wav> <output-dir> assess
Inputs must be mono 44.1 kHz float/int WAV and nonzero. The probe itself is called separately.
"""
import hashlib,json,sys
from pathlib import Path
import numpy as np
import soundfile as sf
solo,piano,dst,mode=Path(sys.argv[1]),Path(sys.argv[2]),Path(sys.argv[3]),sys.argv[4]
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def data(p):
 v,s=sf.read(p,dtype='float32',always_2d=True)
 if s!=44100 or v.shape[1]!=1 or len(v)<4096 or not np.isfinite(v).all(): raise ValueError('expected finite mono 44.1k audio')
 return v[:,0]
if mode=='prepare':
 v=data(solo); a=data(piano)[:len(v)]
 if len(a)!=len(v): raise ValueError('piano shorter than voice')
 vr=np.sqrt(np.mean(v*v)); ar=np.sqrt(np.mean(a*a))
 if min(vr,ar)<1e-5: raise ValueError('quiet source')
 # Uncorrelated recordings; equal RMS, conservatively scale both to prevent clipping.
 a=a*(vr/ar); scale=0.8/max(float(np.max(np.abs(v+a))),1.0)
 voice=np.repeat((scale*v)[:,None],2,axis=1); accomp=np.repeat((scale*a)[:,None],2,axis=1); mix=voice+accomp
 dst.mkdir(parents=True,exist_ok=True)
 for name,arr in [('mix',mix),('voice',voice),('piano',accomp)]: sf.write(dst/(name+'.wav'),arr,44100,subtype='FLOAT')
 print(json.dumps({'solo_sha256':sha(solo),'piano_sha256':sha(piano),'frames':len(v),'solo_rms':float(vr),'piano_rms':float(ar),'gain':float(scale),'inputs':{n:sha(dst/(n+'.wav')) for n in ('mix','voice','piano')}},indent=2))
elif mode=='assess':
 r,s=sf.read(dst/'voice.wav',dtype='float64',always_2d=True); p,s2=sf.read(dst/'piano.wav',dtype='float64',always_2d=True)
 def score(reference,arr):
  r=reference.flatten(); x=arr.flatten(); alpha=np.dot(r,x)/np.dot(r,r); target=alpha*r; noise=x-target
  return {'si_sdr_db':float(10*np.log10(np.dot(target,target)/max(np.dot(noise,noise),1e-30))),'gain':float(alpha),'peak':float(np.max(np.abs(x)))}
 result={'frames':len(r),'sample_rate':s,'files':{}}
 for n,path,ref in [('mixed',dst/'mix.wav',r),('mixed_against_piano',dst/'mix.wav',p),('vocals',dst/'probe'/'vocals.wav',r),('accompaniment',dst/'probe'/'accompaniment.wav',p)]:
  arr,s3=sf.read(path,dtype='float64',always_2d=True)
  if arr.shape!=r.shape or s3!=s or not np.isfinite(arr).all(): raise ValueError('bad output')
  result['files'][n]={'sha256':sha(path),**score(ref,arr)}
 print(json.dumps(result,indent=2))
else: raise ValueError('invalid mode')
