#!/usr/bin/env python3
"""Read the actual packaged ORT C API version/providers without model execution.

OrtApi is a function-pointer table. Field order comes from the exact locked
ort-sys 2.0.0-rc.15 bindings; duplicate wasm-only alternatives are excluded by
keeping the native first declaration. No ownership crosses into product code.
"""
import argparse,ctypes as C,hashlib,json,re
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--runtime',type=Path,required=True);p.add_argument('--bindings',type=Path,required=True);p.add_argument('--report',type=Path,required=True);a=p.parse_args();assert not a.report.exists();assert a.bindings.parent.parent.name=='ort-sys-2.0.0-rc.15'
text=a.bindings.read_text().split('pub struct OrtApi {',1)[1].split('\n}',1)[0];fields=list(dict.fromkeys(re.findall(r'^\tpub (\w+):',text,re.M)))
assert fields.index('GetAvailableProviders')==125 and fields.index('ReleaseAvailableProviders')==126
lib=C.CDLL(str(a.runtime.resolve()));lib.OrtGetApiBase.argtypes=[];lib.OrtGetApiBase.restype=C.c_void_p;base=lib.OrtGetApiBase();assert base
base_table=(C.c_void_p*2).from_address(base);get_api=C.CFUNCTYPE(C.c_void_p,C.c_uint32)(base_table[0]);version=C.CFUNCTYPE(C.c_char_p)(base_table[1])().decode();api=get_api(21);assert api and version=='1.22.1',version;table=(C.c_void_p*127).from_address(api)
get_error=C.CFUNCTYPE(C.c_char_p,C.c_void_p)(table[fields.index('GetErrorMessage')]);release_status=C.CFUNCTYPE(None,C.c_void_p)(table[fields.index('ReleaseStatus')])
def checked(status):
 if status:
  message=get_error(status).decode();release_status(status);raise RuntimeError(message)
get=C.CFUNCTYPE(C.c_void_p,C.POINTER(C.POINTER(C.c_char_p)),C.POINTER(C.c_int))(table[125]);release=C.CFUNCTYPE(C.c_void_p,C.POINTER(C.c_char_p),C.c_int)(table[126]);providers=C.POINTER(C.c_char_p)();count=C.c_int();checked(get(C.byref(providers),C.byref(count)));assert 0<count.value<32
try:names=[providers[i].decode() for i in range(count.value)]
finally:checked(release(providers,count.value))
assert 'CPUExecutionProvider' in names,names
sha=lambda f:hashlib.sha256(f.read_bytes()).hexdigest()
d={'test_script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'result':'PASS','scope':'Actual final-package ORT library C API query; available providers are build capabilities, not a per-node execution profile','runtime':str(a.runtime.resolve()),'runtime_sha256':sha(a.runtime),'runtime_version':version,'api_version':21,'bindings':str(a.bindings),'bindings_sha256':sha(a.bindings),'queried_function_ordinals':{'GetAvailableProviders':125,'ReleaseAvailableProviders':126},'available_providers':names,'native_worker_session_policy':{'source':'core/native-ai-worker/src/main.rs','environment':'ort::init_from(runtime).build()','registered_non_CPU_execution_providers':[],'intra_threads':2,'inter_threads':1,'explanation':'Worker never registers CoreML/CUDA/etc.; ORT default session execution uses CPU. Availability alone does not mean a provider was selected.'}}
a.report.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n');print(json.dumps(d,ensure_ascii=False))
