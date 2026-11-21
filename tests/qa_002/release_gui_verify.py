#!/usr/bin/env python3
"""Final GUI output/media and immutable artifact verification, no playback."""
import argparse,array,hashlib,json,math,subprocess
from fractions import Fraction
from pathlib import Path

def sha(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def selected_packets_match(source, output, source_audio):
 # Only this full-span zero-origin fixture is checked here. Do not discard a
 # negative packet unless its own Skip Samples declares the ENTIRE packet
 # non-content. A partially audible preroll must remain and will fail below.
 expected={i:[dict(x) for x in source if x['stream_index']==i] for i in [0,1]}
 actual={i:[dict(x) for x in output if x['stream_index']==i] for i in [0,1]}
 excluded=[]
 first=expected[1][0]
 # packet duration is in time-base ticks; skip_samples is in audio samples.
 duration_samples=Fraction(first['duration'])*Fraction(source_audio['time_base'])*int(source_audio['sample_rate'])
 skip=[s for s in first.get('side_data_list',[]) if s.get('side_data_type')=='Skip Samples']
 if first['pts']<0 and first['pts']+first['duration']<=0 and duration_samples.denominator==1 and duration_samples>0 and len(skip)==1 and skip[0].get('skip_samples',0)>=duration_samples and skip[0].get('discard_padding',0)==0:
  excluded.append(expected[1].pop(0))
 fields=['pts','dts','duration','data_hash']
 for i in [0,1]:
  assert expected[i] and len(expected[i])==len(actual[i]),('Selected packet count',i)
  assert [[x[k] for k in fields] for x in expected[i]]==[[x[k] for k in fields] for x in actual[i]],('Selected packet content/timing',i)
 return {'selected_packet_counts':{i:len(v) for i,v in expected.items()},'excluded_fully_discarded_preroll':excluded,'compared_fields':fields}
def main():
 p=argparse.ArgumentParser();p.add_argument('--evidence',type=Path,required=True);p.add_argument('--app',type=Path,required=True);a=p.parse_args();ev=a.evidence;commands=[]
 def run(argv,timeout=60,binary=False):
  r=subprocess.run(list(map(str,argv)),stdin=subprocess.DEVNULL,capture_output=True,timeout=timeout);commands.append(dict(argv=list(map(str,argv)),exit=r.returncode,stderr=r.stderr.decode(errors='replace')));assert r.returncode==0,commands[-1];return r.stdout if binary else r.stdout.decode()
 original=json.loads((ev/'release-assembly.json').read_text());actual={str(f.relative_to(a.app)):sha(f) for f in a.app.rglob('*') if f.is_file()};assert actual==original['bundle_files'];input_manifest=json.loads((ev/'gui-inputs.json').read_text())['files'];assert all(sha(Path(k))==v for k,v in input_manifest.items())
 root=Path('/private/tmp/mediastudio-qa002-release/GUI outputs');outputs=[];copy_pcm=None
 for path,seconds,frames in [(root/'release-copy.mp4',2,60),(root/'release-precise.mp4',1,30),(root/'release stems/vocals.wav',4,None),(root/'release stems/accompaniment.wav',4,None)]:
  meta=json.loads(run(['/opt/homebrew/bin/ffprobe','-v','error','-show_streams','-show_format','-of','json',path]));assert abs(float(meta['format']['duration'])-seconds)<=.04
  decode=run(['/opt/homebrew/bin/ffmpeg','-nostdin','-v','error','-xerror','-i',path,'-map','0','-progress','pipe:1','-nostats','-f','null','-']);audio=[s for s in meta['streams'] if s['codec_type']=='audio'];assert len(audio)==1
  pcm=run(['/opt/homebrew/bin/ffmpeg','-nostdin','-v','error','-i',path,'-map','0:a:0','-f','f32le','-'],binary=True);samples=array.array('f');samples.frombytes(pcm);assert samples and all(map(math.isfinite,samples));assert max(map(abs,samples))==0
  if path.name=='release-copy.mp4':copy_pcm=pcm
  if frames is not None:
   counts=[int(x.split('=',1)[1]) for x in decode.splitlines() if x.startswith('frame=')];assert counts[-1]==frames
  else:assert len(samples)==44100*2*4 and audio[0]['codec_name']=='pcm_f32le' and audio[0]['sample_rate']=='44100' and audio[0]['channels']==2
  outputs.append(dict(path=str(path),sha256=sha(path),metadata=meta,actual_decoded_video_frames=frames,audio_sample_values=len(samples),all_finite=True,zero_audio=True,full_decode_exit=0,no_audio_device_opened=True))
 def packets(path):
  return json.loads(run(['/opt/homebrew/bin/ffprobe','-v','error','-show_packets','-show_entries','packet=stream_index,pts,dts,duration,side_data_list,data_hash','-show_data_hash','sha256','-of','json',path]))['packets']
 source=Path('/private/tmp/mediastudio-qa002-release/GUI inputs/silent video.mp4');before=packets(source);after=packets(root/'release-copy.mp4')
 source_metadata=json.loads(run(['/opt/homebrew/bin/ffprobe','-v','error','-show_streams','-of','json',source]));source_audio=next(x for x in source_metadata['streams'] if x['codec_type']=='audio');assert source_audio['index']==1
 comparison=selected_packets_match(before,after,source_audio)
 source_pcm=run(['/opt/homebrew/bin/ffmpeg','-nostdin','-v','error','-i',source,'-map','0:a:0','-f','f32le','-'],binary=True);assert source_pcm==copy_pcm;comparison['full_decoded_audio_bytes_identical']=True;comparison['decoded_audio_sample_values']=len(source_pcm)//4;comparison['source_audio_time_base']=source_audio['time_base'];comparison['source_sample_rate']=source_audio['sample_rate']
 heartbeat=json.loads((ev/'gui-heartbeat-summary.json').read_text());rss=json.loads((ev/'gui-resource-summary.json').read_text());assert heartbeat['result']=='PASS' and not rss['sampled_survivors']
 d={'result':'PASS','scope':'Actual signed Release-v1 CUA GUI exports and normal restart; immutable artifact/source files checked separately; no listening/VoiceOver acceptance','all112_bundle_files_unchanged':True,'input_hashes_unchanged':True,'main_sha256':actual['Contents/MacOS/MediaStudio'],'core_sha256':actual['Contents/Frameworks/libmedia_ffi.dylib'],'full_gui_outputs':outputs,'selected_copy_packet_content_and_timing_identical':True,'packet_comparison':comparison,'maximum_post_original_2s_warmup_ms':heartbeat['maximum_post_warmup_ms'],'startup_warmup_max_ms':heartbeat['startup_warmup_max_ms'],'commands':commands}
 (ev/'gui-final-verification.json').write_text(json.dumps(d,indent=2)+'\n');print(json.dumps({'result':d['result'],'gui_outputs':len(outputs),'files_unchanged':len(actual),'post_warmup_max_ms':heartbeat['maximum_post_warmup_ms']}))
if __name__=='__main__':main()
