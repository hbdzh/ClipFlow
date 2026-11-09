#!/usr/bin/env python3
"""QA-only independent Torch STFT checks at stream chunk and global boundaries."""
import hashlib, importlib.util, json, sys
from pathlib import Path
import numpy as np
import soundfile as sf
import torch
script,audio=map(Path,sys.argv[1:3])
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
expected=sha(script)
spec=importlib.util.spec_from_file_location('candidate_stream_qa',script)
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
x,sr=sf.read(audio,dtype='float32',always_2d=True)
if sr!=44100 or x.shape!=(2646000,2): raise ValueError('expected stereo 60s 44.1k')
torch.set_num_threads(2)
with torch.inference_mode():
 z=torch.stft(torch.from_numpy(x.T.copy()),n_fft=4096,hop_length=1024,win_length=4096,window=torch.hann_window(4096,periodic=True),center=True,pad_mode='reflect',return_complex=True).numpy()
indices=sorted({0,1,z.shape[-1]-2,z.shape[-1]-1} | {i for edge in range(256,z.shape[-1],256) for i in (edge-1,edge)})
result=[]
with sf.SoundFile(audio) as f:
 for k in indices:
  begin=k//256*256; end=min(z.shape[-1],begin+256)
  got=m.spectrum(f,len(x),begin,end)[:,:,k-begin]; ref=z[:,:,k]
  d=got.astype('complex128')-ref.astype('complex128')
  result.append({'frame':k,'block_first':begin,'block_last_exclusive':end,'max_abs':float(np.max(np.abs(d))),'relative_rms':float(np.sqrt(np.mean(np.abs(d)**2))/max(np.sqrt(np.mean(np.abs(ref)**2)),1e-8))})
if sha(script)!=expected: raise RuntimeError('script changed during check')
print(json.dumps({'script_sha256':expected,'qa_script_sha256':sha(Path(__file__)),'input_sha256':sha(audio),'reference':'PyTorch CPU stft centered reflect Hann','frame_count':z.shape[-1],'checks':result},indent=2))
