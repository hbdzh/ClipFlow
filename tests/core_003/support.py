"""Independent CORE-003 QA helpers; no implementation or legacy QA imports."""
import ctypes as C
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TERMINAL = {'succeeded', 'failed', 'cancelled'}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def report(path, value):
    path = Path(path)
    if not path.is_absolute():
        path = ROOT / path
    if HERE not in path.resolve().parents or '..' in path.relative_to(HERE).parts:
        raise ValueError('QA evidence must stay in tests/core_003')
    current = HERE
    for part in path.relative_to(HERE).parts:
        current /= part
        if current.is_symlink():
            raise ValueError('No symlink evidence paths')
        if current.exists():
            info = current.stat()
            if current == path:
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError('No special/hardlinked evidence output')
            elif not stat.S_ISDIR(info.st_mode):
                raise ValueError('Evidence ancestor must be a directory')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                                         prefix='.core003-', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


class Commands:
    def __init__(self):
        self.records = []

    def run(self, argv, expected=0, timeout=30):
        argv = [str(x) for x in argv]
        start = time.monotonic()
        p = subprocess.run(argv, cwd=ROOT, stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, timeout=timeout)
        self.records.append({'argv': argv, 'returncode': p.returncode,
                             'elapsed_seconds': round(time.monotonic()-start, 6),
                             'stdout': p.stdout, 'stderr': p.stderr})
        if expected is not None and p.returncode != expected:
            raise AssertionError((argv, p.returncode, p.stdout, p.stderr))
        return p


class Buffer(C.Structure):
    _fields_ = [('ptr', C.POINTER(C.c_uint8)), ('len', C.c_size_t)]


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()


class API:
    def __init__(self, library):
        self.lib = C.CDLL(str(library))
        for name in ['ms_get_version', 'ms_get_capabilities']:
            f = getattr(self.lib, name)
            f.argtypes = [C.c_void_p, C.POINTER(Buffer)]
            f.restype = C.c_int32
        self.lib.ms_engine_create.argtypes = [C.c_void_p, C.c_size_t, C.POINTER(C.c_void_p)]
        self.lib.ms_engine_create.restype = C.c_int32
        self.lib.ms_engine_destroy.argtypes = [C.c_void_p]
        self.lib.ms_engine_destroy.restype = C.c_int32
        for name in ['ms_submit_job', 'ms_get_job_snapshot']:
            f = getattr(self.lib, name)
            f.argtypes = [C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(Buffer)]
            f.restype = C.c_int32
        self.lib.ms_cancel_job.argtypes = [C.c_void_p, C.c_void_p, C.c_size_t]
        self.lib.ms_cancel_job.restype = C.c_int32
        self.lib.ms_buffer_free.argtypes = [Buffer]
        self.lib.ms_buffer_free.restype = None

    def output(self, rc, out):
        try:
            if rc:
                if out.ptr or out.len:
                    raise AssertionError('Failure output was not zeroed')
                return rc, None
            if not out.ptr or not out.len:
                raise AssertionError('Successful output is empty')
            result = json.loads(C.string_at(out.ptr, out.len))
            if result.get('schema_version') != 0:
                raise AssertionError('Missing schema_version 0')
            return rc, result
        finally:
            self.lib.ms_buffer_free(out)

    def create(self, config):
        b = encoded(config)
        h = C.c_void_p()
        rc = self.lib.ms_engine_create(C.c_char_p(b), len(b), C.byref(h))
        if rc or not h.value:
            raise AssertionError(f'Engine creation failed: {rc}')
        return Engine(self, h)


class Engine:
    def __init__(self, api, handle):
        self.api, self.handle, self.jobs, self.closed = api, handle, [], False
        self.trace = []

    def query(self, name):
        out = Buffer()
        rc, value = self.api.output(getattr(self.api.lib, name)(self.handle, C.byref(out)), out)
        if rc:
            raise AssertionError((name, rc))
        return value

    def submit(self, request):
        b = encoded(request) if not isinstance(request, bytes) else request
        out = Buffer()
        rc, value = self.api.output(self.api.lib.ms_submit_job(self.handle, C.c_char_p(b), len(b), C.byref(out)), out)
        if value:
            self.jobs.append(value['job_id'])
            self.trace.append(value)
        return rc, value

    def snapshot(self, job):
        b = job.encode()
        out = Buffer()
        rc, value = self.api.output(self.api.lib.ms_get_job_snapshot(self.handle, C.c_char_p(b), len(b), C.byref(out)), out)
        if rc:
            raise AssertionError(('snapshot', rc))
        self.trace.append(value)
        return value

    def cancel(self, job):
        b = job.encode()
        return self.api.lib.ms_cancel_job(self.handle, C.c_char_p(b), len(b))

    def terminal(self, job, seconds=20):
        until = time.monotonic()+seconds
        while time.monotonic() < until:
            value = self.snapshot(job)
            if value['state'] in TERMINAL:
                return value
            time.sleep(.01)
        raise AssertionError('Media job did not terminate within QA deadline')

    def destroy(self):
        rc = self.api.lib.ms_engine_destroy(self.handle)
        if rc == 0:
            self.closed = True
        return rc

    def cleanup(self):
        if self.closed:
            return
        for job in self.jobs:
            if self.cancel(job) == 0:
                self.terminal(job, seconds=10)
        if self.destroy() != 0:
            raise AssertionError('Test engine could not close after cleanup')


def backend_config(ffmpeg=None, ffprobe=None, timeout_ms=30000, max_output_bytes=8*1024*1024):
    return {'schema_version': 0, 'media_backend': {
        'ffmpeg': str(ffmpeg or shutil.which('ffmpeg')),
        'ffprobe': str(ffprobe or shutil.which('ffprobe')),
        'timeout_ms': timeout_ms, 'max_output_bytes': max_output_bytes}}


def probe_request(source):
    return {'schema_version': 0, 'operation': 'probe', 'source': {'uri': Path(source).as_uri()}}


def trim_request(source, destination, start_us=1100000, end_us=2600000):
    return {'schema_version': 0, 'operation': 'trim', 'source': {'uri': Path(source).as_uri()},
            'destination': {'uri': Path(destination).as_uri()},
            'range': {'start_us': start_us, 'end_us': end_us},
            'strategy': 'stream_copy', 'stream_selection': 'all_supported',
            'allow_reencode_fallback': False}



def accurate_request(source, destination, start_us=1100000, end_us=2600000):
    request=trim_request(source,destination,start_us,end_us)
    request['strategy']='accurate_reencode'
    request['profile']='mp4_h264_standard_v1'
    return request


def copy_v1_request(source, destination, start_us=1100000, end_us=2600000):
    request=trim_request(source,destination,start_us,end_us)
    request['profile']='mp4_h264_copy_v1'
    return request
