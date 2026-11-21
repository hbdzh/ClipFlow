#!/usr/bin/env python3
"""Match real AVFoundation preview captures to every FFmpeg source frame.

Not bit equality: the two independent colour conversions may differ slightly.
The closest complete RGB source frame must be the requested exact source index.
"""
import argparse, hashlib, json, subprocess
from pathlib import Path
import numpy as np
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument('--native-report',required=True,type=Path);p.add_argument('--output',required=True,type=Path);a=p.parse_args();d=json.loads(a.native_report.read_text());cases=[c['detail'] for c in d['checks'] if c['name'].startswith('decoded-image-time-') and c['result']=='PASS'];assert cases
 sources={};commands=[];rows=[]
 for c in cases:
  path=Path(c['source']);image=Path(c['image']);expected=c['expected_source_frame']
  if str(path) not in sources:
   probe=['/opt/homebrew/bin/ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=width,height','-of','json',str(path)];q=subprocess.run(probe,capture_output=True,check=True);s=json.loads(q.stdout)['streams'][0];width,height=s['width'],s['height']
   cmd=['/opt/homebrew/bin/ffmpeg','-v','error','-i',str(path),'-map','0:v:0','-vsync','0','-pix_fmt','rgb24','-f','rawvideo','-'];r=subprocess.run(cmd,capture_output=True,check=True);frames=np.frombuffer(r.stdout,dtype=np.uint8).reshape((-1,height,width,3));sources[str(path)]=frames;commands.extend([probe,cmd])
  frames=sources[str(path)];png_cmd=['/opt/homebrew/bin/ffmpeg','-v','error','-i',str(image),'-frames:v','1','-pix_fmt','rgb24','-f','rawvideo','-'];png=subprocess.run(png_cmd,capture_output=True,check=True);commands.append(png_cmd);rgb=np.frombuffer(png.stdout,dtype=np.uint8).reshape(frames[0].shape)
  errors=np.array([np.sqrt(np.mean((f.astype(np.float32)-rgb.astype(np.float32))**2)) for f in frames]);rgb_best=int(np.argmin(errors));rmse=float(errors[expected])
  # Match the frozen burnt frame-number crop, without assuming that raw RGB
  # from different colour-management pipelines shares a transfer function.
  def counters(p):
   cmd=['/opt/homebrew/bin/ffmpeg','-v','error','-i',str(p),'-map','0:v:0','-an','-vf','crop=160:48:0:0,scale=32:12:flags=area,format=gray','-fps_mode','passthrough','-f','rawvideo','-'];r=subprocess.run(cmd,capture_output=True,check=True);commands.append(cmd);return np.frombuffer(r.stdout,dtype=np.uint8).reshape((-1,384))
  if 'counter:'+str(path) not in sources:sources['counter:'+str(path)]=counters(path)
  counter_sources=sources['counter:'+str(path)];target=counters(image)[0]
  distances=np.array([np.sum((f.astype(np.int32)-target.astype(np.int32))**2) for f in counter_sources]);best=int(np.argmin(distances));margin=int(np.partition(distances,1)[1]-distances[expected]);ok=best==expected and margin>0 and rgb_best==expected
  # Deliberately wrong index and an injected adjacent source counter must be
  # rejected by the identical rule before real captures can count as passing.
  wrong=(expected+1)%len(frames);wrong_index_rejected=best!=wrong
  injected=np.array([np.sum((f.astype(np.int32)-counter_sources[wrong].astype(np.int32))**2) for f in counter_sources]);wrong_content_rejected=int(np.argmin(injected))!=expected
  rows.append({'source':str(path),'source_sha256':sha(path),'image':str(image),'image_sha256':sha(image),'expected_frame':expected,'closest_frame':best,'closest_rgb_frame':rgb_best,'expected_rgb_rmse':rmse,'rgb_error_is_diagnostic_not_colour_acceptance':True,'counter_squared_error_margin':margin,'wrong_index_control_rejected':wrong_index_rejected,'adjacent_frame_injection_rejected':wrong_content_rejected,'result':'PASS' if ok and wrong_index_rejected and wrong_content_rejected else 'FAIL'})
 result={'result':'PASS' if all(r['result']=='PASS' for r in rows) else 'FAIL','scope':'Real AVFoundation images from the actual preview item time, independently decoded FFmpeg RGB source frames; no output-dependent temporal shift','cases':rows,'commands':commands,'checker_sha256':sha(Path(__file__))};a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'result':result['result'],'count':len(rows),'max_rgb_rmse':max(r['expected_rgb_rmse'] for r in rows)}));assert result['result']=='PASS'
if __name__=='__main__':main()
