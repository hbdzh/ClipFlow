"""Independent real ABI media/queue acceptance against frozen CORE-004 messages."""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import sys
import time
import traceback
sys.dont_write_bytecode=True
from support import API,HERE,ROOT,TERMINAL,Commands,accepted,captured_at,configuration,control,report,sha256,source_hashes,transcode
from media_verify import metadata,verify
from fault_tools import holding_encoder,event_rows,wait_event,pid_alive

class Suite:
    def __init__(self,library,label):
        self.api=API(library);self.c=Commands();self.directory=HERE/'build'/label;self.directory.mkdir(exist_ok=False)
        self.fixtures=json.loads((HERE/'build/fixture-manifest.json').read_text());self.checks=[];self.outputs={};self.traces=[]
        self.before=source_hashes();self.tests_before={p.name:sha256(p) for p in sorted(HERE.glob('*.py'))};self.library_before=sha256(self.api.path)
    def check(self,name,fn):
        try:detail=fn();self.checks.append({'name':name,'result':'PASS','detail':detail})
        except Exception as e:self.checks.append({'name':name,'result':'FAIL','error':str(e),'traceback':traceback.format_exc()})
    def src(self,name):return Path(self.fixtures[name]['path'])
    def dst(self,name):return self.directory/name
    def queue(self,e):
        rc,value=e.submit(control('queue_snapshot'));assert rc==0,(rc,value);return value
    def batch(self,e,submission,*requests):return accepted(e,control('batch_submit',submission_id=submission,items=list(requests)))
    def cleanup(self,e):
        if e.closed:return
        try:
            for job in list(e.jobs):
                rc,value=e.snapshot_raw(job)
                if rc==0 and value['state'] not in TERMINAL:assert e.cancel(job)==0
            for job in list(e.jobs):
                rc,value=e.snapshot_raw(job)
                if rc==0 and value['state'] not in TERMINAL:e.wait(job,timeout=20)
            assert e.destroy()==0
        finally:self.traces.append(e.trace)
    def success(self,e,request):
        response=self.batch(e,'media_'+str(len(e.jobs)),request);assert len(response['job_ids'])==1
        value=e.wait(response['job_ids'][0]);assert value['state']=='succeeded',value
        result=value['result'];assert result['file_created'] and result['reencoded'] and not result['lossless'] and result['strategy']=='transcode'
        assert result['profile']==request['profile'];return value
    def media_matrix(self):
        e=self.api.create(configuration());records=[]
        cases=[('landscape','mp4_h264_standard_v1',None,None),('landscape','mp4_h264_720p_v1',None,None),
               ('portrait','mp4_h264_720p_v1',None,None),('round-landscape','mp4_h264_720p_v1',None,None),('round-portrait','mp4_h264_720p_v1',None,None),
               ('small','mp4_h264_720p_v1',None,None),('small-portrait','mp4_h264_720p_v1',None,None),
               ('multi','mp4_h264_standard_v1',None,(400000,1700000)),('multi','mp4_h264_720p_v1',{'mode':'one','stream_index':2},(400000,1700000)),
               ('audio-first','mp4_h264_standard_v1',{'mode':'one','stream_index':0},None),('multi','mp4_h264_standard_v1',{'mode':'none'},None),
               ('noaudio','mp4_h264_standard_v1',None,None),('unlabelled','mp4_h264_720p_v1',None,None),
               ('fraction','mp4_h264_standard_v1',None,None),('unicode','mp4_h264_720p_v1',{'mode':'one','stream_index':2},None)]
        try:
            for index,(name,profile,audio,range_us) in enumerate(cases):
                source=self.src(name);destination=self.dst(f'media-{index}.mp4');request=transcode(source,destination,profile,audio,range_us)
                value=self.success(e,request);streams=[s['index'] for s in metadata(self.c,source)['streams'] if s['codec_type']=='audio']
                selected=streams if audio is None or audio['mode']=='all_supported' else ([audio['stream_index']] if audio['mode']=='one' else [])
                physical=verify(self.c,source,destination,self.fixtures[name],profile,selected,self.directory,*(range_us or (None,None)))
                plan=value['result']['transcode_plan'];target=physical['target']
                for key,expected in target.items():assert plan[key]==expected,(key,plan,expected)
                assert plan['selected_source_audio_stream_indices']==selected,plan
                self.outputs[destination.name]=physical;records.append({'source':name,'request':request,'job':value,'physical':physical})
            return records
        finally:self.cleanup(e)
    def atomic_rejection(self):
        e=self.api.create(configuration());records=[]
        try:
            good=transcode(self.src('small'),self.dst('atomic-good.mp4'))
            existing=self.dst('existing.mp4');existing.write_bytes(b'QA existing target unchanged')
            tests=[('broken',transcode(self.src('broken'),self.dst('bad.mp4'))),('rotation',transcode(self.src('rotated'),self.dst('rotated.mp4'),audio={'mode':'none'})),
                   ('exists',transcode(self.src('small'),existing)),('source-target',transcode(self.src('small'),self.src('small'))),
                   ('invalid-audio-index',transcode(self.src('small'),self.dst('bad-index.mp4'),audio={'mode':'one','stream_index':0})),
                   ('missing-parent',transcode(self.src('small'),self.dst('absent-directory/output.mp4'))),
                   ('past-end',transcode(self.src('small'),self.dst('past-end.mp4'),range_us=(0,999000000)))]
            for name,bad in tests:
                before=self.queue(e);rc,response=e.submit(control('batch_submit',submission_id='reject_'+name.replace('-','_'),items=[good,bad]));assert rc==0 and not response['accepted'],(name,rc,response)
                after=self.queue(e);assert before['active_count']==after['active_count']==0;assert before['queued_job_ids']==after['queued_job_ids']==[]
                assert not self.dst('atomic-good.mp4').exists();records.append({'name':name,'response':response,'queue_after':after})
            rc,response=e.submit(control('batch_submit',submission_id='duplicate_destination',items=[good,copy.deepcopy(good)]));assert rc==0 and not response['accepted'],response
            assert existing.read_bytes()==b'QA existing target unchanged'
            recovered=self.success(e,transcode(self.src('small'),self.dst('recovered.mp4')));assert recovered['state']=='succeeded'
            return {'rejections':records,'duplicate_destination':response,'recovery':recovered}
        finally:self.cleanup(e)
    def capacity_reorder_global(self):
        wrapper,events,release=holding_encoder(self.dst('capacity-wrapper'));e=self.api.create(configuration(ffmpeg=wrapper));other=self.api.create(configuration(ffmpeg=wrapper));legacy=self.api.create(configuration(core004=False,ffmpeg=wrapper));record={}
        try:
            requests=[transcode(self.src('small'),self.dst(f'capacity-{i}.mp4')) for i in range(32)]
            response=self.batch(e,'capacity_32',*requests);ids=response['job_ids'];assert len(ids)==32 and len(set(ids))==32
            observed=wait_event(events,'staging-ready')[0];q=self.queue(e);assert q['active_count']==32 and len(q['queued_job_ids'])==31,q
            before=q['queued_job_ids'];rc,rejected=e.submit(control('batch_submit',submission_id='over_remaining',items=[transcode(self.src('small'),self.dst('capacity-over.mp4'))]));assert rc==0 and not rejected['accepted'] and any(x['code']=='RESOURCE_LIMIT' for x in rejected['errors']),rejected
            q2=self.queue(e);assert q2['queued_job_ids']==before
            replay=self.batch(e,'capacity_32',*requests);assert replay['replayed'] and replay['job_ids']==ids
            changed=copy.deepcopy(requests);changed[-1]['profile']='mp4_h264_720p_v1';rc,conflict=e.submit(control('batch_submit',submission_id='capacity_32',items=changed));assert rc==0 and not conflict['accepted'] and any(x['code']=='SUBMISSION_CONFLICT' for x in conflict['errors'])
            desired=list(reversed(before));reordered=accepted(e,control('queue_reorder',job_ids=desired));assert reordered['queued_job_ids']==desired
            rc,bad=e.submit(control('queue_reorder',job_ids=[q['running_job_id'],*desired[:-1]]));assert rc==0 and not bad['accepted'];assert self.queue(e)['queued_job_ids']==desired
            # A second engine accepts work, but global permit must leave it queued until first owner exits.
            second=self.batch(other,'other_engine',transcode(self.src('small'),self.dst('other-engine.mp4')));second_id=second['job_ids'][0]
            oq=self.queue(other);assert oq['running_job_id'] is None and oq['queued_job_ids']==[second_id],oq
            assert len([x for x in event_rows(events) if x['kind']=='encode-start'])==1
            old_request={'schema_version':0,'operation':'trim','source':{'uri':self.src('small').as_uri()},'destination':{'uri':self.dst('legacy-global.mp4').as_uri()},'range':{'start_us':400000,'end_us':1700000},'strategy':'accurate_reencode','profile':'mp4_h264_standard_v1','stream_selection':'all_supported','allow_reencode_fallback':False}
            old_status,old_job=legacy.submit(old_request);assert old_status==0,(old_status,old_job);old_waiting=legacy.snapshot(old_job['job_id']);assert old_waiting['state']=='queued',old_waiting
            assert legacy.cancel(old_job['job_id'])==0;assert legacy.wait(old_job['job_id'])['state']=='cancelled'
            assert other.cancel(second_id)==0;cancelled_other=other.wait(second_id);assert cancelled_other['state']=='cancelled'
            for job in desired[1:]:assert e.cancel(job)==0
            for job in desired[1:]:assert e.wait(job)['state']=='cancelled'
            assert len([x for x in event_rows(events) if x['kind']=='encode-start'])==1
            release.touch();first=e.wait(q['running_job_id']);last=e.wait(desired[0]);assert first['state']==last['state']=='succeeded',(first,last)
            rows=wait_event(events,'encode-start',count=2);assert len(rows)==2
            record={'accepted_ids':ids,'accepted_32':response,'observed_real_staging':observed,'replay':replay,'conflict':conflict,'over_capacity':rejected,'reordered':reordered,'running_reorder_refused':bad,'second_engine_queued':oq,'second_engine_cancelled':cancelled_other,'legacy_engine_waiting_global':old_waiting,'events':event_rows(events),'first_terminal':first,'next_after_reorder':last}
            return record
        finally:release.touch();self.cleanup(e);self.cleanup(other);self.cleanup(legacy)
    def races_retry(self):
        wrapper,events,release=holding_encoder(self.dst('race-wrapper'));e=self.api.create(configuration(ffmpeg=wrapper));changed=self.dst('change-source.mp4');changed.write_bytes(self.src('small').read_bytes());race_target=self.dst('race-target.mp4')
        try:
            parent=self.dst('replace-parent');parent.mkdir();renamed=self.dst('old-parent')
            requests=[transcode(self.src('small'),self.dst('race-head.mp4')),transcode(changed,self.dst('changed-result.mp4')),transcode(self.src('small'),race_target),transcode(self.src('small'),parent/'parent-change.mp4'),transcode(self.src('small'),self.dst('race-tail.mp4'))]
            response=self.batch(e,'race_batch',*requests);ids=response['job_ids'];observed=wait_event(events,'staging-ready')[0]
            changed.write_bytes(b'Changed only after atomic acceptance');race_target.write_bytes(b'Competing target remains')
            parent.rename(renamed);parent.mkdir()
            release.touch();values=[e.wait(job) for job in ids];assert [v['state'] for v in values]==['succeeded','failed','failed','failed','succeeded'],values
            assert race_target.read_bytes()==b'Competing target remains';assert not self.dst('changed-result.mp4').exists()
            assert values[1]['error']['code']=='SOURCE_CHANGED',values[1];assert values[2]['error']['code']=='OUTPUT_EXISTS',values[2]
            assert values[3]['error']['code']=='SOURCE_CHANGED',values[3]
            assert not (parent/'parent-change.mp4').exists() and not (renamed/'parent-change.mp4').exists()
            rc,refused=e.submit(control('job_retry',job_id=ids[0],new_destination={'uri':self.dst('success-retry.mp4').as_uri()},submission_id='forbidden_success_retry'));assert rc==0 and not refused['accepted']
            retry=self.batch(e,'prepare_cancelled',transcode(self.src('small'),self.dst('cancelled-original.mp4')));cancel_id=retry['job_ids'][0]
            assert e.cancel(cancel_id)==0;cancelled=e.wait(cancel_id);assert cancelled['state']=='cancelled'
            sub=self.dst('retry-child');sub.mkdir();link=self.dst('retry-parent-link');link.symlink_to(self.directory,target_is_directory=True)
            retry_alias_refusals=[]
            for n,dest in enumerate([self.dst('cancelled-original.mp4'),sub/'..'/'cancelled-original.mp4',link/'cancelled-original.mp4']):
                rc,bad_retry=e.submit(control('job_retry',job_id=cancel_id,new_destination={'uri':dest.as_uri()},submission_id='same_old_target_'+str(n)))
                assert rc==0 and not bad_retry['accepted'],(rc,bad_retry)
                retry_alias_refusals.append(bad_retry)
            request=control('job_retry',job_id=cancel_id,new_destination={'uri':self.dst('retry-new.mp4').as_uri()},submission_id='retry_new')
            retried=accepted(e,request);new_id=retried['job_ids'][0];assert new_id!=cancel_id;done=e.wait(new_id);assert done['state']=='succeeded',done
            return {'observed':observed,'batch':response,'terminals':values,'success_retry_refused':refused,'cancelled':cancelled,'retry_old_target_alias_refusals':retry_alias_refusals,'retry':retried,'new_terminal':done,'events':event_rows(events)}
        finally:release.touch();self.cleanup(e)
    def cancel_timeout_close(self):
        records=[]
        for kind in ['cancel','timeout','close']:
            wrapper,events,release=holding_encoder(self.dst(kind+'-wrapper'));cfg=configuration(ffmpeg=wrapper)
            if kind=='timeout':cfg['media_backend']['transcode_timeout_ms']=1200
            e=self.api.create(cfg)
            try:
                response=self.batch(e,kind,transcode(self.src('small'),self.dst(kind+'-output.mp4')));job=response['job_ids'][0]
                observed=wait_event(events,'staging-ready');witness=observed[0];stage=Path(witness['output']);assert stage.is_file() and stage.stat().st_size>0 and pid_alive(witness['pid'])
                started=time.monotonic()
                if kind!='timeout':
                    if kind=='close':assert e.destroy()==-7
                    assert e.cancel(job)==0;assert e.cancel(job)==0
                value=e.wait(job,timeout=15);elapsed=time.monotonic()-started
                assert value['state']==('failed' if kind=='timeout' else 'cancelled'),value
                if kind=='timeout':assert value['error']['code']=='TIMEOUT',value
                else:assert elapsed<=5,elapsed
                assert not stage.exists() and not self.dst(kind+'-output.mp4').exists() and not pid_alive(witness['pid'])
                records.append({'kind':kind,'observation':witness,'terminal':value,'cancel_to_confirm_seconds':elapsed,'stage_removed':True,'pid_exited':True})
            finally:release.touch();self.cleanup(e)
        return records
    def filesystem_aliases(self):
        records=[]
        for number,(first_name,second_name) in enumerate([('case.mp4','CASE.mp4'),('é.mp4','e\u0301.mp4')]):
            directory=self.dst('alias-'+str(number));directory.mkdir();probe=directory/first_name;alternate=directory/second_name
            probe.write_bytes(b'QA filesystem alias probe');aliases=alternate.exists() and alternate.stat().st_ino==probe.stat().st_ino
            probe.unlink()
            if not aliases:
                records.append({'names':[first_name,second_name],'filesystem_alias':False,'status':'NOT_APPLICABLE'});continue
            wrapper,events,release=holding_encoder(directory/'wrapper');e=self.api.create(configuration(ffmpeg=wrapper));other=self.api.create(configuration(ffmpeg=wrapper))
            try:
                a=transcode(self.src('small'),directory/first_name);b=transcode(self.src('small'),directory/second_name)
                rc,rejected=e.submit(control('batch_submit',submission_id='alias_batch_'+str(number),items=[a,b]))
                assert rc==0 and not rejected['accepted'] and any(x['code']=='OUTPUT_RESERVED' for x in rejected['errors']),rejected
                q=self.queue(e);assert q['active_count']==0 and q['queued_job_ids']==[] and q['history']==[]
                assert not (directory/first_name).exists() and not (directory/second_name).exists()
                recovered=self.batch(e,'alias_after_rollback_'+str(number),a);first_id=recovered['job_ids'][0]
                witness=wait_event(events,'staging-ready')[0]
                rc,cross=other.submit(control('batch_submit',submission_id='alias_cross_engine_'+str(number),items=[b]))
                assert rc==0 and not cross['accepted'] and any(x['code']=='OUTPUT_RESERVED' for x in cross['errors']),cross
                assert self.queue(other)['active_count']==0
                assert e.cancel(first_id)==0;assert e.wait(first_id)['state']=='cancelled'
                rc,retry_alias=e.submit(control('job_retry',job_id=first_id,new_destination={'uri':(directory/second_name).as_uri()},submission_id='retry_same_alias_'+str(number)))
                assert rc==0 and not retry_alias['accepted'],(rc,retry_alias)
                release.touch();accepted_after_cleanup=self.batch(other,'alias_after_cancel_'+str(number),b)
                completed=other.wait(accepted_after_cleanup['job_ids'][0]);assert completed['state']=='succeeded',completed
                records.append({'names':[first_name,second_name],'filesystem_alias':True,'within_batch_rejection':rejected,'no_ghost_after_rejection':q,'reaccepted_after_rollback':recovered,'real_staging':witness,'cross_engine_rejection':cross,'retry_same_position_alias_refused':retry_alias,'successful_after_cancel_release':completed})
            finally:release.touch();self.cleanup(e);self.cleanup(other)
        return records
    def history(self):
        e=self.api.create(configuration());ids=[]
        try:
            for _ in range(103):
                rc,response=e.submit(control('probe',source={'uri':self.src('small').as_uri()}));assert rc==0,(rc,response)
                ids.append(response['job_id']);assert e.wait(ids[-1])['state']=='succeeded'
            q=self.queue(e);assert len(q['history'])==100,q
            assert e.snapshot_raw(ids[0])[0]==-6
            for row in q['history']:
                assert 'uri' not in json.dumps(row) and str(HERE) not in json.dumps(row) and 'handle' not in json.dumps(row)
            cleared=accepted(e,control('history_clear'));assert cleared['cleared_count']==100,cleared
            assert not self.queue(e)['history'];assert e.snapshot_raw(ids[-1])[0]==-6
            assert e.destroy()==0
            fresh=self.api.create(configuration())
            try:empty=self.queue(fresh);assert empty['active_count']==0 and empty['history']==[]
            finally:self.cleanup(fresh)
            return {'real_probe_count':len(ids),'first_id_recycled_not_found':True,'history_before_clear':q,'clear':cleared,'fresh_engine':empty}
        finally:self.cleanup(e)
    def stability(self):
        assert self.before==source_hashes(),'Production source changed while suite ran'
        assert self.tests_before=={p.name:sha256(p) for p in sorted(HERE.glob('*.py'))},'QA source changed while suite ran'
        assert self.library_before==sha256(self.api.path),'Library changed while suite ran'
        assert all(sha256(v['path'])==v['sha256'] for v in self.fixtures.values()),'Fixture changed during run'
        assert self.api.outputs==self.api.frees
        return {'source_test_library_and_input_hashes_unchanged':True,'outputs':self.api.outputs,'frees':self.api.frees}
    def finish(self,label):
        result='PASS' if all(x['result']=='PASS' for x in self.checks) else 'FAIL'
        report(HERE/'evidence'/('media-'+label+'.json'),{'captured_at':captured_at(),'result':result,'scope':'Independent real C ABI transcode/queue/media; no production validator imports','library':{'path':str(self.api.path),'sha256':self.library_before},'source_hashes_before':self.before,'source_hashes_after':source_hashes(),'test_source_hashes':self.tests_before,'fixture_hashes':{k:v['sha256'] for k,v in self.fixtures.items()},'checks':self.checks,'physical_outputs':self.outputs,'traces':self.traces,'commands':self.c.records,'ffi_outputs':self.api.outputs,'ffi_frees':self.api.frees})
        return result

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--library',required=True);parser.add_argument('--label',required=True);parser.add_argument('--only',default='all');opts=parser.parse_args()
    s=Suite(opts.library,opts.label)
    for name in ['media_matrix','atomic_rejection','capacity_reorder_global','races_retry','cancel_timeout_close','filesystem_aliases','history']:
        if opts.only=='all' or name in opts.only.split(','):s.check(name,getattr(s,name))
    s.check('source-and-artifact-stability',s.stability);result=s.finish(opts.label)
    print(json.dumps({'result':result,'checks':[(x['name'],x['result']) for x in s.checks]}));raise SystemExit(0 if result=='PASS' else 1)
if __name__=='__main__':main()
