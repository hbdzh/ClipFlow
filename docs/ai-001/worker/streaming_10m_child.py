#!/usr/bin/env python3
"""Bounded-window, disk-backed UMX recurrence research; NOT production code.

600-second guarded trusted input only. NOT production code.
"""
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import time
import numpy as np
import onnx
from onnx import helper, numpy_helper, TensorProto
import onnxruntime as ort
import soundfile as sf

MODEL_HASH='f27742bb52b24cb039614dc76d0764e279df3eb2a9d6daa82c6daee3369ec747'
RATE=44100
FFT=4096
HOP=1024
BLOCK=256
WIN=np.hanning(FFT+1)[:-1].astype('float32')

def sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()

def config():
    o=ort.SessionOptions(); o.intra_op_num_threads=2; o.inter_op_num_threads=1; o.log_severity_level=3
    return o

def session(blob):
    s=ort.InferenceSession(blob,sess_options=config(),providers=['CPUExecutionProvider'])
    if s.get_providers()!=['CPUExecutionProvider']: raise ValueError('not CPU')
    return s

def mmap(path,shape):
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_RDWR,0o600); os.close(fd)
    return np.memmap(path,mode='w+',dtype='float32',shape=shape)

def value(name,shape): return helper.make_tensor_value_info(name,TensorProto.FLOAT,shape)

def direction_graph(node,init,direction):
    k=0 if direction=='forward' else 1
    weights=[numpy_helper.from_array(numpy_helper.to_array(init[src])[k:k+1].copy(),name=name) for src,name in zip(node.input[1:4],['W','R','B'])]
    op=helper.make_node('LSTM',inputs=['X','W','R','B','','H','C'],outputs=['Y','Hout','Cout'],direction=direction,hidden_size=256)
    g=helper.make_graph([op],'direction',[value('X',['T',1,512]),value('H',[1,1,256]),value('C',[1,1,256])],[value('Y',['T',1,1,256]),value('Hout',[1,1,256]),value('Cout',[1,1,256])],initializer=weights)
    return helper.make_model(g,opset_imports=[helper.make_opsetid('',17)],ir_version=10).SerializeToString()

def validate(audio):
    a=sf.info(audio)
    if a.samplerate != RATE or a.channels != 2 or a.frames != RATE*600 or a.format not in ('WAV','WAVEX') or a.subtype!='FLOAT':
        raise ValueError('expected 600 seconds stereo float WAV')
    return a.frames

def spectrum(file, n, first, last):
    # Frame k centered at input k*HOP. Reflect at the GLOBAL input edges only.
    start=first*HOP-FFT//2; end=(last-1)*HOP+FFT//2
    l=max(start,0); r=min(end,n)
    file.seek(l); samples=file.read(r-l,dtype='float32',always_2d=True).T
    if samples.shape!=(2,r-l) or not np.isfinite(samples).all(): raise ValueError('short or invalid input')
    missing_l=max(0,-start); missing_r=max(0,end-n)
    if missing_l or missing_r: samples=np.pad(samples,((0,0),(missing_l,missing_r)),mode='reflect')
    frames=np.lib.stride_tricks.sliding_window_view(samples,FFT,axis=-1)[:,::HOP,:]
    if frames.shape[1] != last-first: raise ValueError('bad frame slicing')
    return np.fft.rfft(frames*WIN,axis=-1).transpose(0,2,1)

