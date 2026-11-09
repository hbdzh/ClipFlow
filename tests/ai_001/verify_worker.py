#!/usr/bin/env python3
"""Independent real streaming-worker quality checks using existing QA fixtures.

This is a subprocess worker experiment, not a Core/FFI production acceptance.
Fixtures are local files with independently recorded source rights and hashes.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from verify_audio import check_audio, metrics, sha

ROOT = Path(__file__).resolve().parents[2]


def run(args):
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        raise ValueError('ffmpeg and ffprobe must be present')
    before = {name: sha(path) for name, path in [('worker',args.worker),('model',args.model),('weights',args.weights)]}
    cases = []
    with tempfile.TemporaryDirectory(prefix='ai001-qa-real-worker-',dir=args.temp_parent) as temp:
        root = Path(temp)
        root.chmod(0o700)
        for i in (1,3,5):
            case = args.fixture_parent/f'case-{i}'
            source = case/'mix.wav'
            stage = root/f'case-{i}'
            stage.mkdir(mode=0o700)
            input_hash = sha(source)
            begin = time.monotonic()
            proc = subprocess.run([str(args.python),str(args.worker),'--model',str(args.model),
                                   '--input',str(source),'--staging',str(stage)],
                                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=120, check=False)
            if proc.returncode:
                raise AssertionError(('worker nonzero',i,proc.returncode,proc.stdout.decode(errors='replace')[-1000:]))
            response = json.loads(proc.stdout)
            if response != {'schema_version':1,'model_id':'open-unmix-umxhq-vocals-onnx-e06097d3',
                            'frames':132000,'sample_rate':44100,'channels':2,
                            'outputs':['vocals.wav','accompaniment.wav']}:
                raise AssertionError(('response contract mismatch',i,response))
            if {p.name for p in stage.iterdir()} != {'vocals.wav','accompaniment.wav'}:
                raise AssertionError('scratch residue/unknown output')
            if sha(source) != input_hash:
                raise AssertionError('source changed')
            stems = {}
            for name,reference in [('vocals',case/'voice.wav'),('accompaniment',case/'piano.wav')]:
                candidate=stage/f'{name}.wav'
                details=check_audio(candidate,ffprobe,ffmpeg)
                if details['frames'] != response['frames']:
                    raise AssertionError('wrong sample count')
                clean=metrics(reference,candidate)
                mixed=metrics(reference,source)
                details['baseline_si_sdr_db']=mixed['si_sdr_db']
                details['output_si_sdr_db']=clean['si_sdr_db']
                details['si_sdr_improvement_db']=clean['si_sdr_db']-mixed['si_sdr_db']
                stems[name]=details
            refproc=subprocess.run([str(args.python),str(ROOT/'docs'/'ai-001'/'compare_audio_upstream.py'),
                                    str(args.weights),str(source),str(stage)],stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120,check=True,text=True)
            comparison=[json.loads(line) for line in refproc.stdout.splitlines() if line.strip()]
            if len(comparison)!=2 or any(v['relative_rms_error']>1e-4 for v in comparison):
                raise AssertionError(('torch reference mismatch',i,comparison))
            cases.append({'case':i,'input_sha256':input_hash,'worker_stdout':response,'stems':stems,
                          'torch_cpu_reference':comparison,'elapsed_seconds':time.monotonic()-begin})
        after = {name:sha(path) for name,path in [('worker',args.worker),('model',args.model),('weights',args.weights)]}
        if before != after:
            raise AssertionError('source or model drift during test')
    return {'result':'PASS','scope':'three real authorized short vocal/piano controlled mixes; independent subprocess worker QA',
            'hashes':before,'cases':cases,'temporary_directory_removed':not root.exists(),
            'not_run':['Core lifecycle','ten-minute source','two-human listening','complete AC21 matrix','process crash and cleanup']}


def main():
    p=argparse.ArgumentParser()
    for name in ['python','worker','model','weights','fixture-parent','temp-parent']:
        p.add_argument('--'+name,type=lambda x:Path(x).absolute(),required=True)
    print(json.dumps(run(p.parse_args()),indent=2,ensure_ascii=False,allow_nan=False))


if __name__ == '__main__': main()
