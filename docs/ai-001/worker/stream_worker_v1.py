#!/usr/bin/env python3
"""Fixed-model, CPU-only streaming worker for AI-001 integration development.

This process is not a security sandbox. Core owns input normalization, the
private staging directory, process lifetime, cleanup, and final publication.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

MODEL_SHA256 = 'f27742bb52b24cb039614dc76d0764e279df3eb2a9d6daa82c6daee3369ec747'
MODEL_SIZE = 35623512
MODEL_ID = 'open-unmix-umxhq-vocals-onnx-e06097d3'
RATE, FFT, HOP, BLOCK = 44100, 4096, 1024, 256
MAX_FRAMES = RATE * 600
MIN_FRAMES = FFT // 2 + 1  # reflect padding requires input > 2048 samples
MAX_INPUT_SIZE = MAX_FRAMES * 2 * 4 + 1024 * 1024

class WorkerError(Exception):
    def __init__(self, code):
        self.code = code


def parse_args(values):
    if len(values) != 6:
        raise WorkerError('INVALID_ARGUMENT')
    result = {}
    for k, v in zip(values[0::2], values[1::2]):
        if k not in ('--model', '--input', '--staging') or k in result or not os.path.isabs(v) or '\0' in v:
            raise WorkerError('INVALID_ARGUMENT')
        result[k] = v
    if len(result) != 3:
        raise WorkerError('INVALID_ARGUMENT')
    return result


def open_regular(path, limit):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or not 0 < meta.st_size <= limit:
            os.close(fd)
            raise WorkerError('INVALID_INPUT')
        return fd, meta
    except OSError:
        raise WorkerError('INPUT_UNAVAILABLE') from None


def same_content_metadata(a, b):
    return (a.st_dev, a.st_ino, a.st_size, a.st_mtime_ns, a.st_ctime_ns) == (b.st_dev, b.st_ino, b.st_size, b.st_mtime_ns, b.st_ctime_ns)


def model_bytes(path):
    fd, meta = open_regular(path, MODEL_SIZE)
    try:
        if meta.st_size != MODEL_SIZE:
            raise WorkerError('MODEL_INVALID')
        with os.fdopen(fd, 'rb', closefd=False) as f:
            data = f.read(MODEL_SIZE + 1)
        if len(data) != MODEL_SIZE or hashlib.sha256(data).hexdigest() != MODEL_SHA256 or not same_content_metadata(meta, os.fstat(fd)):
            raise WorkerError('MODEL_INVALID')
        return data
    finally:
        os.close(fd)


def execute(params):
    # Load exactly the bytes whose hash was checked; never reopen the model path.
    blob = model_bytes(params['--model'])
    try:
        import numpy as np
        import onnx
        from onnx import helper, numpy_helper, TensorProto
        import onnxruntime as ort
        import soundfile as sf
        from importlib.metadata import version
        for name, required in [('numpy','2.2.6'), ('onnx','1.18.0'), ('onnxruntime','1.22.1'), ('soundfile','0.13.1')]:
            if version(name) != required:
                raise WorkerError('DEPENDENCY_UNSUPPORTED')
    except WorkerError:
        raise
    except Exception:
        raise WorkerError('DEPENDENCY_MISSING') from None
    source_fd, source_meta = open_regular(params['--input'], MAX_INPUT_SIZE)
    try:
        try:
            stage_fd = os.open(params['--staging'], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError:
            raise WorkerError('STAGING_INVALID') from None
        maps, files = [], []
        try:
            stage = os.fstat(stage_fd)
            if not stat.S_ISDIR(stage.st_mode) or stage.st_uid != os.geteuid() or stage.st_mode & 0o077 or os.listdir(stage_fd):
                raise WorkerError('STAGING_INVALID')
            with sf.SoundFile(source_fd, 'r', closefd=False) as src:
                n = src.frames
                if src.format not in ('WAV','WAVEX') or src.subtype != 'FLOAT' or src.samplerate != RATE or src.channels != 2 or not MIN_FRAMES <= n <= MAX_FRAMES:
                    raise WorkerError('INVALID_INPUT')
                # Validate once before creating outputs, in bounded chunks.
                while True:
                    chunk = src.read(RATE, dtype='float32', always_2d=True)
                    if not len(chunk):
                        break
                    if not np.isfinite(chunk).all() or float(np.max(np.abs(chunk))) > 2.0:
                        raise WorkerError('INVALID_INPUT')
                if not same_content_metadata(source_meta, os.fstat(source_fd)):
                    raise WorkerError('INPUT_CHANGED')
                total = n // HOP + 1
                window = np.hanning(FFT + 1)[:-1].astype('float32')
                options = ort.SessionOptions()
                options.intra_op_num_threads = 2
                options.inter_op_num_threads = 1
                options.log_severity_level = 3
                def session(data):
                    instance = ort.InferenceSession(data, sess_options=options, providers=['CPUExecutionProvider'])
                    if instance.get_providers() != ['CPUExecutionProvider']:
                        raise WorkerError('DEPENDENCY_UNSUPPORTED')
                    return instance
                def value(name, shape):
                    return helper.make_tensor_value_info(name, TensorProto.FLOAT, shape)
                def mmap_temp(index):
                    # Open an exclusive file inside Core's private directory and
                    # immediately unlink its name; the open fd owns its lifetime.
                    name = '.feature-' + str(index)
                    fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=stage_fd)
                    try:
                        before = os.fstat(fd)
                        listed = os.stat(name, dir_fd=stage_fd, follow_symlinks=False)
                        if (before.st_dev,before.st_ino) != (listed.st_dev,listed.st_ino):
                            raise WorkerError('STAGING_CHANGED')
                        os.unlink(name, dir_fd=stage_fd)
                        f = os.fdopen(fd, 'w+b', closefd=True)
                        files.append(f)
                        mm = np.memmap(f, mode='w+', dtype='float32', shape=(total,1,512))
                        maps.append(mm)
                        return mm
                    except BaseException:
                        if not files or files[-1].fileno() != fd:
                            os.close(fd)
                        raise
                def spectrum(first, last):
                    start = first * HOP - FFT // 2
                    end = (last - 1) * HOP + FFT // 2
                    left, right = max(start,0), min(end,n)
                    src.seek(left)
                    samples = src.read(right-left, dtype='float32', always_2d=True).T
                    if samples.shape != (2,right-left) or not np.isfinite(samples).all():
                        raise WorkerError('INPUT_CHANGED')
                    pad_l, pad_r = max(0,-start), max(0,end-n)
                    if pad_l or pad_r:
                        samples = np.pad(samples, ((0,0),(pad_l,pad_r)), mode='reflect')
                    frames = np.lib.stride_tricks.sliding_window_view(samples, FFT, axis=-1)[:,::HOP,:]
                    if frames.shape[1] != last-first:
                        raise WorkerError('INTERNAL')
                    return np.fft.rfft(frames*window, axis=-1).transpose(0,2,1)
                def direction_graph(node, initializers, direction):
                    index = 0 if direction == 'forward' else 1
                    tensors = [numpy_helper.from_array(numpy_helper.to_array(initializers[key])[index:index+1].copy(),name=name) for key,name in zip(node.input[1:4],['W','R','B'])]
                    op = helper.make_node('LSTM', inputs=['X','W','R','B','','H','C'], outputs=['Y','Hout','Cout'], direction=direction, hidden_size=256)
                    graph = helper.make_graph([op], 'umx-direction', [value('X',['T',1,512]),value('H',[1,1,256]),value('C',[1,1,256])], [value('Y',['T',1,1,256]),value('Hout',[1,1,256]),value('Cout',[1,1,256])], initializer=tensors)
                    return helper.make_model(graph,opset_imports=[helper.make_opsetid('',17)],ir_version=10).SerializeToString()
                spec = onnx.shape_inference.infer_shapes(onnx.load_model_from_string(blob))
                extractor = onnx.utils.Extractor(spec)
                pre = session(extractor.extract_model(['magnitude'],['/Tanh_output_0']).SerializeToString())
                post = session(extractor.extract_model(['/Tanh_output_0','/lstm/Reshape_2_output_0','/Transpose_output_0'],['vocals_magnitude']).SerializeToString())
                initializers = {v.name:v for v in spec.graph.initializer}
                layers = [node for node in spec.graph.node if node.op_type == 'LSTM']
                if len(layers) != 3:
                    raise WorkerError('MODEL_INVALID')
                encoded = mmap_temp(0)
                for first in range(0,total,BLOCK):
                    last = min(total,first+BLOCK)
                    mag = np.abs(spectrum(first,last))[None].astype('float32')
                    encoded[first:last] = pre.run(None,{'magnitude':mag})[0]
                encoded.flush()
                previous = encoded
                for index,node in enumerate(layers):
                    both = mmap_temp(index+1)
                    for direction,offset in [('forward',0),('reverse',256)]:
                        run = session(direction_graph(node,initializers,direction))
                        h = np.zeros((1,1,256),dtype='float32')
                        c = h.copy()
                        starts = range(0,total,BLOCK) if direction == 'forward' else reversed(range(0,total,BLOCK))
                        for first in starts:
                            last = min(total,first+BLOCK)
                            output,h,c = run.run(None,{'X':np.ascontiguousarray(previous[first:last]),'H':h,'C':c})
                            both[first:last,:,offset:offset+256] = output[:,0,:,:]
                        del run
                    both.flush()
                    previous = both
                # Core alone publishes these files. Worker never writes user targets.
                output_fds = []
                try:
                    for name in ('vocals.wav','accompaniment.wav'):
                        fd = os.open(name,os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,0o600,dir_fd=stage_fd)
                        output_fds.append(fd)
                    with sf.SoundFile(output_fds[0],'w',samplerate=RATE,channels=2,format='WAV',subtype='FLOAT',closefd=False) as vocals, sf.SoundFile(output_fds[1],'w',samplerate=RATE,channels=2,format='WAV',subtype='FLOAT',closefd=False) as accomp:
                        pending = np.zeros((2,FFT-HOP),dtype='float32')
                        pending_norm = np.zeros(FFT-HOP,dtype='float32')
                        emitted = 0
                        for first in range(0,total,BLOCK):
                            last = min(total,first+BLOCK)
                            count = last-first
                            z = spectrum(first,last)
                            mag = np.abs(z)[None].astype('float32')
                            estimate = post.run(None,{'/Tanh_output_0':np.ascontiguousarray(encoded[first:last]),'/lstm/Reshape_2_output_0':np.ascontiguousarray(previous[first:last]),'/Transpose_output_0':np.ascontiguousarray(mag.transpose(3,0,1,2))})[0]
                            if estimate.shape != mag.shape or not np.isfinite(estimate).all():
                                raise WorkerError('MODEL_OUTPUT_INVALID')
                            mask = np.clip(estimate[0]/np.maximum(mag[0],1e-8),0,1)
                            frames = np.fft.irfft((z*mask).transpose(0,2,1),n=FFT,axis=-1)*window
                            accum = np.zeros((2,count*HOP+FFT-HOP),dtype='float32')
                            norm = np.zeros(accum.shape[1],dtype='float32')
                            accum[:,:FFT-HOP] = pending
                            norm[:FFT-HOP] = pending_norm
                            for i in range(count):
                                pos = i*HOP
                                accum[:,pos:pos+FFT] += frames[:,i,:]
                                norm[pos:pos+FFT] += window*window
                            take = count*HOP
                            def emit(a,b):
                                nonlocal emitted
                                lo,hi = max(a,FFT//2),min(b,FFT//2+n)
                                if hi <= lo:
                                    return
                                v = (accum[:,lo-a:hi-a]/np.maximum(norm[lo-a:hi-a],1e-8)).T
                                src.seek(emitted)
                                original = src.read(len(v),dtype='float32',always_2d=True)
                                if original.shape != v.shape or not np.isfinite(v).all():
                                    raise WorkerError('MODEL_OUTPUT_INVALID')
                                vocals.write(v)
                                accomp.write(original-v)
                                emitted += len(v)
                            base = first*HOP
                            emit(base,base+take)
                            pending = accum[:,take:].copy()
                            pending_norm = norm[take:].copy()
                            if last == total:
                                accum,norm = pending,pending_norm
                                emit(base+take,base+take+len(norm))
                        if emitted != n or not same_content_metadata(source_meta,os.fstat(source_fd)):
                            raise WorkerError('INPUT_CHANGED')
                finally:
                    for fd in output_fds:
                        os.close(fd)
                return {'schema_version':1,'model_id':MODEL_ID,'frames':n,'sample_rate':RATE,'channels':2,'outputs':['vocals.wav','accompaniment.wav']}
        finally:
            for mm in maps:
                mm.flush()
            maps.clear()
            for f in files:
                f.close()
            os.close(stage_fd)
    finally:
        os.close(source_fd)


def main():
    try:
        response = execute(parse_args(sys.argv[1:]))
    except WorkerError as e:
        response = {'schema_version':1,'error':e.code}
        print(json.dumps(response,separators=(',',':')))
        return 1
    except Exception:
        print('{"schema_version":1,"error":"PROCESS_FAILED"}')
        return 1
    print(json.dumps(response,separators=(',',':')))
    return 0

if __name__ == '__main__':
    sys.exit(main())
