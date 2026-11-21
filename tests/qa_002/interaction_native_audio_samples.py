#!/usr/bin/env python3
"""Fixed-origin PCM comparison; no lag search, normalization, or realignment."""
import argparse,hashlib,json,subprocess
from pathlib import Path
import numpy as np
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def metrics(reference,actual):
 def error(r,a):
  power=float(np.mean(r*r));return float(np.sqrt(np.mean((r-a)**2)/power)) if power>1e-20 else float(np.max(np.abs(r-a)))
 return {'all':error(reference,actual),'first4096':error(reference[:4096],actual[:4096]),'last4096':error(reference[-4096:],actual[-4096:])}
def accepted(reference,actual):return all(v<=1e-3 for v in metrics(reference,actual).values())
def main():
 p=argparse.ArgumentParser();p.add_argument('--native-report',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();report=json.loads(a.native_report.read_text());cases=[c['detail'] for c in report['checks'] if c['name']=='real-audio-first-pcm-and-frame-count' and c['result']=='PASS'];assert cases;rows=[];commands=[]
 for c in cases:
  source=Path(c['source']);pcm=Path(c['pcm']);channels=c['channels'];rate=c['sample_rate'];n=c['frames'];duration=c['content_duration_us'];actual=np.fromfile(pcm,dtype='<f4').astype(np.float64).reshape((-1,channels));assert len(actual)==n and np.isfinite(actual).all()
  argv=['/opt/homebrew/bin/ffmpeg','-v','error','-i',str(source),'-map','0:a:0','-t',f'{duration//1000000}.{duration%1000000:06d}','-c:a','pcm_f32le','-f','f32le','-'];r=subprocess.run(argv,capture_output=True,check=True);commands.append({'argv':argv,'exit':r.returncode,'stderr':r.stderr.decode()});reference=np.frombuffer(r.stdout,dtype='<f4').astype(np.float64).reshape((-1,channels));assert reference.shape==actual.shape and np.isfinite(reference).all()
  one=np.concatenate([actual[1:],np.zeros_like(actual[:1])]);head=actual.copy();head[:512]=0;tail=actual.copy();tail[-512:]=0
  controls={'skip_first_sample_rejected':not accepted(reference,one),'replace_first512_with_zero_rejected':not accepted(reference,head),'replace_last512_with_zero_rejected':not accepted(reference,tail)}
  values=metrics(reference,actual);ok=accepted(reference,actual) and all(controls.values())
  rows.append({'source':str(source),'source_sha256':sha(source),'actual_preview_asset':c['player_url'],'captured_pcm_sha256':sha(pcm),'reference_pcm_sha256':hashlib.sha256(r.stdout).hexdigest(),'sample_rate':rate,'channels':channels,'frames':n,'first_item_pts_us':c['first_item_pts_us'],'expected_item_pts_us':c['expected_item_pts_us'],'relative_rms':values,'checker_controls':controls,'result':'PASS' if ok else 'FAIL'})
 result={'result':'PASS' if all(r['result']=='PASS' for r in rows) else 'FAIL','scope':'Actual AVAssetReader PCM from the production current preview item asset, fixed canonical first sample and full/first/last windows versus independent FFmpeg source decode with Core content end; no temporal shift or gain','threshold_relative_rms':1e-3,'cases':rows,'commands':commands,'checker_sha256':sha(Path(__file__))};a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'result':result['result'],'count':len(rows),'max_relative_rms':max(max(r['relative_rms'].values()) for r in rows)}));assert result['result']=='PASS'
if __name__=='__main__':main()
