#!/usr/bin/env python3
"""Independent checks of saved GUI outputs, reusing frozen physical oracles."""
import hashlib,importlib.util,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.dont_write_bytecode=True
def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
mc=load('gui_matrix',ROOT/'tests/qa_002/media_matrix_core.py')
def main():
 ev=ROOT/'docs/qa-002/evidence/media-matrix-20261114';work=Path('/private/tmp/mediastudio-qa002-gui-physical');work.mkdir(exist_ok=False)
 inputs=Path('/private/tmp/mediastudio-qa002-media-interaction/GUI v2');outputs=Path('/private/tmp/mediastudio-qa002-media-gui-export');c=mc.Commands();rows=[]
 fixture={'width':320,'height':180,'counter_width':160,'counter_height':48,'tagged_709':True}
 for src,name,preset,audio,start,end in [('06 29.97fps.mp4','one-frame.mp4','mp4_h264_standard_v1',[1],33366,66733),('08 双音轨.mp4','v2-dual-zho.mp4','mp4_h264_720p_v1',[2],None,None),('09 无音轨.mp4','v2-noaudio-p01.mp4','mp4_h264_standard_v1',[],None,None)]:
  source=inputs/src;output=outputs/name
  detail=mc.mv.verify(c,source,output,fixture,preset,audio,work,start,end)
  expected=mc.v3.selected_indices(c,source,0 if start is None else start,4000000 if end is None else end)[0]
  detail['all_source_counter_identity']=mc.v3.frame_sequence(c,source,output,expected)
  rows.append({'output':name,'result':'PASS','physical':detail})
 source=inputs/'07 起点加5秒.mp4';output=outputs/'v2-offset-copy-recovery.mp4';helper=object.__new__(mc.Suite);helper.c=c
 # These are explicitly observed GUI values, not a fabricated Core JSON receipt.
 observed={'lossless':True,'reencoded':False,'strategy':'stream_copy','actual_range_us':{'start_us':1000000,'end_us':3000000}}
 physical=helper.copy_physical(source,output,mc.mv.metadata(c,source),mc.mv.metadata(c,output),observed,1100000,2600000)
 physical['counter_identity']=mc.v3.frame_sequence(c,source,output,list(range(30,90)))
 hashes=[]
 for path,filters in [(source,['-vf','trim=start_frame=30:end_frame=90,setpts=PTS-STARTPTS']),(output,[])]:
  result=c.run(['ffmpeg','-v','error','-nostdin','-i',path,'-map','0:v:0','-an',*filters,'-f','framemd5','-'])
  hashes.append([x.rsplit(',',1)[-1].strip() for x in result.stdout.splitlines() if x and not x.startswith('#')])
 assert len(hashes[0])==len(hashes[1])==60 and hashes[0]==hashes[1]
 c.run(['ffmpeg','-v','error','-nostdin','-xerror','-i',output,'-map','0','-f','null','-'])
 physical['all_60_decoded_frame_hashes_equal_source']=True
 rows.append({'output':output.name,'result':'PASS','observed_GUI_range_and_mode':observed,'physical':physical,'output_sha256':hashlib.sha256(output.read_bytes()).hexdigest()})
 assert not (outputs/'v2-vfr-must-not-exist.mp4').exists()
 (ev/'gui-physical-validation.json').write_text(json.dumps({'result':'PASS','scope':'Actual GUI outputs, independent whole-frame/packet/audio/colour oracles; no subjective viewing/listening claim','outputs':rows,'VFR_rejected_target_absent':True,'commands':c.records},ensure_ascii=False,indent=2)+'\n')
 print('PASS',len(rows),'GUI outputs')
if __name__=='__main__':main()
