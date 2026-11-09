#!/usr/bin/env python3
"""Diagnose whether source tracks reconstruct the 45–65 second OnAir master."""
import hashlib,json,sys
from pathlib import Path
import numpy as np
import soundfile as sf
root=Path(sys.argv[1]); source=root/'Mix.wav'
names=['Vox.wav','Chords.wav','Guitar.wav','Piano.wav','Bass.wav','Kick.wav','Snare.wav','Build Drums.wav','Break Drums.wav','FX, Ambience, Drones.wav']
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
 return h.hexdigest()
def load(path):
 x,s=sf.read(path,start=45*44100,stop=65*44100,dtype='float32',always_2d=True)
 if s!=44100 or x.shape!=(882000,2): raise ValueError('unexpected source')
 return x[::16].ravel().astype('float64')
x=load(source); columns=[]; recorded=[]; excluded=[]
for name in names:
 y=load(root/name); rms=float(np.sqrt(np.mean(y*y))); d={'name':name,'sha256':sha(root/name),'rms':rms}
 if rms<0.0001: excluded.append(d)
 else: columns.append(y); recorded.append(d)
A=np.stack(columns,axis=1); coef=np.linalg.lstsq(A,x,rcond=None)[0]; err=x-np.sum(A*coef,axis=1)
print(json.dumps({'method':'ordinary least squares on stereo samples every 16 frames; no phase/time-varying processing or intercept','start_seconds':45,'duration_seconds':20,'master_sha256':sha(source),'master_rms':float(np.sqrt(np.mean(x*x))),'active_stems':[dict(r,coefficient=float(c)) for r,c in zip(recorded,coef)],'excluded_near_silent_stems':excluded,'linear_variance_explained':float(1-np.mean(err*err)/np.mean(x*x)),'residual_rms':float(np.sqrt(np.mean(err*err)))},indent=2))
