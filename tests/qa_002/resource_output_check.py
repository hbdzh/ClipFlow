#!/usr/bin/env python3
"""Independently decode resource-run exports, without an audio device."""
import argparse,array,hashlib,json,math,subprocess
from pathlib import Path

def sha(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument('--evidence',type=Path,required=True);a=p.parse_args();ev=a.evidence
 cycle=json.loads((ev/'resource-cycles-v2-summary.json').read_text());long=json.loads((ev/'resource-long-summary.json').read_text());assert cycle['result']==long['result']=='PASS'
 sources={}
 for name in ['resource-fixtures.json','resource-fixtures-v2.json']:sources.update(json.loads((ev/name).read_text())['files'])
 assert all(sha(Path(path))==digest for path,digest in sources.items())
 exports=[(Path(x['output']),2,60,320,180) for x in cycle['cycle_outcomes'] if x['state']=='succeeded']
 root=Path(long['argv'][-1]).parent;native=json.loads((ev/'resource-long-native.json').read_text());tasks=native['steps'][-1]['tasks'];assert len(tasks)==8
 assert sum(x['state']=='succeeded' for x in tasks)==2 and sum(x['state']=='cancelled' for x in tasks)==6
 for t in tasks:
  output=root/'outputs'/t['destinationName']
  if t['state']=='succeeded':exports.append((output,600,18000,1920,1080))
  else:assert not output.exists()
 results=[]
 for output,seconds,frames,width,height in exports:
  probe=['/opt/homebrew/bin/ffprobe','-v','error','-show_streams','-show_format','-of','json',str(output)];q=subprocess.run(probe,capture_output=True,text=True,timeout=30);assert q.returncode==0,q.stderr;meta=json.loads(q.stdout);video=[s for s in meta['streams'] if s['codec_type']=='video'];audio=[s for s in meta['streams'] if s['codec_type']=='audio'];assert len(video)==1 and len(video)+len(audio)==len(meta['streams']);v=video[0];assert v['codec_name']=='h264' and (v['width'],v['height'])==(width,height);assert abs(float(meta['format']['duration'])-seconds)<.04
  decode=['/opt/homebrew/bin/ffmpeg','-nostdin','-v','error','-xerror','-i',str(output),'-map','0','-progress','pipe:1','-nostats','-f','null','-'];r=subprocess.run(decode,capture_output=True,text=True,timeout=180);assert r.returncode==0,r.stderr;counts=[int(x.split('=',1)[1]) for x in r.stdout.splitlines() if x.startswith('frame=')];assert counts[-1]==frames,(output,counts[-1],frames)
  pcm=None
  if audio:
   argv=['/opt/homebrew/bin/ffmpeg','-nostdin','-v','error','-i',str(output),'-map','0:a:0','-f','f32le','-'];r2=subprocess.run(argv,capture_output=True,timeout=30);samples=array.array('f');samples.frombytes(r2.stdout);assert r2.returncode==0 and samples and all(map(math.isfinite,samples));peak=max(map(abs,samples));assert peak==0
   pcm=dict(argv=argv,exit=r2.returncode,samples=len(samples),peak_abs=peak,all_finite=True)
  results.append(dict(path=str(output),sha256=sha(output),probe_argv=probe,probe_exit=q.returncode,metadata=meta,decode_argv=decode,decode_exit=r.returncode,actual_decoded_video_frames=counts[-1],progress=r.stdout,stderr=r.stderr,audio=pcm,no_audio_device_opened=True))
 d=dict(result='PASS',scope='New native v2 lifecycle and long-queue successful exports; separate host oracle, null/PCM only',input_hashes_unchanged=True,inputs=sources,outputs=results,output_count=len(results),tool_hashes={n:sha(Path('/opt/homebrew/bin')/n) for n in ['ffmpeg','ffprobe']})
 (ev/'resource-independent-outputs.json').write_text(json.dumps(d,indent=2)+'\n');print('Independent resource outputs PASS:',len(results))
if __name__=='__main__':main()
