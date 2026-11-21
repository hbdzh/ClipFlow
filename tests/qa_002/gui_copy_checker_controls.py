#!/usr/bin/env python3
"""Real packet metadata with in-memory negative controls; never creates bad media."""
import argparse,copy,hashlib,importlib.util,json,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--evidence',type=Path,required=True);a=p.parse_args();ev=a.evidence;script=Path(__file__).with_name('release_gui_verify.py');spec=importlib.util.spec_from_file_location('release_gui_verify',script);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);commands=[]
def probe(path,*args):
 argv=['/opt/homebrew/bin/ffprobe','-v','error',*args,'-of','json',str(path)];r=subprocess.run(argv,capture_output=True,text=True,check=True);commands.append(dict(argv=argv,exit=r.returncode));return json.loads(r.stdout)
source=Path('/private/tmp/mediastudio-qa002-release/GUI inputs/silent video.mp4');output=Path('/private/tmp/mediastudio-qa002-release/GUI outputs/release-copy.mp4');flags=['-show_packets','-show_entries','packet=stream_index,pts,dts,duration,side_data_list,data_hash','-show_data_hash','sha256'];before=probe(source,*flags)['packets'];after=probe(output,*flags)['packets'];streams=probe(source,'-show_streams')['streams'];audio=next(s for s in streams if s['codec_type']=='audio');first=next(s for s in before if s['stream_index']==1);assert (first['pts'],first['duration'],audio['time_base'],audio['sample_rate'])==(-1024,1024,'1/48000','48000')
(ev/'gui-copy-packet-exact-inspection.json').write_text(json.dumps(dict(source_audio=audio,source_packets=before,output_packets=after,commands=commands),indent=2)+'\n');checks=[]
def test(name,expected,mutate=None):
 s,o,meta=copy.deepcopy(before),copy.deepcopy(after),copy.deepcopy(audio)
 if mutate:mutate(s,o,meta)
 try:m.selected_packets_match(s,o,meta);accepted=True
 except (AssertionError,KeyError,ValueError):accepted=False
 checks.append(dict(name=name,expected_acceptance=expected,actual_acceptance=accepted,result='PASS' if accepted==expected else 'FAIL'))
def priming(s):return next(x for x in s if x['stream_index']==1)
test('real-selected-packets',True)
test('partial-skip-is-not-fully-discarded',False,lambda s,o,a:priming(s)['side_data_list'][0].update(skip_samples=512))
test('missing-skip-cannot-be-inferred-from-negative-pts',False,lambda s,o,a:priming(s).pop('side_data_list'))
test('positive-time-content-cannot-be-excluded',False,lambda s,o,a:priming(s).update(pts=-512))
test('zero-duration-cannot-be-excluded',False,lambda s,o,a:priming(s).update(duration=0))
def wrong_units(s,o,a):
 priming(s).update(pts=-512,duration=512);priming(s)['side_data_list'][0]['skip_samples']=512;a['time_base']='1/24000'
test('timebase-ticks-not-pcm-samples',False,wrong_units)
test('duplicate-skip-side-data-rejected',False,lambda s,o,a:priming(s)['side_data_list'].append(copy.deepcopy(priming(s)['side_data_list'][0])))
test('changed-compressed-payload-rejected',False,lambda s,o,a:o[0].update(data_hash='SHA256:wrong'))
test('changed-video-pts-rejected',False,lambda s,o,a:o[0].update(pts=o[0]['pts']+512))
test('changed-packet-duration-rejected',False,lambda s,o,a:o[0].update(duration=o[0]['duration']+1))
test('dropped-video-packet-rejected',False,lambda s,o,a:o.pop(0))
d=dict(result='PASS' if all(x['result']=='PASS' for x in checks) else 'FAIL',scope='Real source/output metadata; only memory mutations, no playback or corrupt media files',checker_sha256=hashlib.sha256(script.read_bytes()).hexdigest(),checks=checks);(ev/'gui-copy-packet-checker-controls.json').write_text(json.dumps(d,indent=2)+'\n');print(d['result'],len(checks));assert d['result']=='PASS'