def reference(model,audio,root):
    n=validate(audio); total=n//HOP+1
    with sf.SoundFile(audio) as f: z=spectrum(f,n,0,total)
    m=np.abs(z)[None].astype('float32')
    run=session(model.read_bytes()); t=time.perf_counter()
    pred=run.run(None,{run.get_inputs()[0].name:m})[0]
    mask=np.clip(pred[0]/np.maximum(m[0],1e-8),0,1)
    chunks=np.fft.irfft((z*mask).transpose(0,2,1),n=FFT,axis=-1)*WIN
    pad_len=n+FFT
    v=np.zeros((2,pad_len),dtype='float32'); norm=np.zeros(pad_len,dtype='float32')
    for i in range(total):
        pos=i*HOP; v[:,pos:pos+FFT]+=chunks[:,i,:]; norm[pos:pos+FFT]+=WIN*WIN
    v/=np.maximum(norm,1e-8); v=v[:,FFT//2:FFT//2+n].T
    x,_=sf.read(audio,dtype='float32',always_2d=True)
    for name,data in [('ref-vocals.wav',v),('ref-accompaniment.wav',x-v)]: sf.write(root/name,data,RATE,subtype='FLOAT')
    return {'seconds':time.perf_counter()-t,'peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'frames':total}

def stream(model,audio,root):
    n=validate(audio); total=n//HOP+1
    t=time.perf_counter()
    spec=onnx.shape_inference.infer_shapes(onnx.load(str(model)))
    extractor=onnx.utils.Extractor(spec)
    pre=session(extractor.extract_model(['magnitude'],['/Tanh_output_0']).SerializeToString())
    post=session(extractor.extract_model(['/Tanh_output_0','/lstm/Reshape_2_output_0','/Transpose_output_0'],['vocals_magnitude']).SerializeToString())
    init={q.name:q for q in spec.graph.initializer}
    layers=[q for q in spec.graph.node if q.op_type=='LSTM']
    if len(layers)!=3: raise ValueError('unexpected model')
    encoded=mmap(root/'encoded.f32',(total,1,512))
    with sf.SoundFile(audio) as f:
        for first in range(0,total,BLOCK):
            last=min(total,first+BLOCK)
            m=np.abs(spectrum(f,n,first,last))[None].astype('float32')
            encoded[first:last]=pre.run(None,{'magnitude':m})[0]
    encoded.flush(); previous=encoded
    for layer,node in enumerate(layers):
        both=mmap(root/f'layer{layer}.f32',(total,1,512))
        for direction,offset in [('forward',0),('reverse',256)]:
            run=session(direction_graph(node,init,direction))
            h=np.zeros((1,1,256),dtype='float32'); c=h.copy()
            starts=range(0,total,BLOCK) if direction=='forward' else reversed(range(0,total,BLOCK))
            for first in starts:
                last=min(total,first+BLOCK)
                out,h,c=run.run(None,{'X':np.ascontiguousarray(previous[first:last]),'H':h,'C':c})
                both[first:last,:,offset:offset+256]=out[:,0,:,:]
            del run
        both.flush(); previous=both
    # OLA buffer begins at padded-domain first*HOP and keeps the FFT-HOP tail.
    pending=np.zeros((2,FFT-HOP),dtype='float32'); pending_norm=np.zeros(FFT-HOP,dtype='float32')
    emitted=0
    with sf.SoundFile(audio) as source, sf.SoundFile(audio) as actual, sf.SoundFile(root/'stream-vocals.wav','w',samplerate=RATE,channels=2,format='WAV',subtype='FLOAT') as vocal_file, sf.SoundFile(root/'stream-accompaniment.wav','w',samplerate=RATE,channels=2,format='WAV',subtype='FLOAT') as accom_file:
        for first in range(0,total,BLOCK):
            last=min(total,first+BLOCK); count=last-first
            z=spectrum(source,n,first,last); m=np.abs(z)[None].astype('float32')
            pred=post.run(None,{'/Tanh_output_0':np.ascontiguousarray(encoded[first:last]),'/lstm/Reshape_2_output_0':np.ascontiguousarray(previous[first:last]),'/Transpose_output_0':np.ascontiguousarray(m.transpose(3,0,1,2))})[0]
            mask=np.clip(pred[0]/np.maximum(m[0],1e-8),0,1)
            frames=np.fft.irfft((z*mask).transpose(0,2,1),n=FFT,axis=-1)*WIN
            accum=np.zeros((2,count*HOP+FFT-HOP),dtype='float32'); norm=np.zeros(accum.shape[1],dtype='float32')
            accum[:,:FFT-HOP]=pending; norm[:FFT-HOP]=pending_norm
            for i in range(count):
                pos=i*HOP; accum[:,pos:pos+FFT]+=frames[:,i,:]; norm[pos:pos+FFT]+=WIN*WIN
            take=count*HOP
            def emit(a,b):
                nonlocal emitted
                # Domain index is padded input; crop center reflect prefix/suffix.
                lo=max(a,FFT//2); hi=min(b,FFT//2+n)
                if hi<=lo: return
                left=lo-a; right=hi-a
                v=(accum[:,left:right]/np.maximum(norm[left:right],1e-8)).T
                actual.seek(emitted); x=actual.read(len(v),dtype='float32',always_2d=True)
                if x.shape!=v.shape: raise ValueError('short audio')
                vocal_file.write(v); accom_file.write(x-v); emitted+=len(v)
            base=first*HOP
            emit(base,base+take)
            pending=accum[:,take:].copy(); pending_norm=norm[take:].copy()
            if last==total:
                accum=pending; norm=pending_norm
                emit(base+take,base+take+len(norm))
    if emitted!=n: raise ValueError('incorrect output sample count')
    names=['encoded.f32','layer0.f32','layer1.f32','layer2.f32','stream-vocals.wav','stream-accompaniment.wav']
    return {'seconds':time.perf_counter()-t,'peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'frames':total,'temp_files':[{'name':name,'size_bytes':(root/name).stat().st_size,'permissions':oct((root/name).stat().st_mode & 0o777)} for name in names]}

def compare(root):
    result={}
    for stem in ['vocals','accompaniment']:
        with sf.SoundFile(root/f'ref-{stem}.wav') as ref, sf.SoundFile(root/f'stream-{stem}.wav') as out:
            if (len(ref),ref.samplerate,ref.channels)!=(len(out),out.samplerate,out.channels): raise ValueError('output metadata mismatch')
            n=len(ref); sum_error=0.0; sum_ref=0.0; peak=0.0; unequal=0
            while True:
                a=ref.read(44100,dtype='float32',always_2d=True); b=out.read(44100,dtype='float32',always_2d=True)
                if len(a)==0: break
                if a.shape!=b.shape or not np.isfinite(a).all() or not np.isfinite(b).all(): raise ValueError('invalid output')
                d=b.astype('float64')-a.astype('float64')
                sum_error+=float(np.sum(d*d)); sum_ref+=float(np.sum(a.astype('float64')**2)); peak=max(peak,float(np.max(np.abs(d)))); unequal+=int(np.count_nonzero(a!=b))
            denominator=max((sum_ref/(n*2))**0.5,1e-8)
            result[stem]={'normalized_rms':(sum_error/(n*2))**0.5/denominator,'peak_abs_difference':peak,'unequal_float_samples':unequal,'source_frames':n}
    return result

def main():
    # Driver only accepts fixed, trusted experiment paths; it is not an ingestion API.
    mode=sys.argv[1]; model=Path(sys.argv[2]); audio=Path(sys.argv[3]); root=Path(sys.argv[4]); os.umask(0o077)
    if sha(model)!=MODEL_HASH: raise ValueError('model hash mismatch')
    if mode=='stream': print(json.dumps(stream(model,audio,root))); return
    raise ValueError('unknown mode')
    if mode!='driver': raise ValueError('unknown mode')
    details={'model_sha256':sha(model),'audio_sha256':sha(audio),'sample_rate':RATE,'onnxruntime':ort.__version__,'onnx':onnx.__version__,'numpy':np.__version__}
    with tempfile.TemporaryDirectory(prefix='ai001-stream-',dir=root) as d:
        stage=Path(d)
        if stage.stat().st_mode & 0o077: raise ValueError('stage permissions')
        for task in ['reference','stream']:
            p=subprocess.run([sys.executable,str(Path(__file__).resolve()),task,str(model),str(audio),str(stage)],capture_output=True,text=True,timeout=120)
            if p.returncode: raise RuntimeError(task+': '+p.stderr)
            details[task]=json.loads(p.stdout)
        details['comparison']=compare(stage)
        details['temporary_total_bytes']=sum(p.stat().st_size for p in stage.iterdir())
    details['temporary_directory_removed_after_normal_completion']=not stage.exists()
    print(json.dumps(details,indent=2))

if __name__=='__main__': main()
