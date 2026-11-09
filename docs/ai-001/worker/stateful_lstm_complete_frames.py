#!/usr/bin/env python3
"""Research only: split UMX BiLSTM with exact directional state across chunks.

Uses global 60-second STFT to focus on neural recurrence. Not a streaming worker.
Graph rewrites remain in memory; activations use private temporary files removed on normal exit.
"""
import hashlib
import json
import os
from pathlib import Path
import resource
import sys
import tempfile
import time
import numpy as np
import onnx
from onnx import helper, numpy_helper, TensorProto
import onnxruntime as ort
import soundfile as sf

EXPECTED='f27742bb52b24cb039614dc76d0764e279df3eb2a9d6daa82c6daee3369ec747'
model=Path(sys.argv[1]); audio=Path(sys.argv[2]); parent=Path(sys.argv[3])
if hashlib.sha256(model.read_bytes()).hexdigest() != EXPECTED: raise SystemExit('model hash mismatch')
x,sr=sf.read(audio,dtype='float32',always_2d=True)
if sr!=44100 or x.shape != (60*44100,2) or not np.isfinite(x).all(): raise SystemExit('expected exactly 60 seconds stereo')
os.umask(0o077)
opts=ort.SessionOptions(); opts.intra_op_num_threads=2; opts.inter_op_num_threads=1; opts.log_severity_level=3

def session(blob):
    s=ort.InferenceSession(blob,sess_options=opts,providers=['CPUExecutionProvider'])
    if s.get_providers()!=['CPUExecutionProvider']: raise RuntimeError('not CPU')
    return s

def memmap(path,shape):
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_RDWR,0o600); os.close(fd)
    return np.memmap(path,mode='w+',dtype=np.float32,shape=shape)

def info(name,shape): return helper.make_tensor_value_info(name,TensorProto.FLOAT,shape)

def direction_graph(node, init, direction):
    i=0 if direction=='forward' else 1
    tensors=[]
    for source,name in zip(node.input[1:4],['W','R','B']):
        tensors.append(numpy_helper.from_array(numpy_helper.to_array(init[source])[i:i+1].copy(),name=name))
    op=helper.make_node('LSTM',inputs=['X','W','R','B','','H','C'],outputs=['Y','Hout','Cout'],direction=direction,hidden_size=256)
    graph=helper.make_graph([op],'direction',[info('X',['T',1,512]), info('H',[1,1,256]), info('C',[1,1,256])],[info('Y',['T',1,1,256]),info('Hout',[1,1,256]),info('Cout',[1,1,256])],initializer=tensors)
    return helper.make_model(graph,opset_imports=[helper.make_opsetid('',17)],ir_version=10).SerializeToString()

def reconstruct(mag,pred,sp,pad_len,win):
    mask=np.clip(pred[0]/np.maximum(mag[0],1e-8),0,1)
    chunks=np.fft.irfft((sp*mask).transpose(0,2,1),n=4096,axis=-1)*win
    out=np.zeros((2,pad_len),dtype='float32'); norm=np.zeros(pad_len,dtype='float32')
    for i in range(chunks.shape[1]):
        p=i*1024; out[:,p:p+4096]+=chunks[:,i,:]; norm[p:p+4096]+=win*win
    out/=np.maximum(norm,1e-8)
    return out[:,2048:2048+len(x)].T.copy()

def error(y,ref):
    d=y.astype('float64')-ref.astype('float64')
    rms=float(np.sqrt(np.mean(ref.astype('float64')**2)))
    return {'normalized_rms':float(np.sqrt(np.mean(d*d))/max(rms,1e-8)), 'max_abs':float(np.max(np.abs(d))), 'reference_rms':rms}

