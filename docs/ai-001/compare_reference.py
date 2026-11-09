#!/usr/bin/env python3
"""Compare stereo WAV against an aligned reference; SI-SDR has no quality threshold."""
import hashlib, json, sys
from pathlib import Path
import numpy as np
import soundfile as sf

def load(p):
    x, sr = sf.read(p, dtype='float64', always_2d=True)
    if not np.isfinite(x).all(): raise ValueError('nonfinite audio')
    return x, sr
ref_path, mix_path, estimate_path = map(Path, sys.argv[1:4])
r, sr = load(ref_path)
result = {'reference_sha256':hashlib.sha256(ref_path.read_bytes()).hexdigest(), 'sample_rate':sr, 'frames':len(r)}
for label, path in [('mixture',mix_path),('estimate',estimate_path)]:
    x, rate = load(path)
    if rate != sr or x.shape != r.shape: raise ValueError('sample rate or shape mismatch')
    a = r.ravel(); b = x.ravel(); den = np.dot(a,a)
    if den < 1e-20: raise ValueError('silent reference')
    gain = np.dot(a,b)/den
    target = gain*a; noise = b-target
    result[label] = {'sha256':hashlib.sha256(path.read_bytes()).hexdigest(), 'si_sdr_db':float(10*np.log10(np.dot(target,target)/max(np.dot(noise,noise),1e-30))), 'projection_gain':float(gain), 'peak':float(np.max(np.abs(x)))}
print(json.dumps(result,indent=2))
