#!/usr/bin/env python3
"""Independent audio-only probe contract; no dummy video timeline."""
import argparse,json,shutil,sys,traceback
from fractions import Fraction
from pathlib import Path
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tests/core_004'))
from support import API,Commands,configuration,control,sha256

def main():
 p=argparse.ArgumentParser();p.add_argument('--library',type=Path,required=True);p.add_argument('--work',type=Path,required=True);p.add_argument('--evidence',type=Path,required=True);p.add_argument('--tools',type=Path,required=True);a=p.parse_args();a.tools=a.tools.resolve()
 a.work.mkdir(parents=True,exist_ok=False);a.evidence.parent.mkdir(parents=True,exist_ok=True)
 c=Commands();api=API(a.library);rows=[];paths=[];f=shutil.which('ffmpeg');probe=shutil.which('ffprobe')
 def run(name,path,expected=True,legacy=False):
  e=api.create(configuration(core004=not legacy,ffmpeg=a.tools/'ffmpeg',ffprobe=a.tools/'ffprobe',core003_enabled=not legacy))
  try:
   rc,receipt=e.submit(control('probe',source={'uri':path.as_uri()}));assert rc==0,(rc,receipt)
   done=e.wait(receipt['job_id']) if 'job_id' in receipt else {'state':'failed','rejected':receipt};row={'case':name,'terminal':done,'source_sha256':sha256(path)}
   if expected:
    assert done['state']=='succeeded',done
    info=done['result']['media_info'];assert info['media_kind']=='audio' and 'video_origin_us' not in info and 'video_frame_count' not in info and 'video_cfr_rate' not in info
    raw=json.loads(c.run([probe,'-v','error','-show_streams','-show_format','-show_frames','-of','json',path]).stdout)
    stream=raw['streams'][0];frames=raw['frames'];tb=Fraction(stream['time_base']);origin=Fraction(frames[0]['best_effort_timestamp'])*tb
    end=Fraction(frames[-1]['best_effort_timestamp'])*tb+Fraction(frames[-1]['nb_samples'],int(stream['sample_rate']))
    assert abs(Fraction(info['audio_origin_us'])-origin*1000000)<=1
    content_end=min(end,Fraction(int(stream['start_pts'])+int(stream['duration_ts']))*tb) if stream['codec_name']=='aac' else end
    assert abs(Fraction(info['duration_us'])-(content_end-origin)*1000000)<=1
    for k in ['codec_name','time_base','sample_rate','channels']:assert info['streams'][0][k]==stream[k]
    row['independent_origin']=str(origin);row['independent_duration']=str(end-origin)
    out=a.work/(name+'-trim.mp4');req={'schema_version':0,'operation':'trim','source':{'uri':path.as_uri()},'destination':{'uri':out.as_uri()},'range':{'start_us':0,'end_us':1000000},'strategy':'accurate_reencode','profile':'mp4_h264_standard_v1','stream_selection':'all_supported','allow_reencode_fallback':False}
    rc,denied=e.submit(control('batch_submit',submission_id='audio_trim',items=[req]));assert rc==0 and not denied['accepted'] and not out.exists()
    row['audio_trim_refused']=denied
   else: assert done['state']=='failed',done
   row['result']='PASS';rows.append(row)
  except Exception as exc:rows.append({'case':name,'result':'FAIL','error':repr(exc),'traceback':traceback.format_exc()})
  finally:assert e.destroy()==0
  print(name,rows[-1]['result'],rows[-1].get('error',''),flush=True)
  a.evidence.write_text(json.dumps({'result':'PASS' if all(r['result']=='PASS' for r in rows) else 'FAIL','checks':rows,'ffi_outputs':api.outputs,'ffi_frees':api.frees,'library_sha256':sha256(a.library),'commands':c.records,'fixtures':[{'path':str(x),'sha256':sha256(x)} for x in paths]},indent=2))
 for codec,ext in [('pcm_s16le','wav'),('pcm_s24le','wav'),('pcm_f32le','wav'),('flac','flac'),('libmp3lame','mp3'),('aac','m4a')]:
  for rate in [44100,48000]:
   for channels in [1,2]:
    name=f'{codec}-{rate}-{channels}';path=a.work/(name+'.'+ext)
    c.run([f,'-nostdin','-v','error','-y','-f','lavfi','-i',f'sine=frequency=431:sample_rate={rate}:duration=3','-c:a',codec,'-ac',str(channels),path]);paths.append(path);run(name,path)
 run('legacy-audio-rejected',paths[0],False,True)
 for name,rate,ch,duration in [('below-minimum',44100,1,0.5),('unsupported-rate',32000,1,2),('surround',48000,6,2)]:
  path=a.work/(name+'.wav');c.run([f,'-nostdin','-v','error','-y','-f','lavfi','-i',f'anullsrc=r={rate}:cl={"5.1" if ch==6 else "mono"}', '-t',str(duration),'-c:a','pcm_s16le',path]);paths.append(path);run(name,path,False)
 source=a.work/'pcm_f32le-48000-2.wav';data=bytearray(source.read_bytes());fmt=data.index(b'fmt ')+8
 old_size=int.from_bytes(data[fmt-4:fmt],'little');new_fmt=bytes.fromhex('feff')+bytes(data[fmt+2:fmt+16])+bytes.fromhex('16002000')+(0x30).to_bytes(4,'little')+bytes.fromhex('0300000000001000800000aa00389b71')
 data[fmt-4:fmt+old_size]=(40).to_bytes(4,'little')+new_fmt;data[4:8]=(len(data)-8).to_bytes(4,'little');path=a.work/'unsupported-wavex-mask.wav';path.write_bytes(data);paths.append(path);run('wavex-mask',path,False)
 for codec,ext in [('pcm_s16le','wav'),('flac','flac'),('libmp3lame','mp3'),('aac','m4a')]:
  for duration in ([1,600] if codec in ['pcm_s16le','aac'] else [1]):
   name=f'boundary-{codec}-{duration}';path=a.work/(name+'.'+ext)
   c.run([f,'-nostdin','-v','error','-y','-f','lavfi','-i','anullsrc=r=44100:cl=mono','-t',str(duration),'-c:a',codec,path]);paths.append(path);run(name,path)
 assert all(r['result']=='PASS' for r in rows)
if __name__=='__main__':main()
