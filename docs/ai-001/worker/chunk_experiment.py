#!/usr/bin/env python3
"""UMX chunk-context experiment: reference is full-track same ONNX, not independent QA.

Research only. Entire reference and STFT reside in RAM. Not a streaming worker.
"""
import hashlib
import json
import resource
import sys
import time
from pathlib import Path
import numpy as np
import onnxruntime as ort
import soundfile as sf

model, audio = map(Path, sys.argv[1:3])
expected = 'f27742bb52b24cb039614dc76d0764e279df3eb2a9d6daa82c6daee3369ec747'
if hashlib.sha256(model.read_bytes()).hexdigest() != expected:
    raise SystemExit('bad model hash')
x, sr = sf.read(audio, dtype='float32', always_2d=True)
if sr != 44100 or x.shape[1] != 2 or not np.isfinite(x).all():
    raise SystemExit('bad input')
window = np.hanning(4097)[:-1].astype('float32')
padded = np.pad(x.T, ((0, 0), (2048, 2048)), mode='reflect')
rem = (padded.shape[1]-4096) % 1024
if rem:
    padded = np.pad(padded, ((0, 0), (0, 1024-rem)))
frames = np.lib.stride_tricks.sliding_window_view(padded, 4096, axis=-1)[:, ::1024, :]
spectrum = np.fft.rfft(frames*window, axis=-1).transpose(0, 2, 1)
mag = np.abs(spectrum)[None].astype('float32')
options = ort.SessionOptions()
options.intra_op_num_threads = 2
options.inter_op_num_threads = 1
options.log_severity_level = 3
session = ort.InferenceSession(str(model), sess_options=options, providers=['CPUExecutionProvider'])
assert session.get_providers() == ['CPUExecutionProvider']
total = mag.shape[-1]

def infer(a, b):
    return session.run(None, {session.get_inputs()[0].name: np.ascontiguousarray(mag[..., a:b])})[0]

def reconstruct(y):
    mask = np.clip(y[0]/np.maximum(mag[0], 1e-8), 0, 1)
    chunks = np.fft.irfft((spectrum*mask).transpose(0,2,1), n=4096, axis=-1)*window
    v = np.zeros_like(padded)
    norm = np.zeros(padded.shape[1], dtype='float32')
    for i in range(chunks.shape[1]):
        j=i*1024
        v[:,j:j+4096] += chunks[:,i,:]
        norm[j:j+4096] += window*window
    v /= np.maximum(norm, 1e-8)
    return v[:,2048:2048+len(x)].T.copy()

def score(actual, ref, boundaries):
    d=actual.astype('float64')-ref
    denominator=max(float(np.sqrt(np.mean(ref.astype('float64')**2))), 1e-8)
    per=[]
    for f in boundaries:
        p=f*1024
        if p >= len(actual): continue
        lo=max(0,p-4410); hi=min(len(actual),p+4410)
        q=d[lo:hi]
        per.append({'sample':p,'peak_abs_difference_100ms':float(np.max(np.abs(q))), 'rms_difference_100ms':float(np.sqrt(np.mean(q*q))), 'difference_jump_at_boundary':float(np.max(np.abs(d[p]-d[p-1])))})
    return {'normalized_rms_error':float(np.sqrt(np.mean(d*d))/denominator), 'peak_abs_difference':float(np.max(np.abs(d))), 'reference_rms':denominator,'boundary_windows':per}

start=time.perf_counter()
whole = infer(0,total)
ref = reconstruct(whole)
whole_seconds=time.perf_counter()-start
del whole
# Frame windows of 256 frames with overlap 64, step 192; weights sum to one.
width=256; overlap=64; step=width-overlap
results=[]
for ctx in (0, 43, 129, 215, 430, 860, 1290, total):
    begin=time.perf_counter()
    prediction=np.zeros_like(mag)
    weight=np.zeros(total,dtype='float32')
    boundaries=[]
    for first in range(0,total,step):
        end=min(total, first+width)
        left=max(0,first-ctx); right=min(total,end+ctx)
        y=infer(left,right)[..., first-left:end-left]
        w=np.ones(end-first,dtype='float32')
        k=min(overlap,len(w))
        if first:
            w[:k]=np.sin((np.arange(k,dtype='float32')+0.5)*np.pi/(2*k))**2
            boundaries.append(first)
        if end<total:
            w[-k:]=np.cos((np.arange(k,dtype='float32')+0.5)*np.pi/(2*k))**2
        prediction[...,first:end] += y*w
        weight[first:end] += w
    prediction /= weight
    vocal=reconstruct(prediction)
    accompaniment=x-vocal
    ref_accompaniment=x-ref
    results.append({'context_frames_each_side':ctx, 'context_seconds_each_side':ctx*1024/sr, 'seconds_including_inference_and_reconstruction':time.perf_counter()-begin,'vocal':score(vocal,ref,boundaries),'accompaniment':score(accompaniment,ref_accompaniment,boundaries)})
    del prediction, vocal, accompaniment, ref_accompaniment
result={'model_sha256':expected,'audio_sha256':hashlib.sha256(audio.read_bytes()).hexdigest(),'source_frames':len(x),'sample_rate':sr,'source_seconds':len(x)/sr,'stft_frames':total,'provider':session.get_providers(),'onnxruntime':ort.__version__,'whole_reference_seconds':whole_seconds,'chunk_frames':width,'overlap_frames':overlap,'step_frames':step,'context_comparison':results,'peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'notes':'Reference is full-sequence same ONNX implementation, not independent CPU reference. Experiment computes global STFT, not bounded memory streaming; edge alignment and chunk-signal extraction not validated.'}
print(json.dumps(result,indent=2))
