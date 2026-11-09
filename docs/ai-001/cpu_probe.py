#!/usr/bin/env python3
"""Research-only CPU probe for the pinned Open-Unmix ONNX; not a product worker."""
import hashlib, json, resource, sys, time
from pathlib import Path
import numpy as np
import onnxruntime as ort
import soundfile as sf

model, source, output = map(Path, sys.argv[1:4])
if hashlib.sha256(model.read_bytes()).hexdigest() != 'f27742bb52b24cb039614dc76d0764e279df3eb2a9d6daa82c6daee3369ec747':
    raise ValueError('model SHA-256 mismatch')
x, sr = sf.read(source, dtype='float32', always_2d=True)
if sr != 44100 or x.shape[1] != 2 or len(x) <= 2048:
    raise ValueError('expected stereo 44100 Hz audio longer than 2048 samples')
win = np.hanning(4097)[:-1].astype('float32')
padded = np.pad(x.T, ((0, 0), (2048, 2048)), mode='reflect')
remainder = (padded.shape[1] - 4096) % 1024
if remainder: padded = np.pad(padded, ((0, 0), (0, 1024 - remainder)))
frames = np.lib.stride_tricks.sliding_window_view(padded, 4096, axis=-1)[:, ::1024, :]
z = np.fft.rfft(frames * win, axis=-1).transpose(0, 2, 1)
mag = np.abs(z)[None].astype('float32')
opts = ort.SessionOptions()
opts.intra_op_num_threads = 2
session = ort.InferenceSession(str(model), sess_options=opts, providers=['CPUExecutionProvider'])
t0 = time.perf_counter()
y = session.run(None, {session.get_inputs()[0].name: mag})[0]
elapsed = time.perf_counter() - t0
if y.shape != mag.shape or not np.isfinite(y).all(): raise ValueError('invalid model output')
mask = np.clip(y[0] / np.maximum(mag[0], 1e-8), 0, 1)
chunks = np.fft.irfft((z * mask).transpose(0, 2, 1), n=4096, axis=-1) * win
vocal = np.zeros_like(padded)
norm = np.zeros(padded.shape[1], dtype='float32')
for i in range(chunks.shape[1]):
    pos = i * 1024
    vocal[:, pos:pos+4096] += chunks[:, i, :]
    norm[pos:pos+4096] += win * win
vocal /= np.maximum(norm, 1e-8)
vocal = vocal[:, 2048:2048+len(x)].T
if vocal.shape != x.shape or not np.isfinite(vocal).all(): raise ValueError('invalid reconstructed audio')
accomp = x - vocal
output.mkdir(parents=True, exist_ok=True)
for name, data in [('vocals', vocal), ('accompaniment', accomp)]: sf.write(output / (name + '.wav'), data, sr, subtype='FLOAT')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
print(json.dumps({'model_sha256':sha(model),'input_sha256':sha(source),'output_sha256':{n:sha(output / (n+'.wav')) for n in ['vocals','accompaniment']},'input_shape':list(x.shape),'sample_rate':sr,'duration_seconds':len(x)/sr,'output_shape':list(vocal.shape),'providers':session.get_providers(),'onnxruntime':ort.__version__,'model_input':session.get_inputs()[0].name,'model_input_shape':list(mag.shape),'inference_seconds':elapsed,'peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'max_abs_reconstruction_error':float(np.max(np.abs(vocal+accomp-x))),'vocals_rms':float(np.sqrt(np.mean(vocal**2))),'accompaniment_rms':float(np.sqrt(np.mean(accomp**2)))},indent=2))
