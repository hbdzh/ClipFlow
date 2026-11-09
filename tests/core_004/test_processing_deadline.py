#!/usr/bin/env python3
"""Shared processing budget: preflight cost counts; queue/other-item idle does not."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import time
import traceback
sys.dont_write_bytecode=True
from support import API,HERE,Commands,accepted,captured_at,configuration,control,report,sha256,source_hashes,transcode
from fault_tools import holding_encoder,event_rows,wait_event,pid_alive
from media_verify import verify

def delayed_tool(directory,real,preflight_seconds,encode_seconds):
    directory.mkdir(exist_ok=False);wrapper=directory/'tool-delay';events=directory/'events.jsonl'
    program='''#!PYTHON
import json,os,pathlib,subprocess,sys,time
args=sys.argv[1:]
version=args==['-version']
encode=bool(args and args[-1].endswith('output.mp4') and '-c:v' in args)
def event(kind):
 with open(EVENT_PATH,'a') as f:
  f.write(json.dumps({'kind':kind,'pid':os.getpid(),'monotonic_seconds':time.monotonic(),'output':args[-1] if encode else None})+'\\n');f.flush();os.fsync(f.fileno())
if version:event('preflight-version-entered');time.sleep(PREFLIGHT_DELAY);event('preflight-version-forwarded')
if encode:event('execution-entered');time.sleep(ENCODE_DELAY);event('execution-forwarded')
sys.exit(subprocess.call([REAL_TOOL,*args]))
'''.replace('PYTHON',sys.executable).replace('EVENT_PATH',repr(str(events))).replace('PREFLIGHT_DELAY',repr(preflight_seconds)).replace('ENCODE_DELAY',repr(encode_seconds)).replace('REAL_TOOL',repr(real))
    wrapper.write_text(program);wrapper.chmod(0o700);return wrapper,events

class Suite:
    def __init__(self,path,label):
        self.api=API(path);self.directory=HERE/'build'/label;self.directory.mkdir(exist_ok=False);self.c=Commands();self.checks=[]
        self.before=source_hashes();self.artifact=sha256(path);self.tests={p.name:sha256(p) for p in HERE.glob('*.py')};self.fixture=json.loads((HERE/'build/fixture-manifest.json').read_text())['noaudio'];self.source=Path(self.fixture['path'])
    def check(self,name,fn):
        try:value=fn();self.checks.append({'name':name,'result':'PASS','detail':value})
        except Exception as e:self.checks.append({'name':name,'result':'FAIL','error':str(e),'traceback':traceback.format_exc()})
    def close(self,e):
        for job in e.jobs:
            rc,value=e.snapshot_raw(job)
            if rc==0 and value['state'] not in ['failed','cancelled','succeeded']:e.cancel(job);e.wait(job,timeout=20)
        assert e.destroy()==0
    def total_budget(self):
        wrapper,events=delayed_tool(self.directory/'shared',shutil.which('ffmpeg'),4.5,4.5)
        cfg=configuration(ffmpeg=wrapper);cfg['media_backend']['transcode_timeout_ms']=8000;e=self.api.create(cfg);target=self.directory/'budget-must-not-publish.mp4'
        try:
            start=time.monotonic();accepted_response=accepted(e,control('batch_submit',submission_id='shared_budget',items=[transcode(self.source,target)]));submit_ms=(time.monotonic()-start)*1000
            terminal=e.wait(accepted_response['job_ids'][0],timeout=20);total_ms=(time.monotonic()-start)*1000
            assert terminal['state']=='failed' and terminal['error']['code']=='TIMEOUT',{'preflight_submit_ms':submit_ms,'whole_processing_ms':total_ms,'terminal':terminal,'events':event_rows(events)}
            assert not target.exists() and not list(self.directory.glob('.mediastudio-*'))
            rows=event_rows(events);assert any(x['kind']=='preflight-version-forwarded' for x in rows) and len([x for x in rows if x['kind']=='preflight-version-entered'])>=2,rows
            assert not any(x['kind']=='execution-forwarded' for x in rows),rows
            for child in [x for x in rows if x['kind'] in ['preflight-version-entered','execution-entered']]:assert not pid_alive(child['pid'])
            assert 4500<=submit_ms<8000 and 7400<=total_ms<9500,(submit_ms,total_ms)
            return {'budget_ms':8000,'preflight_submit_ms':submit_ms,'whole_processing_ms':total_ms,'terminal':terminal,'events':rows,'pid_exited':True,'no_staging_or_final_output':True}
        finally:self.close(e)
    def operation_deadlines(self):
        records=[]
        for kind in ['probe','copy','accurate','transcode']:
            real=shutil.which('ffprobe' if kind=='probe' else 'ffmpeg');wrapper,events=delayed_tool(self.directory/kind,real,1.2,0)
            cfg=configuration();cfg['media_backend'].update(timeout_ms=10000,accurate_reencode_timeout_ms=10000,transcode_timeout_ms=10000)
            selected='timeout_ms' if kind in ['probe','copy'] else ('accurate_reencode_timeout_ms' if kind=='accurate' else 'transcode_timeout_ms')
            cfg['media_backend'][selected]=500;cfg['media_backend']['ffprobe' if kind=='probe' else 'ffmpeg']=str(wrapper)
            e=self.api.create(cfg)
            try:
                request=transcode(self.source,self.directory/(kind+'-must-not-publish.mp4'))
                if kind=='probe':request=control('probe',source={'uri':self.source.as_uri()})
                if kind in ['copy','accurate']:
                    request={'schema_version':0,'operation':'trim','source':{'uri':self.source.as_uri()},'destination':{'uri':(self.directory/(kind+'-must-not-publish.mp4')).as_uri()},'range':{'start_us':400000,'end_us':1700000},'strategy':'stream_copy' if kind=='copy' else 'accurate_reencode','profile':'mp4_h264_copy_v1' if kind=='copy' else 'mp4_h264_standard_v1','stream_selection':'all_supported','allow_reencode_fallback':False}
                start=time.monotonic();rc,response=e.submit(request);elapsed=(time.monotonic()-start)*1000
                assert rc==0 and response.get('accepted') is False and any(x['code']=='TIMEOUT' for x in response['errors']),response
                assert elapsed<1100,elapsed
                rows=event_rows(events);assert rows and rows[0]['kind']=='preflight-version-entered' and not any(x['kind']=='preflight-version-forwarded' for x in rows),rows
                assert not pid_alive(rows[0]['pid']);rc,q=e.submit(control('queue_snapshot'));assert rc==0 and q['active_count']==0 and q['history']==[]
                records.append({'operation':kind,'selected_budget_field':selected,'budget_ms':500,'elapsed_ms':elapsed,'response':response,'events':rows,'queue_no_ghost':q})
            finally:self.close(e)
        return records
    def queue_idle(self):
        wrapper,events,release=holding_encoder(self.directory/'idle-head');a=self.api.create(configuration(ffmpeg=wrapper));cfg=configuration();cfg['media_backend']['transcode_timeout_ms']=4000;b=self.api.create(cfg)
        try:
            head=accepted(a,control('batch_submit',submission_id='head',items=[transcode(self.source,self.directory/'idle-head.mp4')]))['job_ids'][0];wait_event(events,'staging-ready')
            start=time.monotonic();job=accepted(b,control('batch_submit',submission_id='queue_idle',items=[transcode(self.source,self.directory/'idle-tail.mp4')]))['job_ids'][0];own_preflight=(time.monotonic()-start)*1000
            until=time.monotonic()+5.0
            while time.monotonic()<until:
                snapshot=b.snapshot(job);assert snapshot['state']=='queued',snapshot;time.sleep(.05)
            idle_ms=5000;release.touch();assert a.wait(head)['state']=='succeeded';terminal=b.wait(job,timeout=15);assert terminal['state']=='succeeded',terminal
            physical=verify(self.c,self.source,self.directory/'idle-tail.mp4',self.fixture,'mp4_h264_standard_v1',[],self.directory)
            return {'budget_ms':4000,'own_preflight_ms':own_preflight,'queued_idle_lower_bound_ms':idle_ms,'terminal':terminal,'physical':physical}
        finally:release.touch();self.close(a);self.close(b)
    def other_preflight_idle(self):
        wrapper,events=delayed_tool(self.directory/'other-item-preflight',shutil.which('ffmpeg'),3.0,0)
        cfg=configuration(ffmpeg=wrapper);cfg['media_backend']['transcode_timeout_ms']=10000;e=self.api.create(cfg)
        try:
            requests=[transcode(self.source,self.directory/('batch-idle-'+str(i)+'.mp4')) for i in range(4)]
            start=time.monotonic();response=accepted(e,control('batch_submit',submission_id='other_preflight_idle',items=requests));preflight_ms=(time.monotonic()-start)*1000
            assert preflight_ms>10000,preflight_ms
            terminals=[e.wait(job,timeout=20) for job in response['job_ids']];assert all(x['state']=='succeeded' for x in terminals),terminals
            physical=verify(self.c,self.source,self.directory/'batch-idle-0.mp4',self.fixture,'mp4_h264_standard_v1',[],self.directory)
            return {'per_item_processing_budget_ms':10000,'whole_batch_preflight_ms':preflight_ms,'terminals':terminals,'events':event_rows(events),'first_item_physical':physical}
        finally:self.close(e)
    def prepared_accurate_vs_copy(self):
        e=self.api.create(configuration());records=[]
        try:
            for n,rng in enumerate([(0,500000),(110000,210000)]):
                target=self.directory/('accurate-short-'+str(n)+'.mp4')
                request={'schema_version':0,'operation':'trim','source':{'uri':self.source.as_uri()},'destination':{'uri':target.as_uri()},'range':{'start_us':rng[0],'end_us':rng[1]},'strategy':'accurate_reencode','profile':'mp4_h264_standard_v1','stream_selection':'all_supported','allow_reencode_fallback':False}
                response=accepted(e,control('batch_submit',submission_id='accurate_short_'+str(n),items=[request]));terminal=e.wait(response['job_ids'][0]);assert terminal['state']=='succeeded',terminal
                assert terminal['result']['strategy']=='accurate_reencode' and terminal['result']['reencoded'] and not terminal['result']['lossless']
                physical=verify(self.c,self.source,target,self.fixture,'mp4_h264_standard_v1',[],self.directory,*rng)
                records.append({'request':request,'terminal':terminal,'physical':physical})
            target=self.directory/'copy-short.mp4';request={'schema_version':0,'operation':'trim','source':{'uri':self.source.as_uri()},'destination':{'uri':target.as_uri()},'range':{'start_us':0,'end_us':500000},'strategy':'stream_copy','profile':'mp4_h264_copy_v1','stream_selection':'all_supported','allow_reencode_fallback':False}
            rc,response=e.submit(control('batch_submit',submission_id='copy_short',items=[request]));assert rc==0
            if not response['accepted']:
                assert any(x['code']=='SEEK_NOT_EXACT' for x in response['errors']),response;copy={'preflight_refused_by_copy_keyframe_rules':response};assert not target.exists()
            else:
                terminal=e.wait(response['job_ids'][0])
                if terminal['state']=='failed':assert terminal['error']['code']=='SEEK_NOT_EXACT',terminal;copy={'copy_keyframe_failure':terminal}
                else:
                    assert terminal['state']=='succeeded' and terminal['result']['lossless'] and not terminal['result']['reencoded'],terminal
                    actual=terminal['result']['actual_range_us'];assert actual['start_us']==0 and actual['end_us']>500000,actual
                    copy={'terminal':terminal,'physical':verify(self.c,self.source,target,self.fixture,'mp4_h264_standard_v1',[],self.directory,actual['start_us'],actual['end_us'])}
            return {'accurate_successes':records,'copy_distinct_semantics':copy}
        finally:self.close(e)
    def finish(self,label):
        stable=self.before==source_hashes() and self.artifact==sha256(self.api.path) and self.tests=={p.name:sha256(p) for p in HERE.glob('*.py')}
        result='PASS' if all(x['result']=='PASS' for x in self.checks) and stable else 'FAIL'
        report(HERE/'evidence'/('processing-deadline-'+label+'.json'),{'captured_at':captured_at(),'result':result,'scope':'Independent real tool delayed phases; no mocked success, queue/other-item idle excluded from own processing budget','library':{'path':str(self.api.path),'sha256':self.artifact},'source_hashes_before':self.before,'source_hashes_after':source_hashes(),'test_source_hashes':self.tests,'stable':stable,'checks':self.checks,'ffi_outputs':self.api.outputs,'ffi_frees':self.api.frees,'commands':self.c.records})
        return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--library',required=True);p.add_argument('--label',required=True);p.add_argument('--only',default='all');opts=p.parse_args();suite=Suite(opts.library,opts.label)
    for name in ['total_budget','operation_deadlines','queue_idle','other_preflight_idle','prepared_accurate_vs_copy']:
        if opts.only=='all' or name in opts.only.split(','):suite.check(name,getattr(suite,name))
    result=suite.finish(opts.label);print(json.dumps({'result':result,'checks':[(x['name'],x['result']) for x in suite.checks]}));raise SystemExit(0 if result=='PASS' else 1)
if __name__=='__main__':main()
