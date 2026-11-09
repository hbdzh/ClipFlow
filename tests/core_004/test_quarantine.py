#!/usr/bin/env python3
"""Isolated process: actual staging directory permission denial during cleanup."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import time
import traceback
sys.dont_write_bytecode=True
from support import API,HERE,Commands,accepted,captured_at,configuration,control,report,sha256,transcode,source_hashes
from fault_tools import holding_encoder,event_rows,wait_event,pid_alive


def child(library,directory):
    directory=Path(directory);api=API(library);wrapper,events,release=holding_encoder(directory/'wrapper')
    source=HERE/'build/fixtures/small.mp4';a=api.create(configuration(ffmpeg=wrapper));b=api.create(configuration(ffmpeg=wrapper));stage_parent=None;value={};result='FAIL';error=None
    try:
        first=accepted(a,control('batch_submit',submission_id='real_cleanup_failure',items=[transcode(source,directory/'first-must-not-publish.mp4')]))['job_ids'][0]
        witness=wait_event(events,'staging-ready')[0];stage=Path(witness['output']);stage_parent=stage.parent
        assert stage.is_file() and stage.stat().st_size>0 and pid_alive(witness['pid'])
        second=accepted(b,control('batch_submit',submission_id='other_engine_queued',items=[transcode(source,directory/'other-must-not-publish.mp4')]))['job_ids'][0]
        assert b.snapshot(second)['state']=='queued'
        original_mode=stage_parent.stat().st_mode & 0o777;stage_parent.chmod(0)
        try:
            denied=False
            try:list(stage_parent.iterdir())
            except PermissionError:denied=True
            assert denied,'Host does not enforce directory permission denial; cannot claim real cleanup fault'
            assert a.cancel(first)==0;failed=a.wait(first,timeout=15)
            assert failed['state']=='failed' and failed['error']['code']=='CLEANUP_FAILED',failed
            assert not pid_alive(witness['pid'])
            # Snapshot/control use the engine's real state; no sleep-based fake terminal.
            other=b.snapshot(second)
            assert other['state']!='succeeded' and not (directory/'other-must-not-publish.mp4').exists(),other
            assert len([row for row in event_rows(events) if row['kind']=='encode-start'])==1
            rc,rejected=b.submit(control('batch_submit',submission_id='after_quarantine',items=[transcode(source,directory/'new-must-not-publish.mp4')]))
            assert (rc==0 and not rejected.get('accepted',True)) or rc!=0,(rc,rejected)
            rc,ledger=b.submit(control('queue_snapshot'));assert rc==0 and ledger['cleanup_unconfirmed'],ledger
            resources=ledger['resources'];assert resources['global_quarantined'] and resources['global_reserved_storage_bytes']>=failed['request_summary']['reserved_storage_bytes'] and resources['global_reserved_target_count']>=1,ledger
            value={'global_ledger_after_cleanup_failure':ledger,'physical_permission_denied':True,'staging_before':witness,'failed_terminal':failed,'other_engine_snapshot':other,'after_quarantine_submit':{'status':rc,'response':rejected},'encoder_events':event_rows(events),'initial_directory_mode':oct(original_mode)}
        finally:stage_parent.chmod(original_mode)
        assert stage.exists() and stage.stat().st_size>0,'Fault did not actually retain failed staging'
        value['retained_staging_after_permissions_restored']={'bytes':stage.stat().st_size,'sha256':sha256(stage)}
        assert b.cancel(second)==0
        other_terminal=b.wait(second,timeout=15);assert other_terminal['state'] in ['failed','cancelled'],other_terminal
        value['other_terminal']=other_terminal
        result='PASS'
    except Exception as e:error={'message':str(e),'traceback':traceback.format_exc()}
    finally:
        release.touch(exist_ok=True)
        if stage_parent is not None and stage_parent.exists():
            stage_parent.chmod(0o700);shutil.rmtree(stage_parent);value['qa_restored_permissions_and_removed_own_staging']=True
        for key,engine in [('a',a),('b',b)]:
            for job in engine.jobs:
                try:engine.cancel(job);engine.wait(job,timeout=10)
                except Exception:pass
            value[key+'_destroy_status']=engine.destroy()
        value['ffi']={'outputs':api.outputs,'frees':api.frees}
        assert api.outputs==api.frees
        if result=='PASS':assert value['a_destroy_status']==value['b_destroy_status']==-7,value
    print(json.dumps({'result':result,'detail':value,'error':error},ensure_ascii=False));raise SystemExit(0 if result=='PASS' else 1)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--library',required=True);parser.add_argument('--label');parser.add_argument('--child-directory');opts=parser.parse_args()
    if opts.child_directory:child(opts.library,opts.child_directory);return
    assert opts.label;directory=HERE/'build'/opts.label;directory.mkdir(exist_ok=False);c=Commands();before=source_hashes();artifact=sha256(opts.library);tests={p.name:sha256(p) for p in HERE.glob('*.py')};result='FAIL';error=None;data={}
    try:
        p=c.run([sys.executable,'-B',Path(__file__),'--library',opts.library,'--child-directory',directory],expected=None,timeout=90)
        data=json.loads(p.stdout);assert p.returncode==0 and data['result']=='PASS',data
        assert before==source_hashes() and artifact==sha256(opts.library) and tests=={p.name:sha256(p) for p in HERE.glob('*.py')}
        result='PASS'
    except Exception as e:error={'message':str(e),'traceback':traceback.format_exc()}
    report(HERE/'evidence'/('quarantine-'+opts.label+'.json'),{'captured_at':captured_at(),'result':result,'scope':'Real OS permission-denied staging cleanup in isolated process; no simulated kernel kill failure','library':{'path':opts.library,'sha256':artifact},'source_hashes':before,'test_source_hashes':tests,'child':data,'error':error,'commands':c.records})
    print(json.dumps({'result':result,'error':error['message'] if error else None}));raise SystemExit(0 if result=='PASS' else 1)
if __name__=='__main__':main()
