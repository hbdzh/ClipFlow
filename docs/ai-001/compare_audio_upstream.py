#!/usr/bin/env python3
"""Compare research worker WAV with upstream Torch model + centered Torch STFT."""
import hashlib,json,sys
from pathlib import Path
import numpy as np, soundfile as sf, torch
from openunmix.model import OpenUnmix

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
weight, source, output=map(Path,sys.argv[1:4])
if sha(weight)!='b62c91cedbc7a066f1778ead5b5cecb377aa3a46a31af1cce7c5c8769339d083': raise ValueError('upstream weight hash mismatch')
x,sr=sf.read(source,dtype='float32',always_2d=True)
y,osr=sf.read(output/'vocals.wav',dtype='float32',always_2d=True)
a,asr=sf.read(output/'accompaniment.wav',dtype='float32',always_2d=True)
if sr!=44100 or osr!=sr or asr!=sr or x.shape!=y.shape or x.shape!=a.shape or x.shape[1]!=2: raise ValueError('audio mismatch')
torch.set_num_threads(2)
model=OpenUnmix(nb_bins=2049,nb_channels=2,hidden_size=512,max_bin=1487).eval()
state=torch.load(weight,map_location='cpu',weights_only=True)
res=model.load_state_dict(state,strict=False)
if res.missing_keys or sorted(res.unexpected_keys)!=['sample_rate','stft.window','transform.0.window']: raise ValueError('weight mapping')
with torch.inference_mode():
 t=torch.from_numpy(x.T.copy()); w=torch.hann_window(4096,periodic=True)
 z=torch.stft(t,n_fft=4096,hop_length=1024,win_length=4096,window=w,center=True,pad_mode='reflect',return_complex=True)
 mag=torch.abs(z); v=model(mag.unsqueeze(0))[0]
 mask=(v / torch.clamp(mag,min=1e-8)).clamp(0,1)
 reference=torch.istft(z*mask,n_fft=4096,hop_length=1024,win_length=4096,window=w,center=True,length=len(x)).T.numpy()
other=x-reference
for label,got,expected in [('vocals',y,reference),('accompaniment',a,other)]:
 d=got.astype('float64')-expected.astype('float64'); base=expected.astype('float64')
 print(json.dumps({'stem':label,'input_sha256':sha(source),'output_sha256':sha(output/(label+'.wav')),'frames':len(x),'torch_stft_frames':mag.shape[-1],'max_abs_error':float(np.max(np.abs(d))),'relative_rms_error':float(np.sqrt(np.mean(d*d))/max(np.sqrt(np.mean(base*base)),1e-8))}))
