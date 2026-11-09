"""Independent CORE-004 QA process/ABI helpers, no production imports."""
import ctypes as C
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
TERMINAL={'succeeded','failed','cancelled'}

def sha256(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def source_hashes():
    paths=[*sorted((ROOT/'core').rglob('*.rs')),ROOT/'core/Cargo.lock',ROOT/'contracts/media_core_v0.h']
    return {str(p.relative_to(ROOT)):sha256(p) for p in paths if 'target' not in p.parts}
def captured_at():
    from datetime import datetime,timezone
    return datetime.now(timezone.utc).isoformat()
def report(path,value):
    path=Path(path)
    if not path.is_absolute():path=ROOT/path
    relative=path.relative_to(HERE)
    if '..' in relative.parts:raise ValueError('Evidence escapes own QA tree')
    current=HERE
    for part in relative.parts:
        current/=part
        if current.is_symlink():raise ValueError('Symlink evidence target')
        if current.exists():
            s=current.stat()
            if current==path and (not stat.S_ISREG(s.st_mode) or s.st_nlink!=1):raise ValueError('Unsafe evidence target')
            if current!=path and not stat.S_ISDIR(s.st_mode):raise ValueError('Unsafe evidence ancestor')
    path.parent.mkdir(exist_ok=True,parents=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=path.parent,prefix='.qa-core004-',delete=False) as stream:
            temporary=Path(stream.name);json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.write('\n')
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():temporary.unlink()

class Commands:
    def __init__(self):self.records=[]
    def run(self,argv,expected=0,timeout=120,env=None,binary=False):
        argv=[str(x) for x in argv];start=time.monotonic()
        result=subprocess.run(argv,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout,env=env)
        row={'argv':argv,'returncode':result.returncode,'elapsed_seconds':time.monotonic()-start,
             'stderr':result.stderr.decode(errors='replace')}
        if binary:row.update(stdout_bytes=len(result.stdout),stdout_sha256=hashlib.sha256(result.stdout).hexdigest())
        else:row['stdout']=result.stdout.decode(errors='strict')
        self.records.append(row)
        if expected is not None and result.returncode!=expected:raise AssertionError((argv,result.returncode,row['stderr'][-4000:]))
        if binary:return result.stdout
        result.stdout=row['stdout'];result.stderr=row['stderr'];return result

class Buffer(C.Structure):_fields_=[('ptr',C.POINTER(C.c_uint8)),('len',C.c_size_t)]
def encoded(value):return json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()
class API:
    def __init__(self,path):
        self.path=Path(path).resolve(strict=True);self.lib=C.CDLL(str(self.path));self.outputs=0;self.frees=0
        for name in ['ms_get_version','ms_get_capabilities']:
            function=getattr(self.lib,name);function.argtypes=[C.c_void_p,C.POINTER(Buffer)];function.restype=C.c_int32
        self.lib.ms_engine_create.argtypes=[C.c_void_p,C.c_size_t,C.POINTER(C.c_void_p)];self.lib.ms_engine_create.restype=C.c_int32
        self.lib.ms_engine_destroy.argtypes=[C.c_void_p];self.lib.ms_engine_destroy.restype=C.c_int32
        for name in ['ms_submit_job','ms_get_job_snapshot']:
            function=getattr(self.lib,name);function.argtypes=[C.c_void_p,C.c_void_p,C.c_size_t,C.POINTER(Buffer)];function.restype=C.c_int32
        self.lib.ms_cancel_job.argtypes=[C.c_void_p,C.c_void_p,C.c_size_t];self.lib.ms_cancel_job.restype=C.c_int32
        self.lib.ms_buffer_free.argtypes=[Buffer];self.lib.ms_buffer_free.restype=None
    def decode(self,rc,out):
        self.outputs+=1
        try:
            if rc:
                assert not out.ptr and not out.len,'Core error did not zero original output';return rc,None
            assert out.ptr and out.len,'Core success with empty output'
            value=json.loads(C.string_at(out.ptr,out.len));assert value['schema_version']==0
            return rc,value
        finally:self.lib.ms_buffer_free(out);self.frees+=1
    def create_raw(self,config):
        value=encoded(config) if not isinstance(config,bytes) else config;handle=C.c_void_p()
        rc=self.lib.ms_engine_create(C.c_char_p(value),len(value),C.byref(handle))
        if rc:assert not handle.value
        return rc,Engine(self,handle) if rc==0 else None
    def create(self,config):
        rc,engine=self.create_raw(config);assert rc==0,rc;return engine
class Engine:
    def __init__(self,api,handle):self.api=api;self.handle=handle;self.closed=False;self.jobs=set();self.trace=[]
    def query(self,name):
        out=Buffer();rc,value=self.api.decode(getattr(self.api.lib,name)(self.handle,C.byref(out)),out);assert rc==0,rc;return value
    def submit(self,request):
        data=encoded(request) if not isinstance(request,bytes) else request;out=Buffer()
        rc,value=self.api.decode(self.api.lib.ms_submit_job(self.handle,C.c_char_p(data),len(data),C.byref(out)),out)
        if value:
            if 'job_id' in value:self.jobs.add(value['job_id'])
            self.jobs.update(value.get('job_ids',[]))
        return rc,value
    def snapshot_raw(self,job):
        data=job.encode();out=Buffer()
        return self.api.decode(self.api.lib.ms_get_job_snapshot(self.handle,C.c_char_p(data),len(data),C.byref(out)),out)
    def snapshot(self,job):
        rc,value=self.snapshot_raw(job);assert rc==0,(rc,job);self.trace.append(value);return value
    def wait(self,job,timeout=180):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            value=self.snapshot(job)
            if value['state'] in TERMINAL:return value
            time.sleep(.015)
        raise AssertionError(('Job did not reach terminal',job))
    def cancel(self,job):
        data=job.encode();return self.api.lib.ms_cancel_job(self.handle,C.c_char_p(data),len(data))
    def destroy(self):
        rc=self.api.lib.ms_engine_destroy(self.handle)
        if rc==0:self.closed=True;self.handle=None
        return rc

def configuration(core004=True,ffmpeg=None,ffprobe=None,**overrides):
    import shutil
    backend={'ffmpeg':str(ffmpeg or shutil.which('ffmpeg')),'ffprobe':str(ffprobe or shutil.which('ffprobe')),
             'timeout_ms':30000,'max_output_bytes':8388608,'core003_enabled':True,'accurate_reencode_timeout_ms':600000}
    if core004:backend.update(core004_enabled=True,transcode_timeout_ms=600000)
    backend.update(overrides)
    return {'schema_version':0,'media_backend':backend}
def transcode(source,destination,profile='mp4_h264_standard_v1',audio=None,range_us=None):
    value={'schema_version':0,'operation':'transcode','source':{'uri':Path(source).as_uri()},'destination':{'uri':Path(destination).as_uri()},'profile':profile,'audio_selection':audio or {'mode':'all_supported'}}
    if range_us is not None:value['range']={'start_us':range_us[0],'end_us':range_us[1]}
    return value
def control(operation,**fields):return {'schema_version':0,'operation':operation,**fields}
def accepted(engine,request):
    rc,value=engine.submit(request);assert rc==0,(rc,request);assert value['accepted'],value;return value
