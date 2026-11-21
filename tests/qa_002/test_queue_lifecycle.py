#!/usr/bin/env python3
"""QA-002 AC12–16: bounded actual C ABI queue and lifecycle checks.

Uses only synthetic media, owned directories and independently selected tools.
Core cancellation is tested during an actual FFmpeg encode, not a mock job.
Cleanup denial runs in a separate process because quarantine is intentionally sticky.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

sys.dont_write_bytecode = True
if not __debug__:
    raise SystemExit('Run without -O; assertions are required.')
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tests/core_004'))
from support import API, Commands, TERMINAL, accepted, configuration, control, sha256, transcode
from fault_tools import holding_encoder, event_rows, wait_event, pid_alive
from test_media import Suite as ExistingIndependentQueueSuite


class Suite(ExistingIndependentQueueSuite):
    def __init__(self, library, source, work):
        self.api = API(library)
        self.c = Commands()
        self.directory = work.resolve()
        self.directory.mkdir(parents=True, exist_ok=False)
        self.source = self.directory/'source.mp4'
        shutil.copy2(source, self.source)
        self.source_hash = sha256(self.source)
        self.fixtures = {'small': {'path': str(self.source)}}
        self.checks, self.traces, self.outputs = [], [], {}

    def check(self, name, fn):
        started = time.monotonic()
        super().check(name, fn)
        self.checks[-1]['seconds'] = time.monotonic()-started
        print(name, self.checks[-1]['result'], flush=True)

    def physical(self, path):
        probe = json.loads(self.c.run(['/opt/homebrew/bin/ffprobe', '-v', 'error', '-count_frames',
            '-show_entries', 'stream=codec_type,codec_name,nb_read_frames:format=duration', '-of', 'json', path]).stdout)
        assert [(x['codec_type'], x['codec_name']) for x in probe['streams']] == [('video','h264'), ('audio','aac')]
        self.c.run(['/opt/homebrew/bin/ffmpeg','-nostdin','-v','error','-xerror','-i',path,'-map','0','-f','null','-'])
        return {'path': str(path), 'sha256': sha256(path), 'probe': probe, 'full_decode_exit': 0}

    def atomic_and_33(self):
        e = self.api.create(configuration())
        good = transcode(self.source, self.dst('atomic-good.mp4'))
        existing = self.dst('existing.mp4'); existing.write_bytes(b'KEEP ORIGINAL TARGET')
        unreadable = self.dst('unreadable.mp4'); shutil.copy2(self.source, unreadable)
        bad = self.dst('broken.mp4'); bad.write_bytes(b'not media')
        records = []
        try:
            rc, response = e.submit(control('batch_submit', submission_id='length33', items=[good]*33))
            assert rc == -9 and response is None, (rc,response)
            for name, request, code in [
                ('existing', transcode(self.source,existing), 'OUTPUT_EXISTS'),
                ('invalid-range', transcode(self.source,self.dst('past-end.mp4'), range_us=(0,999000000)), 'INVALID_INPUT'),
                ('bad-source', transcode(bad,self.dst('broken-output.mp4')), 'INVALID_MEDIA'),
                ('missing-source', transcode(self.dst('missing.mp4'),self.dst('missing-output.mp4')), 'INVALID_INPUT'),
                ('permission', transcode(unreadable,self.dst('denied-output.mp4')), 'PERMISSION_DENIED')]:
                if name == 'permission': unreadable.chmod(0)
                try:
                    rc, rejected = e.submit(control('batch_submit', submission_id='atomic_'+name.replace('-','_'),items=[good,request]))
                    assert rc == 0 and rejected['accepted'] is False and not rejected.get('job_ids'),(rc,rejected)
                    assert any(x['code']==code for x in rejected['errors']), rejected
                    q = self.queue(e)
                    assert q['active_count']==0 and q['history']==[] and q['queued_job_ids']==[], q
                    assert q['resources']['global_reserved_storage_bytes']==0 and not self.dst('atomic-good.mp4').exists()
                    records.append({'case':name,'response':rejected,'queue_after':q})
                finally: unreadable.chmod(0o600)
            assert existing.read_bytes()==b'KEEP ORIGINAL TARGET'
            missing = self.api.create(configuration(ffmpeg=self.dst('no-such-ffmpeg')))
            try:
                rc, rejected = missing.submit(control('batch_submit',submission_id='missing_dep',items=[good]))
                assert rc==0 and not rejected['accepted'] and any(x['code']=='DEPENDENCY_MISSING' for x in rejected['errors']), rejected
                assert self.queue(missing)['active_count']==0
                records.append({'case':'dependency','response':rejected})
            finally:self.cleanup(missing)
            return {'thirty_three_status':-9,'atomic_rejections':records,'existing_target_unchanged':True}
        finally:self.cleanup(e)

    def frozen_payload(self):
        wrapper, events, release = holding_encoder(self.dst('freeze-wrapper'))
        e = self.api.create(configuration(ffmpeg=wrapper))
        request = transcode(self.source,self.dst('frozen-output.mp4'),range_us=(1100000,2600000))
        frozen = copy.deepcopy(request)
        try:
            receipt = self.batch(e,'frozen',request)
            wait_event(events,'staging-ready')
            request['source']['uri']=self.dst('different-missing.mp4').as_uri()
            request['range']={'start_us':0,'end_us':500000}
            request['destination']['uri']=self.dst('different-output.mp4').as_uri()
            replay=self.batch(e,'frozen',frozen)
            assert replay['replayed'] and replay['job_ids']==receipt['job_ids']
            value=e.snapshot(receipt['job_ids'][0]);summary=value['request_summary']
            assert summary['source_name']==self.source.name and summary['requested_range_us']==frozen['range'],summary
            release.touch();done=e.wait(receipt['job_ids'][0]);assert done['state']=='succeeded',done
            assert not self.dst('different-output.mp4').exists()
            physical=self.physical(self.dst('frozen-output.mp4'))
            assert physical['probe']['streams'][0]['nb_read_frames']=='45',physical
            return {'receipt':receipt,'replay':replay,'request_after_client_edit':request,'snapshot_before_release':value,'terminal':done,'physical':physical}
        finally:release.touch();self.cleanup(e)

    def realtime_encode_lifecycle(self):
        records=[]
        real_ffmpeg=shutil.which('ffmpeg')
        assert real_ffmpeg, 'Explicit test FFmpeg must be available on PATH'
        for kind in ['cancel','timeout','busy_destroy_cancel']:
            folder=self.dst('real-'+kind);folder.mkdir()
            events=folder/'encode.json'
            wrapper=folder/'ffmpeg-realtime'
            program='''#!PYTHON
import json,os,pathlib,sys,time
args=sys.argv[1:]
if args and args[-1].endswith('output.mp4') and '-c:v' in args:
 pathlib.Path(EVENTS).write_text(json.dumps({'pid':os.getpid(),'output':args[-1],'monotonic_seconds':time.monotonic(),'kind':'real-ffmpeg-exec'}))
 i=args.index('-i');args[i:i]=['-re']
os.execv(REAL,[REAL,*args])
'''.replace('PYTHON',sys.executable).replace('EVENTS',repr(str(events))).replace('REAL',repr(real_ffmpeg))
            wrapper.write_text(program);wrapper.chmod(0o700)
            cfg=configuration(ffmpeg=wrapper)
            if kind=='timeout':cfg['media_backend']['transcode_timeout_ms']=2500
            e=self.api.create(cfg);target=folder/'final.mp4'
            try:
                receipt=self.batch(e,kind,transcode(self.source,target))
                job=receipt['job_ids'][0];deadline=time.monotonic()+10;witness=None
                while time.monotonic()<deadline:
                    if events.exists():
                        try:witness=json.loads(events.read_text())
                        except ValueError:pass
                    if witness and Path(witness['output']).exists() and Path(witness['output']).stat().st_size>0 and pid_alive(witness['pid']):break
                    time.sleep(.005)
                assert witness and Path(witness['output']).stat().st_size>0 and pid_alive(witness['pid']),witness
                witness['bytes_before']=Path(witness['output']).stat().st_size
                witness['command_line']=self.c.run(['/bin/ps','-p',str(witness['pid']),'-o','command=']).stdout.strip()
                assert real_ffmpeg in witness['command_line'] and '-re' in witness['command_line'],witness
                started=time.monotonic();states=[]
                if kind=='busy_destroy_cancel':assert e.destroy()==-7
                if kind!='timeout':
                    assert e.cancel(job)==0;assert e.cancel(job)==0
                    states.append(e.snapshot(job)['state'])
                    assert states[0]=='cancelling',states
                while time.monotonic()-started<10:
                    value=e.snapshot(job);states.append(value['state'])
                    if value['state'] in TERMINAL:break
                    time.sleep(.002)
                seconds=time.monotonic()-started
                assert value['state']==('failed' if kind=='timeout' else 'cancelled'),value
                assert value.get('error',{}).get('code')==('TIMEOUT' if kind=='timeout' else 'CANCELLED'),value
                if kind!='timeout':assert seconds<=5,seconds
                assert not pid_alive(witness['pid']) and not Path(witness['output']).parent.exists() and not target.exists()
                q=self.queue(e);assert q['active_count']==0 and q['resources']['global_reserved_storage_bytes']==0,q
                records.append({'kind':kind,'receipt':receipt,'real_ffmpeg_observation':witness,'states':states,'terminal':value,'request_to_confirm_seconds':seconds,'pid_exited':True,'staging_removed':True,'no_published_output':True,'resources':q['resources']})
            finally:self.cleanup(e)
        return records


def quarantine_child(library, source, work):
    s=Suite(library,source,work)
    wrapper,events,release=holding_encoder(s.dst('cleanup-wrapper'))
    a=s.api.create(configuration(ffmpeg=wrapper));b=s.api.create(configuration(ffmpeg=wrapper))
    stage=None;detail={};result='FAIL';error=None
    try:
        first=s.batch(a,'cleanup_fail',transcode(s.source,s.dst('failed-output.mp4')))['job_ids'][0]
        witness=wait_event(events,'staging-ready')[0];stage=Path(witness['output']);assert stage.stat().st_size>0 and pid_alive(witness['pid'])
        second=s.batch(b,'other_queued',transcode(s.source,s.dst('other-output.mp4')))['job_ids'][0]
        assert b.snapshot(second)['state']=='queued'
        stage.parent.chmod(0)
        try:
            denied=False
            try:list(stage.parent.iterdir())
            except PermissionError:denied=True
            assert denied,'OS did not enforce controlled cleanup denial'
            assert a.cancel(first)==0
            failed=a.wait(first,timeout=10)
            assert failed['state']=='failed' and failed['error']['code']=='CLEANUP_FAILED',failed
            assert not pid_alive(witness['pid'])
            q=s.queue(b);assert q['cleanup_unconfirmed'] and q['resources']['global_quarantined'],q
            assert q['resources']['global_reserved_storage_bytes']>0 and q['resources']['global_reserved_target_count']>0,q
            rc,rejected=b.submit(control('batch_submit',submission_id='unsafe_reuse',items=[transcode(s.source,s.dst('new-output.mp4'))]))
            assert rc==0 and not rejected['accepted'] and any(x['code']=='CLEANUP_FAILED' for x in rejected['errors']),rejected
            assert len([x for x in event_rows(events) if x['kind']=='encode-start'])==1
            detail={'real_permission_denial':True,'witness':witness,'failed':failed,'quarantined_queue':q,'reuse_rejected':rejected,'other_job':b.snapshot(second),'pid_exited':True}
        finally:stage.parent.chmod(0o700)
        assert stage.exists() and stage.stat().st_size>0
        detail['retained_staging_bytes']=stage.stat().st_size
        assert b.cancel(second)==0;b.wait(second,timeout=10)
        assert not s.dst('failed-output.mp4').exists() and not s.dst('other-output.mp4').exists() and not s.dst('new-output.mp4').exists()
        result='PASS'
    except Exception as exc:error={'message':str(exc),'traceback':traceback.format_exc()}
    finally:
        release.touch()
        if stage and stage.parent.exists():stage.parent.chmod(0o700);shutil.rmtree(stage.parent);detail['qa_removed_owned_staging']=True
        for key,e in [('a',a),('b',b)]:
            for job in e.jobs:
                try:e.cancel(job);e.wait(job,timeout=10)
                except Exception:pass
            detail[key+'_destroy_status']=e.destroy()
        detail['ffi_outputs']=s.api.outputs;detail['ffi_frees']=s.api.frees
        assert s.api.outputs==s.api.frees
        if result=='PASS':assert detail['a_destroy_status']==detail['b_destroy_status']==-7
    print(json.dumps({'result':result,'detail':detail,'error':error},ensure_ascii=False))
    return 0 if result=='PASS' else 1


def main():
    p=argparse.ArgumentParser();p.add_argument('--library',required=True,type=Path);p.add_argument('--source',required=True,type=Path);p.add_argument('--work-dir',required=True,type=Path);p.add_argument('--report',type=Path);p.add_argument('--quarantine-child',action='store_true');opts=p.parse_args()
    if opts.quarantine_child:return quarantine_child(opts.library,opts.source,opts.work_dir)
    assert opts.report and not opts.report.exists(),'Refusing to replace previous evidence'
    s=Suite(opts.library,opts.source,opts.work_dir)
    library_hash=sha256(opts.library);test_hash=sha256(__file__)
    for name in ['atomic_and_33','capacity_reorder_global','frozen_payload','races_retry','realtime_encode_lifecycle']:
        s.check(name,getattr(s,name))
    def validate_outputs():
        outputs=[s.physical(path) for path in sorted(s.directory.glob('*.mp4')) if path.name.startswith(('capacity-','race-head','race-tail','retry-new'))]
        assert len(outputs)==5,len(outputs)
        assert sha256(s.source)==s.source_hash and sha256(opts.library)==library_hash and sha256(__file__)==test_hash
        assert s.api.outputs==s.api.frees
        assert not list(s.directory.rglob('.mediastudio-*'))
        return {'outputs':outputs,'source_unchanged':True,'library_unchanged':True,'ffi_outputs':s.api.outputs,'ffi_frees':s.api.frees}
    s.check('physical_outputs_and_safety',validate_outputs)
    def cleanup_denial():
        command=[sys.executable,'-B',str(Path(__file__).resolve()),'--library',str(opts.library.resolve()),'--source',str(opts.source.resolve()),'--work-dir',str(s.directory/'quarantine-child'),'--quarantine-child']
        r=s.c.run(command,expected=None,timeout=90);data=json.loads(r.stdout)
        assert r.returncode==0 and data['result']=='PASS',data
        return data
    s.check('real_cleanup_failure_quarantine',cleanup_denial)
    result='PASS' if all(x['result']=='PASS' for x in s.checks) else 'FAIL'
    data={'result':result,'scope':'Independent actual C ABI queue and lifecycle; real FFmpeg, controlled wrappers and OS cleanup denial; no GUI claim','library':str(opts.library.resolve()),'library_sha256':library_hash,'source_sha256':s.source_hash,'test_sha256':test_hash,'checks':s.checks,'traces':s.traces,'commands':s.c.records,'ffi_outputs':s.api.outputs,'ffi_frees':s.api.frees}
    opts.report.parent.mkdir(parents=True,exist_ok=True)
    with opts.report.open('x') as f:json.dump(data,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps({'result':result,'checks':[(x['name'],x['result']) for x in s.checks]}))
    return 0 if result=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
