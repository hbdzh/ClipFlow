#!/usr/bin/env python3
"""Research comparison of upstream Open-Unmix weights and the fixed ONNX graph."""
import hashlib, json, sys
from pathlib import Path
import numpy as np
import onnxruntime as ort
import torch
from openunmix.model import OpenUnmix

def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
 return h.hexdigest()
original, converted=map(Path,sys.argv[1:3])
if sha(original) != 'b62c91cedbc7a066f1778ead5b5cecb377aa3a46a31af1cce7c5c8769339d083': raise ValueError('upstream weight hash mismatch')
if sha(converted) != 'f27742bb52b24cb039614dc76d0764e279df3eb2a9d6daa82c6daee3369ec747': raise ValueError('ONNX model hash mismatch')
torch.set_num_threads(2)
model=OpenUnmix(nb_bins=2049,nb_channels=2,hidden_size=512,max_bin=1487).eval()
state=torch.load(original,map_location='cpu',weights_only=True)
result=model.load_state_dict(state,strict=False)
if result.missing_keys or sorted(result.unexpected_keys)!=['sample_rate','stft.window','transform.0.window']:
 raise ValueError('unexpected weight mapping: '+str(result))
options=ort.SessionOptions(); options.intra_op_num_threads=2; options.inter_op_num_threads=1
session=ort.InferenceSession(str(converted),sess_options=options,providers=['CPUExecutionProvider'])
rng=np.random.default_rng(1201)
cases=[]
for frames in [57,152,234,863]:
 x=np.abs(rng.standard_normal((1,2,2049,frames))).astype(np.float32)
 with torch.inference_mode(): y=model(torch.from_numpy(x)).numpy()
 z=session.run(None,{'magnitude':x})[0]
 diff=np.abs(y-z); rms=float(np.sqrt(np.mean((y.astype(np.float64)-z.astype(np.float64))**2))); denom=float(np.sqrt(np.mean(y.astype(np.float64)**2)))
 cases.append({'frames':frames,'max_abs_diff':float(np.max(diff)),'rms_relative_error':rms/max(denom,1e-8),'upstream_output_sha256':hashlib.sha256(y.tobytes()).hexdigest(),'onnx_output_sha256':hashlib.sha256(z.tobytes()).hexdigest()})
print(json.dumps({'original_sha256':sha(original),'converted_sha256':sha(converted),'torch_version':torch.__version__,'onnxruntime_version':ort.__version__,'providers':session.get_providers(),'missing_keys':result.missing_keys,'ignored_non_network_keys':result.unexpected_keys,'seed':1201,'cases':cases},indent=2))
