#!/usr/bin/env python3
"""One 8-second research inference; fixed-length ONNX, not a production worker."""
import hashlib, json, resource, sys, time
from pathlib import Path
import numpy as np
import onnxruntime as ort
import soundfile as sf

model, source, target = map(Path, sys.argv[1:4])
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
 return h.hexdigest()
if sha(model) != '64a4f3bee48fbe7d971b23875adc924ed004c3533f49672592641dddc0f6f561': raise ValueError('model SHA-256 mismatch')
x,sr=sf.read(source,dtype='float32',always_2d=True)
if sr != 44100 or x.shape != (352800,2) or not np.isfinite(x).all(): raise ValueError('expected finite 8s 44100Hz stereo')
opts=ort.SessionOptions(); opts.intra_op_num_threads=2
session=ort.InferenceSession(str(model),sess_options=opts,providers=['CPUExecutionProvider'])
t0=time.perf_counter(); out=session.run(None,{session.get_inputs()[0].name:x.T[None].copy()})[0]; elapsed=time.perf_counter()-t0
if out.shape != (1,2,2,352800) or not np.isfinite(out).all(): raise ValueError('invalid output')
target.mkdir(parents=True,exist_ok=True)
for i,n in enumerate(('vocals','accompaniment')): sf.write(target/(n+'.wav'),out[0,i].T,sr,subtype='FLOAT')
print(json.dumps({'model_sha256':sha(model),'input_sha256':sha(source),'output_sha256':{n:sha(target/(n+'.wav')) for n in ('vocals','accompaniment')},'providers':session.get_providers(),'onnxruntime':ort.__version__,'duration_seconds':8,'sample_rate':sr,'input_shape':list(x.shape),'output_shape':list(out.shape),'inference_seconds':elapsed,'peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'max_abs_reconstruction_error':float(np.max(np.abs(out[0,0].T+out[0,1].T-x)))},indent=2))
