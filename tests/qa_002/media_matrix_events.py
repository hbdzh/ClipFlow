"""Frame flash and independently located audio bursts; no inferred lip-sync claim."""
from array import array
from fractions import Fraction
from pathlib import Path
import json,math
from support import sha256
import media_verify as mv
FONT='/System/Library/Fonts/Menlo.ttc'

def prepare(s):
 for name,rate in [('24','24'),('25','25'),('30','30'),('60','60'),('23976','24000/1001'),('2997','30000/1001')]:
  f=Fraction(rate);period=float(1/f)
  vf=f'drawbox=x=0:y=0:w=160:h=48:color=black:t=fill,drawtext=fontfile={FONT}:text=%{{n}}:x=8:y=4:fontsize=32:fontcolor=white,drawbox=x=200:y=90:w=80:h=60:color=black:t=fill'
  for event in [1.5,2.5]:vf+=f",drawbox=x=200:y=90:w=80:h=60:color=white:t=fill:enable='gte(t,{event})*lt(t,{event+period:.12f})'"
  tone='aevalsrc=0.8*sin(2*PI*1000*t)*(between(t\\,1.5\\,1.52)+between(t\\,2.5\\,2.52)):s=48000:d=4'
  p=s.work/('flash-'+name+'.mp4')
  s.c.run(s.prefix+['-f','lavfi','-i',f'color=c=gray:size=320x180:rate={rate}:duration=4','-f','lavfi','-i',tone,'-vf',vf,'-c:v','libx264','-threads','1','-crf','14','-g',str(round(float(f))),'-sc_threshold','0','-bf','2','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k',p]);s.files['flash-'+name]=p
 p=s.work/'flash-offset.mp4';s.c.run(s.prefix+['-i',s.files['flash-30'],'-map','0','-c','copy','-output_ts_offset','5',p]);s.files['flash-offset']=p
 s.sources.update({str(p):sha256(p) for p in s.files.values()})

def flashes(s,path):
 raw=s.c.run([mv.FFMPEG,'-v','error','-i',path,'-map','0:v:0','-an','-vf','crop=64:40:208:100,scale=1:1:flags=area,format=gray','-fps_mode','passthrough','-f','rawvideo','-'],binary=True)
 return [n for n,v in enumerate(raw) if v>200]

def pulses(s,path):
 data=s.c.run([mv.FFMPEG,'-v','error','-i',path,'-map','0:a:0','-vn','-ac','1','-ar','48000','-f','f32le','-'],binary=True);values=array('f');values.frombytes(data)
 hits=[n for n in range(len(values)//48) if math.sqrt(sum(v*v for v in values[n*48:(n+1)*48])/48)>.25]
 groups=[]
 for n in hits:
  if not groups or n-groups[-1][-1]>100:groups.append([n])
  else:groups[-1].append(n)
 return [Fraction(g[0],1000) for g in groups]

def verify(s,name):
 detail=s.trim_case(name,'accurate_reencode');src=s.files[name];out=s.work/f'{name}-accurate_reencode-1100000.mp4'
 indices=detail['frame_identity']['source_frame_indices'];sf=flashes(s,src);df=flashes(s,out);expected=[n-indices[0] for n in sf if n in indices]
 groups=sum(i==0 or n!=expected[i-1]+1 for i,n in enumerate(expected))
 assert groups==2 and df==expected,(sf,df,expected)
 def first_audio(path):
  stream=next(x for x in mv.metadata(s.c,path)['streams'] if x['codec_type']=='audio');fs=mv.decoded_frames(s.c,path,'a:0');return Fraction(fs[0]['best_effort_timestamp'])*Fraction(stream['time_base'])
 sm=mv.metadata(s.c,src);sv=next(x for x in sm['streams'] if x['codec_type']=='video');fs=mv.decoded_frames(s.c,src);origin=Fraction(fs[indices[0]]['best_effort_timestamp'])*Fraction(sv['time_base'])
 before=pulses(s,src);after=pulses(s,out);assert len(before)==len(after)==2,(before,after)
 mapped=[p+first_audio(src)-origin for p in before];got=[p+first_audio(out) for p in after];tolerance=max(Fraction(40,1000),1/Fraction(sv['avg_frame_rate'])+Fraction(1024,48000))
 def compare(actual):
  errors=[abs(a-b) for a,b in zip(mapped,actual)];assert max(errors)<=tolerance,(errors,tolerance)
  drift=abs((actual[1]-actual[0])-(mapped[1]-mapped[0]));assert drift<=Fraction(2,1000),(drift,tolerance)
  return [str(e) for e in errors],str(drift)
 errors,drift=compare(got)
 try:compare([p+Fraction(1,5) for p in got])
 except AssertionError:pass
 else:raise AssertionError('Pulse checker accepted 200ms wrong origin')
 detail['flash_pulse']={'source_flash_indices':sf,'output_flash_indices':df,'mapped_pulse_seconds':[str(x) for x in mapped],'output_pulse_seconds':[str(x) for x in got],'mapping_errors_seconds':errors,'interval_drift_seconds':drift,'tolerance_seconds':str(tolerance),'wrong_200ms_origin_rejected':True}
 return detail

def run(s):
 for name in ['flash-24','flash-25','flash-30','flash-60','flash-23976','flash-2997','flash-offset']:s.check(name+'-pulse-mapping',lambda n=name:verify(s,n))
