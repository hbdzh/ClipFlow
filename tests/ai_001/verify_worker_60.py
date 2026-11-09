#!/usr/bin/env python3
"""60s direct worker and independent CPU audio-reference comparison."""
import argparse,json,subprocess,tempfile
from pathlib import Path
from verify_audio import sha,check_audio
import shutil
ROOT=Path(__file__).resolve().parents[2]
def main():
 p=argparse.ArgumentParser()
 for name in ('python','worker','model','weights','source','temp-parent'):p.add_argument('--'+name,type=lambda s:Path(s).absolute(),required=True)
 a=p.parse_args(); paths=[a.worker,a.model,a.weights,a.source]; hashes={str(i):sha(v) for i,v in enumerate(paths)}
 ffmpeg,ffprobe=shutil.which('ffmpeg'),shutil.which('ffprobe')
 if not ffmpeg or not ffprobe:raise ValueError('missing tools')
 with tempfile.TemporaryDirectory(prefix='ai001-worker60-',dir=a.temp_parent) as d:
  stage=Path(d);stage.chmod(0o700)
  proc=subprocess.run([str(a.python),str(a.worker),'--model',str(a.model),'--input',str(a.source),'--staging',str(stage)],capture_output=True,text=True,timeout=120,check=True)
  response=json.loads(proc.stdout)
  if response.get('frames')!=60*44100:raise AssertionError(response)
  if {q.name for q in stage.iterdir()}!={'vocals.wav','accompaniment.wav'}:raise AssertionError('unrecognized artifacts')
  output={name:check_audio(stage/(name+'.wav'),ffprobe,ffmpeg) for name in ('vocals','accompaniment')}
  ref=subprocess.run([str(a.python),str(ROOT/'docs/ai-001/compare_audio_upstream.py'),str(a.weights),str(a.source),str(stage)],capture_output=True,text=True,timeout=120,check=True)
  parity=[json.loads(l) for l in ref.stdout.splitlines() if l.strip()]
  if len(parity)!=2 or any(q['relative_rms_error']>1e-4 for q in parity):raise AssertionError(parity)
  if hashes!={str(i):sha(v) for i,v in enumerate(paths)}:raise AssertionError('file drift')
 print(json.dumps({'result':'PASS','worker_sha256':hashes['0'],'model_sha256':hashes['1'],'input_sha256':hashes['3'],'worker_response':response,'output':output,'torch_reference':parity,'temp_removed':not stage.exists(),'scope':'60 seconds real worker, not Core or 10-minute reference'},indent=2))
if __name__=='__main__':main()
