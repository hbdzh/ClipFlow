#!/usr/bin/env python3
"""AC16 negative: delay one real C cancellation past the global 15s UI deadline."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time
import traceback
sys.dont_write_bytecode=True
from support import Commands,HERE,ROOT,captured_at,sha256,report,source_hashes
from fault_tools import observed_export_hang


def main():
    args=argparse.ArgumentParser();args.add_argument('--label',required=True);options=args.parse_args()
    directory=HERE/'build'/options.label;directory.mkdir(exist_ok=False)
    binary=ROOT/'apps/macos/build/MediaStudioSpike.app/Contents/MacOS/MediaStudioSpike'
    framework=binary.parents[1]/'Frameworks';library=framework/'libmedia_ffi.dylib'
    before=source_hashes();artifact_before={str(p):sha256(p) for p in [binary,library]};c=Commands();observed=[];stop=threading.Event();data={};result='FAIL';error=None
    shim,export_marker=observed_export_hang(directory/'hang');cancel_marker=directory/'delayed-cancel.jsonl';interposer=directory/'close-delay.dylib'
    scenario=directory/'scenario.json';output=directory/'must-not-exist.mp4'
    report(scenario,{'schema_version':1,'history_path':str(directory/'history.json'),'actions':[
        {'action':'configure','ffmpeg':str(shim),'ffprobe':shutil.which('ffprobe')},
        {'action':'import','paths':[str(HERE/'build/fixtures/a.mp4')]},
        {'action':'range','start_us':1100000,'end_us':2600000},
        {'action':'export','destination':str(output),'wait':False},
        {'action':'wait-observation','path':str(export_marker),'contains':'staging'},
        {'action':'shutdown-expect-timeout'}, {'action':'sleep','milliseconds':500}, {'action':'await-shutdown'},{'action':'wait'}]})
    def witness():
        while not stop.is_set():
            try:
                export=json.loads(export_marker.read_text());stage=Path(export['staging'])
                lines=[json.loads(line) for line in cancel_marker.read_text().splitlines()]
                started=next(v['monotonic_seconds'] for v in lines if v['phase']=='real-cancel-delayed')
                elapsed=time.monotonic()-started
                if 15.05<=elapsed<16.9 and stage.is_file() and stage.stat().st_size>0:
                    os.kill(export['pid'],0)
                    if not observed:observed.append({'seconds_since_real_cancel_entered':elapsed,'wrapper_pid':export['pid'],'pid_alive':True,'staging':str(stage),'bytes':stage.stat().st_size,'sha256':sha256(stage),'phase':'after UI15s deadline before forwarded real cancel'})
            except (OSError,ValueError,StopIteration):pass
            stop.wait(.01)
    try:
        c.run(['xcrun','clang','-std=c11','-Wall','-Wextra','-Werror','-dynamiclib','-I',ROOT/'contracts',HERE/'close_delay.c','-L',framework,'-lmedia_ffi','-Wl,-rpath,'+str(framework),'-o',interposer])
        env=dict(os.environ);env['DYLD_INSERT_LIBRARIES']=str(interposer);env['MAC002_CANCEL_DELAY_MARKER']=str(cancel_marker);env['MAC002_EXPORT_READY_MARKER']=str(export_marker)
        watcher=threading.Thread(target=witness);watcher.start()
        try:r=c.run([binary,'--workspace-self-test','--scenario',scenario],timeout=90,env=env)
        finally:stop.set();watcher.join()
        (directory/'stdout.json').write_text(r.stdout);data=json.loads(r.stdout)
        c.records[-1]['stdout_sha256']=hashlib.sha256(r.stdout.encode()).hexdigest();c.records[-1].pop('stdout')
        steps=data['steps'];deadline=next(s for s in steps if s['action']=='shutdown-expect-timeout');done=next(s for s in steps if s['action']=='await-shutdown')
        d=deadline['shutdownDiagnostics'];a=deadline['accessDiagnostics']
        assert not deadline['closed'] and d['cleanupPending'] and not d['cleanupCompleted'] and d['watchdogTimeouts']==1,deadline
        assert 14900<=d['watchdogElapsedMs']<16000,d
        assert a['heldLeases']>0 and deadline.get('activeExport'),deadline
        assert not deadline['canImport'] and not deadline['canExport'] and 14900<=deadline['actionWallTimeMs']<16000
        assert done['shutdownDiagnostics']['cleanupAttempts']==1
        assert any(t['state'] not in ['cancelled','succeeded','failed'] for t in deadline['tasks']),deadline['tasks']
        assert observed,'Independent witness missed alive process/staging after 15s deadline'
        assert done['closed'] and done['shutdownDiagnostics']['cleanupCompleted'] and not done['shutdownDiagnostics']['cleanupPending']
        assert done['accessDiagnostics']['heldLeases']==0
        assert data['passed'] and data['closed'];bd=data['bridgeDiagnostics'];ad=data['accessDiagnostics']
        assert bd['outputCalls']==bd['bufferReleaseCalls'] and bd['allCallsOffMainThread'];assert ad['granted']==ad['stops'] and ad['activeGranted']==0
        export=json.loads(export_marker.read_text())
        try:os.kill(export['pid'],0)
        except ProcessLookupError:pass
        else:raise AssertionError('Owned export wrapper survived actual shutdown')
        assert not Path(export['staging']).exists() and not output.exists()
        assert all(t['state']=='cancelled' for t in done['tasks'])
        assert data['heartbeatMaxIntervalMs']<1000
        assert before==source_hashes() and artifact_before=={str(p):sha256(p) for p in [binary,library]}
        result='PASS'
    except Exception as e:error={'message':str(e),'traceback':traceback.format_exc()}
    report(HERE/'evidence'/('close-deadline-'+options.label+'.json'),{'captured_at':captured_at(),'result':result,'scope':'Independent real Native AC16 delay interposition; 17s forwarding real ms_cancel_job, not simulated Core success','artifacts':artifact_before,'source_hashes':before,'test_sources':{p.name:sha256(p) for p in [Path(__file__),HERE/'close_delay.c',HERE/'fault_tools.py']},'observed_after_15s':observed,'report':data,'error':error,'commands':c.records})
    print(json.dumps({'result':result,'error':error['message'] if error else None,'evidence':str(HERE/'evidence'/('close-deadline-'+options.label+'.json'))},ensure_ascii=False))
    raise SystemExit(0 if result=='PASS' else 1)

if __name__=='__main__':main()
