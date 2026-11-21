#!/usr/bin/env python3
"""Independent QA-002 media C ABI matrix; generated media only."""
import argparse
from fractions import Fraction
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import time
import traceback
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests/core_004'))
from support import API, Commands, configuration, control, transcode, sha256
import media_verify as mv

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value

v3 = load('matrix_verify3', ROOT / 'tests/core_003/verify.py')
f4 = load('matrix_fixtures4', ROOT / 'tests/core_004/fixtures.py')

def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, allow_nan=False) + '\n')

class Suite:
    def __init__(self, a):
        self.a=a; self.c=Commands(); self.api=API(a.library); self.checks=[]; self.traces=[]
        self.work=a.work.resolve(); self.work.mkdir(parents=True, exist_ok=False)
        self.before=sha256(a.library)
        self.qa_before={p.name:sha256(p) for p in Path(__file__).parent.glob("media_matrix*.py")}
        self.source_before={str(p.relative_to(ROOT)):sha256(p) for p in (ROOT/"core").rglob("*.rs") if "target" not in p.parts}
        self.files={k:Path(v) for k,v in json.loads((a.fixtures/'manifest.json').read_text())['files'].items()}
        self.prefix=[shutil.which('ffmpeg'),'-nostdin','-hide_banner','-v','error','-y']
        self.sources={}

    def check(self, name, fn):
        start=time.monotonic()
        try:
            detail=fn(); row={'name':name,'result':'PASS','detail':detail}
        except Exception as exc:
            row={'name':name,'result':'FAIL','error':repr(exc),'traceback':traceback.format_exc()}
        row['elapsed_seconds']=time.monotonic()-start
        self.checks.append(row); print(name,row['result'],row.get('error',''),flush=True); self.save()

    def engine(self, enabled=True):
        return self.api.create(configuration(ffmpeg=self.a.ffmpeg,ffprobe=self.a.ffprobe,core003_enabled=enabled))

    def close(self,e):
        for job in e.jobs:
            rc, v=e.snapshot_raw(job)
            if rc==0 and v['state'] not in {'succeeded','failed','cancelled'}:
                assert e.cancel(job)==0; e.wait(job)
        assert e.destroy()==0; self.traces.append(e.trace)

    def single(self,e,req):
        rc,receipt=e.submit(req); assert rc==0,(rc,receipt)
        return e.wait(receipt['job_id'])

    def trim(self,src,out,strategy,start=1100000,end=2600000):
        return {'schema_version':0,'operation':'trim','source':{'uri':src.as_uri()},'destination':{'uri':out.as_uri()},
                'range':{'start_us':start,'end_us':end},'strategy':strategy,
                'profile':'mp4_h264_copy_v1' if strategy=='stream_copy' else 'mp4_h264_standard_v1',
                'stream_selection':'all_supported','allow_reencode_fallback':False}

    def generate(self):
        self.fix4=f4.make_fixtures(self.work/'transcode-fixtures',self.c)
        for rate in ['cfr','fps24','fps25','fps60','fps23976','fps2997']:
            p=self.work/(rate+'-offset.mp4')
            self.c.run(self.prefix+['-i',self.files[rate],'-map','0','-c','copy','-output_ts_offset','5',p])
            self.files[rate+'-offset']=p
        for name,ext in [('mov','.mov'),('fake-extension','.unexpected')]:
            p=self.work/(name+ext)
            if name=='mov': self.c.run(self.prefix+['-i',self.files['cfr'],'-map','0','-c','copy',p])
            else: shutil.copy2(self.files['cfr'],p)
            self.files[name]=p
        import media_matrix_extra as extra
        extra.prepare(self)
        import media_matrix_events as events
        events.prepare(self)
        self.sources={str(p):sha256(p) for p in self.files.values()}
        self.sources.update({v['path']:v['sha256'] for v in self.fix4.values()})
        write(self.a.evidence/'core-fixtures.json',{'files':self.sources,'transcode_fixtures':self.fix4,'commands':self.c.records})

    def packets(self,path):
        return json.loads(self.c.run([mv.FFPROBE,'-v','error','-show_packets','-show_data_hash','sha256','-show_entries','packet=stream_index,pts,dts,duration,data_hash','-of','json',path]).stdout)['packets']

    def save(self):
        write(self.a.evidence/'core-results.json',{'result':'PASS' if self.checks and all(r['result']=='PASS' for r in self.checks) else 'FAIL',
             'scope':'Actual C ABI Core with explicitly supplied FFmpeg7 tools; independent host full-decode/frame/packet/audio checks; not GUI/subjective/other platforms',
             'core':str(self.a.library),'core_sha256':self.before,'core_unchanged':self.before==sha256(self.a.library),
             'source_hashes_preserved':all(sha256(Path(p))==h for p,h in self.sources.items()),'checks':self.checks,'commands':self.c.records,'traces':self.traces,
             'ffi_outputs':self.api.outputs,'ffi_frees':self.api.frees,
             'qa_source_hashes':self.qa_before,'qa_sources_unchanged':self.qa_before=={p.name:sha256(p) for p in Path(__file__).parent.glob('media_matrix*.py')},
             'production_source_hashes':self.source_before,'production_sources_unchanged':self.source_before=={str(p.relative_to(ROOT)):sha256(p) for p in (ROOT/'core').rglob('*.rs') if 'target' not in p.parts}})

    def trim_case(self,name,strategy,start=1100000,end=2600000):
        src=self.files[name];out=self.work/f'{name}-{strategy}-{start}.mp4';e=self.engine()
        try:
            req=self.trim(src,out,strategy,start,end);done=self.single(e,req);assert done['state']=='succeeded',done
            result=done['result'];sm=mv.metadata(self.c,src);dm=mv.metadata(self.c,out)
            sv=next(s for s in sm['streams'] if s['codec_type']=='video');dv=next(s for s in dm['streams'] if s['codec_type']=='video')
            assert Fraction(sv['avg_frame_rate'])==Fraction(dv['avg_frame_rate'])
            assert len(sm['streams'])==len(dm['streams'])
            for s,d in zip(sm['streams'],dm['streams']):
                for key in ['codec_type','codec_name','sample_rate','channels','color_range','color_space','color_transfer','color_primaries']:assert s.get(key)==d.get(key),(key,s.get(key),d.get(key))
            self.c.run([mv.FFMPEG,'-nostdin','-v','error','-xerror','-i',out,'-map','0','-f','null','-'])
            if strategy=='accurate_reencode':
                assert result['reencoded'] and not result['lossless'] and result['strategy']==strategy
                indices,_,fs,origin,tb=v3.selected_indices(self.c,src,start,end);first=Fraction(fs[indices[0]]['best_effort_timestamp'])*tb
                detail={'frame_identity':v3.frame_sequence(self.c,src,out,indices),'timing':v3.timing(self.c,out,src,first,origin+Fraction(end,1000000)),
                    'audio':mv.audio(self.c,src,out,[s['index'] for s in sm['streams'] if s['codec_type']=='audio'],first,origin+Fraction(end,1000000))}
                frame_end=Fraction(fs[indices[-1]]['best_effort_timestamp'])*tb+Fraction(fs[indices[-1]].get('duration',fs[indices[-1]].get('pkt_duration')))*tb-origin
                assert 0<=first-origin-Fraction(start,1000000)<=1/Fraction(sv['avg_frame_rate'])
                assert abs(frame_end-Fraction(end,1000000))<=1/Fraction(sv['avg_frame_rate'])
                for key,value in [('start_us',(first-origin)*1000000),('end_us',frame_end*1000000)]:assert abs(Fraction(result['actual_range_us'][key])-value)<=1
            else:detail=self.copy_physical(src,out,sm,dm,result,start,end)
            return {'request':req,'terminal':done,'output_sha256':sha256(out),'source_sha256':sha256(src),'output_probe':dm,'full_decode':True,**detail}
        finally:self.close(e)

    def copy_physical(self,src,out,sm,dm,result,start,end):
        assert result['lossless'] and not result['reencoded'] and result['strategy']=='stream_copy'
        ps,qs=self.packets(src),self.packets(out);shifts=[];records=[]
        for s,d in zip(sm['streams'],dm['streams']):
            before=[p for p in ps if p['stream_index']==s['index']];after=[p for p in qs if p['stream_index']==d['index']]
            candidates=[i for i in range(len(before)-len(after)+1) if [p['data_hash'] for p in before[i:i+len(after)]]==[p['data_hash'] for p in after]]
            assert len(candidates)==1,(s['index'],candidates)
            selected=before[candidates[0]:candidates[0]+len(after)]
            offset=[Fraction(b['pts'])*Fraction(d['time_base'])-Fraction(a['pts'])*Fraction(s['time_base']) for a,b in zip(selected,after)]
            shifts.extend(offset);dts=[int(p['dts']) for p in after];assert all(a<b for a,b in zip(dts,dts[1:]))
            records.append({'source_index':s['index'],'output_index':d['index'],'source_first_packet':candidates[0],'identical_payload_count':len(after),'shift_min':str(min(offset)),'shift_max':str(max(offset))})
        assert max(shifts)-min(shifts)<=Fraction(2,1000)
        sv=next(s for s in sm['streams'] if s['codec_type']=='video');dv=next(s for s in dm['streams'] if s['codec_type']=='video')
        fs=v3.frames(self.c,src);tb=Fraction(sv['time_base']);origin=Fraction(fs[0]['best_effort_timestamp'])*tb
        keys=[Fraction(f['best_effort_timestamp'])*tb-origin for f in fs if f['key_frame']]
        eof=Fraction(fs[-1]['best_effort_timestamp'])*tb+Fraction(fs[-1].get('duration',fs[-1].get('pkt_duration')))*tb-origin
        left=max(k for k in keys if k<=Fraction(start,1000000));right=min([k for k in keys if k>=Fraction(end,1000000)] or [eof])
        for key,value in [('start_us',left*1000000),('end_us',right*1000000)]:assert abs(Fraction(result['actual_range_us'][key])-value)<=1
        pts=[Fraction(f['best_effort_timestamp'])*Fraction(dv['time_base']) for f in v3.frames(self.c,out)];assert all(a<b for a,b in zip(pts,pts[1:]))
        tails=[max((Fraction(p['pts'])+Fraction(p.get('duration',0)))*Fraction(s['time_base']) for p in qs if p['stream_index']==s['index']) for s in dm['streams']]
        limit=max(Fraction(150,1000),1/Fraction(dv['avg_frame_rate'])+Fraction(1024,44100));assert max(tails)-min(tails)<=limit,(tails,limit)
        return {'packet_identity':records,'global_shift_spread':str(max(shifts)-min(shifts)),'tail_spread':str(max(tails)-min(tails)),'tail_limit':str(limit),'independent_actual_range_seconds':[str(left),str(right)]}

    def transcode_case(self,name,preset,audio=None):
        fix=self.fix4[name];src=Path(fix['path']);out=self.work/f'transcode-{name}-{preset}-{len(self.checks)}.mp4';e=self.engine()
        try:
            req=transcode(src,out,preset,audio);rc,receipt=e.submit(control('batch_submit',submission_id='matrix',items=[req]));assert rc==0 and receipt['accepted'],(rc,receipt)
            done=e.wait(receipt['job_ids'][0]);assert done['state']=='succeeded',done
            selected=[s['index'] for s in mv.metadata(self.c,src)['streams'] if s['codec_type']=='audio'] if audio is None else ([] if audio['mode']=='none' else [audio['stream_index']])
            facts=mv.verify(self.c,src,out,fix,preset,selected,self.work)
            for key,value in facts['target'].items():assert done['result']['transcode_plan'][key]==value
            return {'request':req,'terminal':done,'physical':facts}
        finally:self.close(e)

    def rejection(self,name,strategy):
        src=self.files[name];out=self.work/f'reject-{name}-{strategy}.mp4';e=self.engine()
        try:
            req=transcode(src,out,audio={'mode':'none'}) if strategy=='transcode' else self.trim(src,out,strategy)
            rc,receipt=e.submit(control('batch_submit',submission_id='reject',items=[req]));assert rc==0 and not receipt['accepted'],(rc,receipt)
            assert all(x['code'] in {'UNSUPPORTED_FORMAT','INVALID_MEDIA','INVALID_INPUT','RESOURCE_LIMIT'} for x in receipt['errors']),receipt
            rc,q=e.submit(control('queue_snapshot'));assert rc==0 and q['active_count']==0 and q['history']==[]
            assert not out.exists() and not list(self.work.glob('.mediastudio-*'))
            return {'request':req,'receipt':receipt,'no_ghost_or_output':True}
        finally:self.close(e)

    def probe_case(self,name):
        src=self.files[name];e=self.engine()
        try:
            done=self.single(e,control('probe',source={'uri':src.as_uri()}));assert done['state']=='succeeded',done
            info=done['result']['media_info'];raw=mv.metadata(self.c,src)
            assert info['container']==raw['format']['format_name'] and len(info['streams'])==len(raw['streams'])
            for a,b in zip(info['streams'],raw['streams']):
                for k in ['index','codec_name','codec_type','time_base','sample_rate','channels','width','height','avg_frame_rate']:assert a.get(k)==b.get(k),(k,a,b)
            fs=v3.frames(self.c,src);s=next(s for s in raw['streams'] if s['codec_type']=='video');tb=Fraction(s['time_base']);origin=Fraction(fs[0]['best_effort_timestamp'])*tb
            assert abs(Fraction(info['video_origin_us'])-origin*1000000)<=1
            if name=='vfr':assert 'video_cfr_rate' not in info
            else:assert Fraction(info['video_cfr_rate'])==Fraction(s['avg_frame_rate'])
            return {'source_sha256':sha256(src),'terminal':done,'independent_probe':raw}
        finally:self.close(e)

    def checker_controls(self):
        src=self.files['cfr'];rows=[]
        for mode,vf in [('wrong-frame','trim=start_frame=32:end_frame=77,setpts=PTS-STARTPTS'),('wrong-chroma','trim=start_frame=33:end_frame=78,setpts=PTS-STARTPTS,swapuv')]:
            out=self.work/(mode+'.mp4');self.c.run(self.prefix+['-i',src,'-an','-vf',vf,'-c:v','libx264','-threads','1','-crf','18',out])
            try:
                if mode=='wrong-frame':v3.frame_sequence(self.c,src,out,list(range(33,78)))
                else:v3.psnr_and_patches(self.c,src,out,list(range(33,78)),self.work)
            except AssertionError as exc:rows.append({'control':mode,'rejected':True,'reason':str(exc)})
            else:raise AssertionError(('Checker accepted bad output',mode))
        return rows

    def run(self):
        self.generate();self.check('checker-negative-controls',self.checker_controls)
        for name in ['cfr','fps24','fps25','fps60','fps23976','fps2997']:
            for offset in ['', '-offset']:
                n=name+offset;self.check('probe-'+n,lambda n=n:self.probe_case(n))
                for strategy in ['stream_copy','accurate_reencode']:self.check(n+'-'+strategy,lambda n=n,s=strategy:self.trim_case(n,s))
        for name in ['multi','noaudio','unlabelled','audio44100','stereo','audio_first','unicode','mov','fake-extension']:
            self.check('probe-'+name,lambda n=name:self.probe_case(n))
            for strategy in ['stream_copy','accurate_reencode']:self.check(name+'-'+strategy,lambda n=name,s=strategy:self.trim_case(n,s))
        self.check('probe-vfr-no-fake-cfr',lambda:self.probe_case('vfr'))
        for start,end in [(0,500000),(3100000,4000000)]:
            for strategy in ['stream_copy','accurate_reencode']:self.check(f'edge-{start}-{strategy}',lambda s=strategy,a=start,b=end:self.trim_case('cfr',s,a,b))
        for name in ['landscape','portrait','round-landscape','round-portrait','small','small-portrait','noaudio','unlabelled','fraction']:
            for preset in ['mp4_h264_standard_v1','mp4_h264_720p_v1']:self.check(name+'-'+preset,lambda n=name,p=preset:self.transcode_case(n,p))
        for name in ['multi','audio-first']:
            for audio in [None,{'mode':'none'},{'mode':'one','stream_index':0 if name=='audio-first' else 1},{'mode':'one','stream_index':2}]:
                self.check(f'audio-map-{name}-{audio}',lambda n=name,a=audio:self.transcode_case(n,'mp4_h264_standard_v1',a))
        for name in ['vfr','sar','hdr','rotation','subtitle','hevc','alpha','multi_video','surround','bad']:
            for strategy in ['stream_copy','accurate_reencode','transcode']:self.check('reject-'+name+'-'+strategy,lambda n=name,s=strategy:self.rejection(n,s))
        import media_matrix_extra as extra
        extra.run(self)
        import media_matrix_events as events
        events.run(self)
        self.save();assert self.api.outputs==self.api.frees
        assert all(r['result']=='PASS' for r in self.checks),[r['name'] for r in self.checks if r['result']=='FAIL']

if __name__=='__main__':
    p=argparse.ArgumentParser()
    for key in ['library','ffmpeg','ffprobe','work','evidence','fixtures']:p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();a.library=a.library.resolve();a.ffmpeg=a.ffmpeg.resolve();a.ffprobe=a.ffprobe.resolve();Suite(a).run()
