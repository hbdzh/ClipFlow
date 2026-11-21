#!/usr/bin/env python3
"""Independent CPU parity for the accepted CPU matrix's >1/full headroom outputs."""
import argparse,json
from pathlib import Path
import numpy as np
import onnxruntime as ort
import torch
from openunmix.model import OpenUnmix
from ai_quality_qa import MODEL_SHA,TORCH_SHA,sha,signal,references,error,peak,write

p=argparse.ArgumentParser(description=__doc__);p.add_argument('--app',required=True,type=Path);p.add_argument('--cpu-root',required=True,type=Path);p.add_argument('--report',required=True,type=Path);a=p.parse_args()
modelpath=a.app/'Contents/Resources/ai/model.onnx';weights=Path('/private/tmp/mediastudio-ai001/umx-original.pth')
assert sha(modelpath)==MODEL_SHA and sha(weights)==TORCH_SHA
torch.manual_seed(0);torch.set_num_threads(2);model=OpenUnmix(nb_bins=2049,nb_channels=2,hidden_size=512,max_bin=1487).eval();mapped=model.load_state_dict(torch.load(weights,map_location='cpu',weights_only=True),strict=False)
assert not mapped.missing_keys and sorted(mapped.unexpected_keys)==['sample_rate','stft.window','transform.0.window']
opts=ort.SessionOptions();opts.intra_op_num_threads=2;opts.inter_op_num_threads=1;session=ort.InferenceSession(str(modelpath),sess_options=opts,providers=['CPUExecutionProvider'])
cases=[]
for name in ['peak-headroom','peak-exact-two']:
 source=a.cpu_root/(name+'.wav');destination=a.cpu_root/(name+'-result');x=signal(source);refs,frames=references(x,session,model);stems={}
 assert peak(x)>1 and peak(x)<=2
 for stem in ['vocals','accompaniment']:
  path=destination/(stem+'.wav');got=signal(path);assert got.shape==x.shape;comparisons={kind:error(got,v[stem]) for kind,v in refs.items()}
  assert all(v['passed'] for v in comparisons.values()),comparisons;stems[stem]={'sha256':sha(path),'reference_comparisons':comparisons}
 cases.append({'name':name,'source':str(source),'source_sha256':sha(source),'input_peak':peak(x),'frames':len(x),'stft_frames':frames,'stems':stems})
value={'result':'PASS','scope':'Independent same pinned full ONNX CPU + upstream Torch CPU comparison of outputs already produced by final CPU52 matrix; no output-dependent alignment/gain','core_sha256':sha(a.app/'Contents/Frameworks/libmedia_ffi.dylib'),'model_sha256':sha(modelpath),'upstream_weights_sha256':sha(weights),'ort_version':ort.__version__,'torch_version':torch.__version__,'seed':0,'cases':cases}
write(a.report,value);print(json.dumps({'result':'PASS','cases':[(v['name'],v['input_peak']) for v in cases]}))
