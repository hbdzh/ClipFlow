#!/usr/bin/env python3
"""Experimental Open-Unmix CPU worker. Not a product or a sandbox boundary."""
import hashlib
import json
import os
import stat
import sys

MODEL_SHA256 = 'f27742bb52b24cb039614dc76d0764e279df3eb2a9d6daa82c6daee3369ec747'
MODEL_BYTES = 35623512
MODEL_ID = 'open-unmix-umxhq-vocals-onnx-e06097d3'
RATE = 44100
MAX_INPUT_BYTES = 16 * 1024 * 1024

class WorkerError(Exception):
    def __init__(self, code):
        self.code = code


def args_parse(args):
    if len(args) != 6:
        raise WorkerError('INVALID_ARGUMENT')
    found = {}
    for i in range(0, len(args), 2):
        k, v = args[i:i+2]
        if k not in ('--model', '--input', '--staging') or k in found or not os.path.isabs(v) or '\0' in v:
            raise WorkerError('INVALID_ARGUMENT')
        found[k] = v
    if len(found) != 3:
        raise WorkerError('INVALID_ARGUMENT')
    return found


def open_regular(path, max_bytes, missing_code, invalid_code):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        m = os.fstat(fd)
        if not stat.S_ISREG(m.st_mode) or m.st_size <= 0 or m.st_size > max_bytes:
            os.close(fd)
            raise WorkerError(invalid_code)
        return fd, m
    except OSError:
        raise WorkerError(missing_code) from None


def execute(params):
    # Hash the exact model bytes given to ORT, preventing a path swap after hash.
    model_fd, meta = open_regular(params['--model'], MODEL_BYTES, 'MODEL_MISSING', 'MODEL_INVALID')
    try:
        if meta.st_size != MODEL_BYTES:
            raise WorkerError('MODEL_INVALID')
        with os.fdopen(model_fd, 'rb', closefd=False) as f:
            blob = f.read(MODEL_BYTES + 1)
        if len(blob) != MODEL_BYTES or hashlib.sha256(blob).hexdigest() != MODEL_SHA256:
            raise WorkerError('MODEL_INVALID')
    finally:
        os.close(model_fd)
    try:
        import numpy as np
        import onnxruntime as ort
        import soundfile as sf
    except Exception:
        raise WorkerError('DEPENDENCY_MISSING') from None
    source_fd, meta = open_regular(params['--input'], MAX_INPUT_BYTES, 'INPUT_UNAVAILABLE', 'INVALID_INPUT')
    try:
        with sf.SoundFile(source_fd, mode='r', closefd=False) as f:
            if f.format not in ('WAV', 'WAVEX') or f.subtype != 'FLOAT' or f.samplerate != RATE or f.channels != 2 or not RATE <= f.frames <= RATE * 30:
                raise WorkerError('INVALID_INPUT')
            x = f.read(dtype='float32', always_2d=True)
            if x.shape != (f.frames, 2) or not np.isfinite(x).all() or float(np.max(np.abs(x))) > 1.0:
                raise WorkerError('INVALID_INPUT')
        now = os.fstat(source_fd)
        if (meta.st_dev, meta.st_ino, meta.st_size, meta.st_mtime_ns) != (now.st_dev, now.st_ino, now.st_size, now.st_mtime_ns):
            raise WorkerError('INPUT_CHANGED')
    except WorkerError:
        raise
    except Exception:
        raise WorkerError('INVALID_INPUT') from None
    finally:
        os.close(source_fd)
    try:
        dfd = os.open(params['--staging'], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        raise WorkerError('STAGING_INVALID') from None
    created = []
    try:
        st = os.fstat(dfd)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or (st.st_mode & 0o077) != 0 or os.listdir(dfd):
            raise WorkerError('STAGING_INVALID')
        win = np.hanning(4097)[:-1].astype('float32')
        padded = np.pad(x.T, ((0, 0), (2048, 2048)), mode='reflect')
        # torch.stft(center=True) uses only complete windows after reflect
        # padding. Adding an extra right-zero frame changes this bidirectional
        # model's context and therefore its result across the whole clip.
        frames = np.lib.stride_tricks.sliding_window_view(padded, 4096, axis=-1)[:, ::1024, :]
        z = np.fft.rfft(frames * win, axis=-1).transpose(0, 2, 1)
        mag = np.abs(z)[None].astype('float32')
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.log_severity_level = 3
        session = ort.InferenceSession(blob, sess_options=options, providers=['CPUExecutionProvider'])
        if session.get_providers() != ['CPUExecutionProvider'] or len(session.get_inputs()) != 1:
            raise WorkerError('MODEL_INVALID')
        y = session.run(None, {session.get_inputs()[0].name: mag})[0]
        if y.shape != mag.shape or not np.isfinite(y).all():
            raise WorkerError('MODEL_OUTPUT_INVALID')
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
        other = x - vocal
        if vocal.shape != x.shape or not np.isfinite(vocal).all() or not np.isfinite(other).all():
            raise WorkerError('MODEL_OUTPUT_INVALID')
        for name, data in (('vocals.wav', vocal), ('accompaniment.wav', other)):
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dfd)
            try:
                m = os.fstat(fd)
                created.append((name, m.st_dev, m.st_ino))
                with sf.SoundFile(fd, 'w', samplerate=RATE, channels=2, format='WAV', subtype='FLOAT', closefd=False) as output:
                    output.write(data)
            finally:
                os.close(fd)
        return {'schema_version': 1, 'model_id': MODEL_ID, 'frames': len(x), 'sample_rate': RATE, 'channels': 2, 'outputs': ['vocals.wav', 'accompaniment.wav']}
    except BaseException:
        # Remove only the exact inodes created by this process; never unknown names.
        for name, dev, ino in created:
            try:
                m = os.stat(name, dir_fd=dfd, follow_symlinks=False)
                if (m.st_dev, m.st_ino) == (dev, ino):
                    os.unlink(name, dir_fd=dfd)
            except OSError:
                pass
        raise
    finally:
        os.close(dfd)


def main():
    try:
        result = execute(args_parse(sys.argv[1:]))
    except WorkerError as exc:
        print(json.dumps({'schema_version': 1, 'error': exc.code}, separators=(',', ':')))
        return 1
    except Exception:
        print(json.dumps({'schema_version': 1, 'error': 'PROCESS_FAILED'}, separators=(',', ':')))
        return 1
    print(json.dumps(result, separators=(',', ':')))
    return 0

if __name__ == '__main__':
    sys.exit(main())
