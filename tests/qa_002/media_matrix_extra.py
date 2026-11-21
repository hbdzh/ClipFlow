"""Extra unsupported layouts and chromatic odd-content edge oracle."""
from pathlib import Path
from support import control,transcode,sha256
import media_verify as mv

def prepare(s):
 for name,codec,ext in [('vp9-webm','libvpx-vp9','.webm'),('av1-mkv','libaom-av1','.mkv'),('vp9-mp4','libvpx-vp9','.mp4'),('av1-mp4','libaom-av1','.mp4')]:
  p=s.work/(name+ext);s.c.run(s.prefix+['-f','lavfi','-i','testsrc2=size=160x96:rate=24:duration=1','-an','-c:v',codec,'-threads','1','-cpu-used','8',p]);s.files[name]=p
 p=s.work/'attachment.mkv';s.c.run(s.prefix+['-i',s.files['cfr'],'-map','0','-c','copy','-attach',Path(__file__).resolve(),'-metadata:s:t','mimetype=text/plain',p]);s.files['attachment']=p
 p=s.work/'three-audio.mp4';s.c.run(s.prefix+['-i',s.files['multi'],'-map','0:v','-map','0:a:0','-map','0:a:1','-map','0:a:0','-c','copy',p]);s.files['three-audio']=p
 for name,rate in [('audio32000',32000),('audio96000',96000)]:
  p=s.work/(name+'.mp4');s.c.run(s.prefix+['-i',s.files['cfr'],'-map','0','-c:v','copy','-c:a','aac','-ar',str(rate),p]);s.files[name]=p
 for name,fix,box in [('color-right','round-portrait','drawbox=x=iw-24:y=0:w=24:h=ih:color=red:t=fill'),('color-bottom','round-landscape','drawbox=x=0:y=ih-24:w=iw:h=24:color=blue:t=fill')]:
  p=s.work/(name+'.mp4');s.c.run(s.prefix+['-i',s.fix4[fix]['path'],'-map','0','-vf',box,'-c:v','libx264','-qp','0','-threads','1','-c:a','copy',p]);s.files[name]=p
 from interaction_native_edits import replace_edit
 # Generate these negative controls from this run's self-authored input rather
 # than depending on an unrelated, disposable prior /private/tmp session.
 data=s.files['cfr'].read_bytes()
 for n in ['half-speed','repeat']:
  p=s.work/('complex-'+n+'.mp4');p.write_bytes(replace_edit(data,n));s.files['complex-'+n]=p
 s.sources.update({str(p):sha256(p) for p in s.files.values()})

def edge(s,name):
 src=s.files[name];out=s.work/(name+'-edge-output.mp4');e=s.engine();preset='mp4_h264_720p_v1'
 try:
  req=transcode(src,out,preset);rc,r=e.submit(control('batch_submit',submission_id='color_edge',items=[req]));assert rc==0 and r['accepted'],r
  done=e.wait(r['job_ids'][0]);assert done['state']=='succeeded',done
  meta=mv.metadata(s.c,src);v=next(v for v in meta['streams'] if v['codec_type']=='video');cw,ch,ow,oh=mv.dimensions(v['width'],v['height'],preset)
  right=name=='color-right';assert (ow-cw,oh-ch)==((1,0) if right else (0,1))
  crop=f'crop=2:{oh}:{cw-1}:0' if right else f'crop={ow}:2:0:{ch-1}'
  ref=f'scale={cw}:{ch}:flags=bicubic+accurate_rnd,format=yuv444p,pad={ow}:{oh}:0:0:color=black,format=yuv420p,format=yuv444p,{crop}'
  def raw(path,vf):return s.c.run([mv.FFMPEG,'-nostdin','-v','error','-i',path,'-map','0:v:0','-an','-vf',vf,'-fps_mode','passthrough','-pix_fmt','yuv444p','-f','rawvideo','-'],binary=True)
  a,b=raw(src,ref),raw(out,'format=yuv444p,'+crop);plane=2*oh if right else 2*ow;frame=plane*3;assert len(a)==len(b) and len(a)%frame==0
  def evaluate(reference,actual):
   rows=[]
   for n in range(len(a)//frame):
    vals={}
    for index,p in enumerate(['y','u','v']):
     lo=n*frame+index*plane;x=reference[lo:lo+plane];y=actual[lo:lo+plane]
     parts=[(x[::2],y[::2]),(x[1::2],y[1::2])] if right else [(x[:ow],y[:ow]),(x[ow:],y[ow:])]
     maes=[sum(abs(u-v) for u,v in zip(first,second))/len(first) for first,second in parts]
     assert all(mae<=20 for mae in maes),(n,p,maes);vals[p]={'content_mae':maes[0],'padding_mae':maes[1]}
    rows.append(vals)
   return rows
  metrics=evaluate(a,b);negatives=[]
  for kind in ['swap-uv','black-content','white-padding']:
   broken=bytearray(b)
   if kind=='swap-uv':broken[plane:2*plane],broken[2*plane:3*plane]=b[2*plane:3*plane],b[plane:2*plane]
   else:
    indexes=range(0,plane,2) if right and kind=='black-content' else (range(1,plane,2) if right else (range(ow) if kind=='black-content' else range(ow,plane)))
    for i in indexes:broken[i]=16 if kind=='black-content' else 235
   try:evaluate(a,broken)
   except AssertionError:negatives.append({'mutation':kind,'rejected':True})
   else:raise AssertionError(('Color edge checker accepted bad first frame',kind))
  s.c.run([mv.FFMPEG,'-nostdin','-v','error','-xerror','-i',out,'-map','0','-f','null','-'])
  return {'request':req,'terminal':done,'source_sha256':sha256(src),'output_sha256':sha256(out),'reference_filter':ref,'per_frame_yuv_edge_mae':metrics,'negative_controls':negatives,'full_decode':True}
 finally:s.close(e)

def run(s):
 for n in ['vp9-webm','av1-mkv','vp9-mp4','av1-mp4','attachment','three-audio','audio32000','audio96000','complex-half-speed','complex-repeat']:
  for strategy in ['stream_copy','accurate_reencode','transcode']:s.check('reject-'+n+'-'+strategy,lambda n=n,t=strategy:s.rejection(n,t))
 for n in ['color-right','color-bottom']:s.check('p02-'+n,lambda n=n:edge(s,n))