with tempfile.TemporaryDirectory(prefix='ai001-states-',dir=parent) as temp:
    base=Path(temp)
    if (base.stat().st_mode & 0o077): raise RuntimeError('temporary directory not private')
    win=np.hanning(4097)[:-1].astype('float32')
    padded=np.pad(x.T,((0,0),(2048,2048)),mode='reflect')
    frames=np.lib.stride_tricks.sliding_window_view(padded,4096,axis=-1)[:,::1024,:]
    sp=np.fft.rfft(frames*win,axis=-1).transpose(0,2,1)
    mag=np.abs(sp)[None].astype('float32'); total=mag.shape[-1]
    original=session(model.read_bytes()); start=time.perf_counter()
    full=original.run(None,{original.get_inputs()[0].name:mag})[0]
    whole_time=time.perf_counter()-start
    ref=reconstruct(mag,full,sp,padded.shape[1],win)
    del original
    spec=onnx.shape_inference.infer_shapes(onnx.load(str(model)))
    extractor=onnx.utils.Extractor(spec)
    pre=extractor.extract_model(['magnitude'],['/Tanh_output_0'])
    post=extractor.extract_model(['/Tanh_output_0','/lstm/Reshape_2_output_0','/Transpose_output_0'],['vocals_magnitude'])
    pre_s=session(pre.SerializeToString()); post_s=session(post.SerializeToString())
    init={q.name:q for q in spec.graph.initializer}; layers=[n for n in spec.graph.node if n.op_type=='LSTM']
    if len(layers)!=3: raise RuntimeError('unexpected network')
    block=256
    start=time.perf_counter()
    encoded=memmap(base/'encoded.f32',(total,1,512))
    for s in range(0,total,block):
        e=min(total,s+block)
        encoded[s:e]=pre_s.run(None,{'magnitude':np.ascontiguousarray(mag[...,s:e])})[0]
    encoded.flush()
    previous=encoded
    for layer,node in enumerate(layers):
        both=memmap(base/f'layer{layer}.f32',(total,1,512))
        for direction,offset in [('forward',0),('reverse',256)]:
            run=session(direction_graph(node,init,direction))
            h=np.zeros((1,1,256),dtype='float32'); c=h.copy()
            starts=list(range(0,total,block))
            if direction=='reverse': starts.reverse()
            for s in starts:
                e=min(total,s+block)
                result,h,c=run.run(None,{'X':np.ascontiguousarray(previous[s:e]),'H':h,'C':c})
                both[s:e,:,offset:offset+256]=result[:,0,:,:]
            del run
        both.flush()
        previous=both
    pred=memmap(base/'predicted.f32',(1,2,2049,total))
    for s in range(0,total,block):
        e=min(total,s+block)
        pred[...,s:e]=post_s.run(None,{'/Tanh_output_0':np.ascontiguousarray(encoded[s:e]),'/lstm/Reshape_2_output_0':np.ascontiguousarray(previous[s:e]),'/Transpose_output_0':np.ascontiguousarray(mag[...,s:e].transpose(3,0,1,2))})[0]
    pred.flush()
    stream_time=time.perf_counter()-start
    vocal=reconstruct(mag,pred,sp,padded.shape[1],win)
    out={'model_sha256':EXPECTED,'audio_sha256':hashlib.sha256(audio.read_bytes()).hexdigest(),'onnx':onnx.__version__,'onnxruntime':ort.__version__,'provider':['CPUExecutionProvider'],'samples':len(x),'spectrogram_frames':total,'chunk_frames':block,'layers':len(layers),'whole_inference_seconds':whole_time,'stateful_stages_seconds':stream_time,'stereo_spectral_output':error(pred,full),'vocals':error(vocal,ref),'accompaniment':error(x-vocal,x-ref),'temporary_files':[{'name':p.name,'size_bytes':p.stat().st_size,'permissions':oct(p.stat().st_mode & 0o777)} for p in sorted(base.iterdir())], 'peak_rss_bytes_process':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'notes':'Full input/global STFT and full-graph reference are held in RAM; this is not a bounded end-to-end memory measurement or independent CPU reference. Global periodic Hann reflect-pad with complete windows only matches corrected worker frame count; this does not verify all production endpoint behavior.'}
    print(json.dumps(out,indent=2))
