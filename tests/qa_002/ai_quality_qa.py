#!/usr/bin/env python3
"""QA-002 fixed native Core/CPU outputs vs independent full ONNX/Torch references.

All media remain local. Cohort and mix gains are written before inference.
Human scores are never generated. No output-dependent alignment or normalization.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time
import traceback

import numpy as np
import onnxruntime as ort
import soundfile as sf
import torch
from openunmix.model import OpenUnmix

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tests/core_004'))
from support import API, configuration

MODEL_SHA = 'f27742bb52b24cb039614dc76d0764e279df3eb2a9d6daa82c6daee3369ec747'
TORCH_SHA = 'b62c91cedbc7a066f1778ead5b5cecb377aa3a46a31af1cce7c5c8769339d083'
RATE = 44100
STEMS = ('vocals', 'accompaniment')

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p, d): Path(p).write_text(json.dumps(d, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
def rms(x): return float(np.sqrt(np.mean(np.square(x.astype('float64')))))
def peak(x): return float(np.max(np.abs(x)))
def signal(p):
    x, rate = sf.read(p, dtype='float32', always_2d=True)
    assert rate == RATE and x.shape[1] == 2 and np.isfinite(x).all()
    return x
def sisdr(s, y):
    s, y = s.astype('float64').reshape(-1), y.astype('float64').reshape(-1)
    assert np.dot(s, s) > 1e-12
    target = np.dot(y, s)/np.dot(s, s)*s
    return float(10*np.log10(max(float(np.dot(target,target)),1e-12)/max(float(np.dot(y-target,y-target)),1e-12)))

def references(x, session, model):
    # Entire centered sequence, floor(N/hop)+1 frames; no extra right-hop frame.
    with torch.inference_mode():
        audio = torch.from_numpy(x.T.copy())
        win = torch.hann_window(4096, periodic=True)
        z = torch.stft(audio, n_fft=4096, hop_length=1024, window=win,
                       center=True, pad_mode='reflect', return_complex=True)
        mag = z.abs()
        ort_prediction = session.run(None, {session.get_inputs()[0].name: mag.unsqueeze(0).numpy()})[0][0]
        torch_prediction = model(mag.unsqueeze(0))[0]
        outputs = {}
        for name, prediction in [('onnx_cpu', torch.from_numpy(ort_prediction)), ('torch_cpu', torch_prediction)]:
            mask = (prediction/torch.clamp(mag,min=1e-8)).clamp(0,1)
            voice = torch.istft(z*mask, n_fft=4096, hop_length=1024,
                window=win, center=True, length=len(x)).T.numpy()
            outputs[name] = {'vocals':voice, 'accompaniment':x-voice}
        return outputs, int(mag.shape[-1])

def error(got, ref):
    assert got.shape == ref.shape and got.ndim == 2 and got.shape[1] == 2 and len(got) > 0
    difference = got.astype('float64')-ref.astype('float64')
    ref_rms = rms(ref)
    absolute = peak(difference)
    normalized = rms(difference)/max(ref_rms,1e-8)
    return {'reference_rms':ref_rms,'peak_absolute_error':absolute,
            'relative_rms_error':normalized,
            'criterion':'peak<=1e-7' if ref_rms<1e-8 else 'relative_rms<=1e-4',
            'passed':absolute<=1e-7 if ref_rms<1e-8 else normalized<=1e-4}

def pulse_locations(got, ref):
    locations=[{'channel':c,'native_peak_sample':int(np.argmax(np.abs(got[:,c]))),
                'reference_peak_sample':int(np.argmax(np.abs(ref[:,c])))} for c in range(2)]
    assert all(abs(v['native_peak_sample']-v['reference_peak_sample'])<=1 for v in locations)
    return locations

def build_cases(args, commands):
    work = args.work
    prepared = work/'prepared';prepared.mkdir()
    raw = args.raw_inputs
    inputs = {}
    source_records = []
    for key, source in [('v1',raw/'vocalset-1.wav'),('v3',raw/'vocalset-3.wav'),('v5',raw/'vocalset-5.wav'),
        ('p0',raw/'singset-0-label.wav'),('p10',raw/'singset-10-label.wav'),
        ('male',args.male),
        ('female',args.inputs/'librispeech-female-121.flac')]:
        target=prepared/(key+'.wav')
        command=[args.ffmpeg,'-nostdin','-v','error','-i',str(source),'-ar','44100','-ac','2','-c:a','pcm_f32le',str(target)]
        r=subprocess.run(command,capture_output=True,text=True,check=True)
        commands.append({'argv':command,'exit':r.returncode})
        inputs[key]=signal(target)
        source_records.append({'key':key,'path':str(source),'sha256':sha(source),'converted_sha256':sha(target)})
    if args.mandarin:
        target=prepared/'mandarin.wav'
        command=[args.ffmpeg,'-nostdin','-v','error','-i',str(args.mandarin),'-ar','44100','-ac','2','-c:a','pcm_f32le',str(target)]
        r=subprocess.run(command,capture_output=True,text=True,check=True);commands.append({'argv':command,'exit':r.returncode})
        inputs['mandarin']=signal(target);source_records.append({'key':'mandarin','path':str(args.mandarin),'sha256':sha(args.mandarin),'converted_sha256':sha(target)})
    cases=[]
    def add(name, voice, accomp, labels, algorithm, mixture=False, blind=True):
        assert voice.shape==accomp.shape and voice.shape[1]==2
        mix=(voice+accomp).astype('float32')
        gain=0.8/max(peak(mix),1.0)
        voice=(voice*gain).astype('float32');accomp=(accomp*gain).astype('float32');mix=(voice+accomp).astype('float32')
        case=work/('case-'+name);case.mkdir()
        for label,data in [('mix',mix),('clean-vocals',voice),('clean-accompaniment',accomp)]:sf.write(case/(label+'.wav'),data,RATE,subtype='FLOAT')
        cases.append({'id':name,'labels':labels,'frames':len(mix),'source_gain':gain,'mix_algorithm':algorithm,
                      'double_nonzero_mixture':mixture,'blind_listening':blind,'folder':str(case),
                      'input_hashes':{label:sha(case/(label+'.wav')) for label in ['mix','clean-vocals','clean-accompaniment']}})
    v=inputs['v1'];add('01',v,np.zeros_like(v),['pure_singing_voice'], 'VocalSet straight row1, zero accompaniment')
    p=inputs['p0'][:RATE*4];add('02',np.zeros_like(p),p,['pure_accompaniment'], 'SingSet piano row0 first4s, zero voice')
    for name,key,pkey in [('03','v1','p0'),('04','v3','p10')]:
        v=inputs[key];p=inputs[pkey][:len(v)].copy();gain=rms(v)/rms(p);p*=gain
        add(name,v,p,['singing_mix',key,pkey],f'{key} + {pkey}[0:N] times voiceRMS/pianoRMS={gain:.17g}',True)
    v=inputs['v5'];p0=inputs['p0'][:len(v)].copy();p10=inputs['p10'][:len(v)].copy();p0*=rms(v)/rms(p0);p10*=0.5*rms(v)/rms(p10)
    rng=np.random.default_rng(20261114);noise=rng.normal(size=(len(v),2)).astype('float32')
    # Deterministic short broadband transients, an extra percussion-like layer.
    envelope=np.zeros(len(v),dtype='float32')
    for start in range(0,len(v),RATE//4):
        n=min(RATE//20,len(v)-start);envelope[start:start+n]=np.exp(-np.arange(n)/(RATE/150))
    noise*=envelope[:,None];noise*=0.15*rms(v)/rms(noise)
    add('05',v,p0+p10+noise,['dense_singing_mix','two_pianos','generated_noise_percussion'],
        'VocalSet vibrato row5 + two independent piano rows; piano RMS ratios1,0.5 + seeded broadband transients RMS ratio0.15; seed20261114',True)
    chunks=[]
    for key in ['male','female']+(['mandarin'] if 'mandarin' in inputs else []):
        x=inputs[key].copy();x*=0.12/max(rms(x),1e-8);chunks.append(x);chunks.append(np.zeros((RATE//4,2),dtype='float32'))
    speech=np.concatenate(chunks)
    add('06',speech,np.zeros_like(speech),['pure_speech','male_english','female_english']+(['mandarin'] if 'mandarin'in inputs else []),
        'Entire attributed utterances, fixed RMS0.12 per source before concatenation; 0.25s zero gaps; no inference-dependent change')
    silence=np.zeros((RATE*3,2),dtype='float32');add('07',silence,silence,['all_zero_silence'],'All samples exactly0',blind=False)
    impulse=np.zeros((RATE*4+137,2),dtype='float32');impulse[0]=[.5,-.5];impulse[4096]=[-.3,.3];impulse[-1]=[.25,-.25]
    add('08',impulse,np.zeros_like(impulse),['endpoint_impulses','non_hop_multiple'],'N=4*44100+137; impulses at0,4096,N-1, left/right opposite; no alignment search',blind=False)
    return cases,source_records

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['app','worker','work','inputs','evidence']:p.add_argument('--'+name,required=True,type=Path)
    p.add_argument('--mandarin',type=Path)
    p.add_argument('--raw-inputs',type=Path,default=Path('/private/tmp/mediastudio-ai001/qa'))
    p.add_argument('--male',type=Path,default=Path('/private/tmp/mediastudio-ai001/librispeech/LibriSpeech/test-clean/6930/75918/6930-75918-0000.flac'))
    p.add_argument('--ffmpeg',default='/opt/homebrew/bin/ffmpeg');p.add_argument('--ffprobe',default='/opt/homebrew/bin/ffprobe')
    p.add_argument('--weights',type=Path,default=Path('/private/tmp/mediastudio-ai001/umx-original.pth'))
    args=p.parse_args();args.app=args.app.resolve();args.work=args.work.resolve();args.work.mkdir(mode=0o700,exist_ok=False);args.evidence.mkdir(parents=True,exist_ok=True)
    modelpath=args.app/'Contents/Resources/ai/model.onnx';assert sha(modelpath)==MODEL_SHA and sha(args.weights)==TORCH_SHA
    before={str(f.relative_to(args.app)):sha(f) for f in args.app.rglob('*') if f.is_file()}
    commands=[];cases,sources=build_cases(args,commands)
    manifest={'seed':20261114,'sources':sources,'cases':cases,'declared_before_inference':True,'no_output_dependent_alignment_or_gain':True}
    write(args.evidence/'quality-cohort.json',manifest)
    torch.manual_seed(0);np.random.seed(0);torch.set_num_threads(2)
    model=OpenUnmix(nb_bins=2049,nb_channels=2,hidden_size=512,max_bin=1487).eval()
    state=torch.load(args.weights,map_location='cpu',weights_only=True);mapped=model.load_state_dict(state,strict=False)
    assert not mapped.missing_keys and sorted(mapped.unexpected_keys)==['sample_rate','stft.window','transform.0.window']
    opts=ort.SessionOptions();opts.intra_op_num_threads=2;opts.inter_op_num_threads=1
    session=ort.InferenceSession(str(modelpath),sess_options=opts,providers=['CPUExecutionProvider'])
    api=API(args.app/'Contents/Frameworks/libmedia_ffi.dylib');config=configuration(ffmpeg=args.ffmpeg,ffprobe=args.ffprobe)
    config['media_backend']['ai_backend']={'native_worker':str(args.worker.resolve()),'native_graphs':str(args.app/'Contents/Resources/ai/graphs'),
        'native_runtime':str(args.app/'Contents/Frameworks/libonnxruntime.dylib'),'model':str(modelpath),'timeout_ms':600000,'development_root':str(args.work)}
    engine=api.create(config);checks=[];failure=None
    try:
        for case in cases:
            folder=Path(case['folder']);source=args.work/('source-'+case['id']+'.wav');shutil.copy2(folder/'mix.wav',source);destination=args.work/('result-'+case['id']);t=time.monotonic()
            rc,receipt=engine.submit({'schema_version':0,'operation':'ai_separate','source':{'uri':source.as_uri()},'destination':{'uri':destination.as_uri()},'model_id':'umx_hq_vocals_onnx_v1'})
            assert rc==0 and receipt.get('job_id'),(rc,receipt)
            terminal=engine.wait(receipt['job_id'],timeout=650);assert terminal['state']=='succeeded',terminal
            x=signal(source);refs,stftframes=references(x,session,model);row={'case':case['id'],'frames':len(x),'stft_frames':stftframes,'receipt':receipt,'terminal':terminal,'seconds':time.monotonic()-t,'stems':{}}
            for kind,values in refs.items():
                directory=folder/kind;directory.mkdir()
                for stem,value in values.items():sf.write(directory/(stem+'.wav'),value,RATE,subtype='FLOAT')
            for stem in STEMS:
                got=signal(destination/(stem+'.wav'));assert got.shape==x.shape
                check={'sha256':sha(destination/(stem+'.wav')),'peak':peak(got),'rms':rms(got),'reference_comparisons':{k:error(got,values[stem]) for k,values in refs.items()}}
                assert all(v['passed'] for v in check['reference_comparisons'].values()),check
                clean=signal(folder/('clean-'+stem+'.wav'))
                if case['double_nonzero_mixture']:
                    old=sisdr(clean,x);new=sisdr(clean,got);check['si_sdr']={'mix_db':old,'output_db':new,'improvement_db':new-old}
                if case['id']=='07':assert peak(got)<=1e-6
                if case['id']=='08':
                    expected=refs['onnx_cpu'][stem]
                    check['pulse_peak_locations']=pulse_locations(got,expected)
                command=[args.ffmpeg,'-nostdin','-v','error','-xerror','-i',str(destination/(stem+'.wav')),'-map','0:a:0','-f','null','-']
                dec=subprocess.run(command,capture_output=True,text=True,check=True);commands.append({'argv':command,'exit':dec.returncode});check['full_decode_exit']=dec.returncode
                row['stems'][stem]=check
            reconstruction=signal(destination/'vocals.wav')+signal(destination/'accompaniment.wav')
            row['reconstruction_peak_error']=peak(reconstruction-x);row['endpoint_reconstruction_error']=peak((reconstruction-x)[[0,-1]])
            assert row['reconstruction_peak_error']<=2e-7 and row['endpoint_reconstruction_error']<=2e-7
            assert sha(source)==case['input_hashes']['mix'];checks.append(row)
            write(args.evidence/'quality-progress.json',{'checks':checks})
            print('case',case['id'],'PASS',flush=True)
        assert engine.destroy()==0 and api.outputs==api.frees
        medians={stem:float(np.median([r['stems'][stem]['si_sdr']['improvement_db'] for r in checks if 'si_sdr'in r['stems'][stem]])) for stem in STEMS}
        assert all(v>=0 for v in medians.values()),medians
        assert before=={str(f.relative_to(args.app)):sha(f) for f in args.app.rglob('*') if f.is_file()}
        result='PASS'
    except Exception:
        failure=traceback.format_exc();result='FAIL'
        if not engine.closed:
            for job in engine.jobs:
                try:engine.cancel(job);engine.wait(job,timeout=15)
                except Exception:pass
            engine.destroy()
        medians={}
    value={'result':result,'scope':'Actual signed final Core + native worker executable sections identical to final bundle, re-signed for non-sandbox QA parent; not final helper signing or GUI evidence',
        'core_sha256':sha(args.app/'Contents/Frameworks/libmedia_ffi.dylib'),'worker_sha256':sha(args.worker),'model_sha256':sha(modelpath),'upstream_weights_sha256':sha(args.weights),
        'versions':{'numpy':np.__version__,'torch':torch.__version__,'onnxruntime':ort.__version__,'provider':session.get_providers()},'seed':0,
        'cohort_manifest':'quality-cohort.json','cases':checks,'median_si_sdr_improvement_db':medians,'ffi_outputs':api.outputs,'ffi_frees':api.frees,
        'commands':commands,'failure':failure,'human_listening':'NOT_RUN; awaiting two independent human score sheets','audio_root':str(args.work)}
    write(args.evidence/'quality-results.json',value);print(json.dumps({'result':result,'medians':medians,'failure':failure},ensure_ascii=False))
    return 0 if result=='PASS' else 1

if __name__=='__main__':raise SystemExit(main())
