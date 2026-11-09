#!/usr/bin/env python3
"""Isolated 256MiB HFS+ test image: real write exhaustion, never host volume."""
import argparse
import errno
import json
import os
from pathlib import Path
import shutil
import sys
import time
import traceback
sys.dont_write_bytecode=True
from support import API,HERE,Commands,accepted,captured_at,configuration,control,report,sha256,transcode,source_hashes
from fault_tools import pause_before_encoder,wait_event,pid_alive
from media_verify import verify


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--library',required=True);parser.add_argument('--label',required=True);parser.add_argument('--prepared-directory');opts=parser.parse_args()
    directory=Path(opts.prepared_directory).resolve(strict=True) if opts.prepared_directory else HERE/'build'/opts.label
    assert (HERE/'build') in directory.parents and not directory.is_symlink()
    if not opts.prepared_directory:directory.mkdir(exist_ok=False)
    mount=directory/'mount';image=directory/'qa-only.dmg'
    if not opts.prepared_directory:mount.mkdir()
    c=Commands();data={};result='BLOCKED';error=None;attached=False;engine=None;api=None;release=None;before=source_hashes();tests={p.name:sha256(p) for p in HERE.glob('*.py')}
    try:
        assert not mount.is_symlink()
        if not opts.prepared_directory:assert not list(mount.iterdir())
        assert shutil.disk_usage(directory).free>=1024**3,'Host has insufficient free space for bounded 256MiB test image'
        if not opts.prepared_directory:
            c.run(['/usr/bin/hdiutil','create','-size','256m','-fs','HFS+','-volname','CORE004_QA_ONLY','-type','UDIF','-nospotlight',image],timeout=120)
            c.run(['/usr/sbin/diskutil','image','attach','--mountPoint',mount,'--nobrowse',image],timeout=120);attached=True
        c.run(['/usr/sbin/diskutil','info','-plist',mount])
        assert os.stat(mount).st_dev!=os.stat(directory).st_dev,'Safety: mount did not change filesystem device'
        usage=shutil.disk_usage(mount);assert 100*1024**2<usage.total<=270*1024**2,usage
        if opts.prepared_directory:attached=True
        data['isolated_volume']={'total_bytes':usage.total,'free_initial_bytes':usage.free,'host_free_before_bytes':shutil.disk_usage(directory).free,'device':os.stat(mount).st_dev,'host_device':os.stat(directory).st_dev}
        result='FAIL';api=API(opts.library);wrapper,events,release=pause_before_encoder(directory/'wrapper');engine=api.create(configuration(ffmpeg=wrapper))
        fixture=json.loads((HERE/'build/fixture-manifest.json').read_text())['small'];source=Path(fixture['path']);target=mount/'must-not-be-published.mp4'
        response=accepted(engine,control('batch_submit',submission_id='real_disk_full',items=[transcode(source,target)]));job=response['job_ids'][0]
        witness=wait_event(events,'before-real-encode')[0];assert pid_alive(witness['pid']);stage=Path(witness['output']);assert stage.parent.is_dir() and os.stat(stage.parent).st_dev==os.stat(mount).st_dev
        fill=mount/'qa-only-fill.bin';written=0;failures=[]
        with fill.open('wb',buffering=0) as stream:
            for block in [1048576,4096,512,1]:
                chunk=b'\xa7'*block
                while written<=300*1024**2:
                    try:written+=stream.write(chunk)
                    except OSError as e:
                        failures.append({'errno':e.errno,'message':str(e),'block_bytes':block,'written_bytes':written})
                        assert e.errno==errno.ENOSPC,('Expected actual ENOSPC filling isolated volume',failures[-1])
                        break
                else:raise AssertionError('Safety: bounded image accepted more than 300MiB fill')
            os.fsync(stream.fileno())
        assert failures and shutil.disk_usage(mount).free<4096,('Image still has allocatable tail space',shutil.disk_usage(mount))
        failure={'errno':errno.ENOSPC,'write_failures':failures,'written_bytes':fill.stat().st_size}
        usage_after=shutil.disk_usage(mount);data['physical_fill']={**failure,'free_bytes_at_exhaustion':usage_after.free,'fill_sha256':sha256(fill)}
        release.touch();terminal=engine.wait(job,timeout=60)
        assert terminal['state']=='failed' and terminal['error']['code']=='OUT_OF_DISK',terminal
        assert not target.exists() and not stage.exists() and not pid_alive(witness['pid'])
        data.update(accepted=response,real_encoder_before_write=witness,failed_terminal=terminal,pid_exited=True,staging_removed=True)
        # Existing low free space must be caught before creating any new job.
        rc,rejected=engine.submit(control('batch_submit',submission_id='known_full_preflight',items=[transcode(source,mount/'known-full.mp4')]));assert rc==0 and not rejected['accepted'];assert any(x['code']=='OUT_OF_DISK' for x in rejected['errors']),rejected
        data['preflight_rejected_while_full']=rejected
        fill.unlink();free_recovered=shutil.disk_usage(mount).free;assert free_recovered>100*1024**2
        recovered=accepted(engine,control('batch_submit',submission_id='after_space_restored',items=[transcode(source,mount/'recovered.mp4')]));recovered_job=engine.wait(recovered['job_ids'][0]);assert recovered_job['state']=='succeeded',recovered_job
        data['recovery']={'free_bytes':free_recovered,'job':recovered_job,'physical':verify(c,source,mount/'recovered.mp4',fixture,'mp4_h264_standard_v1',[1],directory)}
        assert engine.destroy()==0;engine=None
        assert api.outputs==api.frees;data['ffi']={'outputs':api.outputs,'frees':api.frees}
        assert before==source_hashes() and tests=={p.name:sha256(p) for p in HERE.glob('*.py')}
        result='PASS'
    except Exception as e:error={'message':str(e),'traceback':traceback.format_exc()}
    finally:
        if release is not None:release.touch(exist_ok=True)
        if engine is not None and not engine.closed:
            for job in engine.jobs:
                try:engine.cancel(job);engine.wait(job,timeout=20)
                except Exception:pass
            data['final_engine_destroy_status']=engine.destroy()
        if attached:
            try:c.run(['/usr/bin/hdiutil','detach',mount],timeout=60);data['volume_detached']=True
            except Exception as e:data['volume_detached']=False;data['detach_error']=str(e);result='FAIL'
    report(HERE/'evidence'/('disk-'+opts.label+'.json'),{'captured_at':captured_at(),'result':result,'scope':'Isolated bounded image, real filesystem ENOSPC after Core accepted; no host-volume filling','library':{'path':str(opts.library),'sha256':sha256(opts.library)},'source_hashes':before,'test_source_hashes':tests,'detail':data,'error':error,'commands':c.records})
    print(json.dumps({'result':result,'error':error['message'] if error else None,'detached':data.get('volume_detached')}));raise SystemExit(0 if result=='PASS' else 1)
if __name__=='__main__':main()
