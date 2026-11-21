#!/usr/bin/env python3
"""Kill an owned native process after a real Core encode has produced staging.

No GUI/new-user claim. The test records and then manually removes only its own
surviving helper/staging; restart must not claim that crash cleanup was done.
"""
import argparse, hashlib, json, os, shutil, signal, subprocess, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'tests/core_004'))
from fault_tools import holding_encoder, event_rows, pid_alive
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def main():
    p=argparse.ArgumentParser()
    for name in ['binary','work','evidence']: p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args(); a.binary=a.binary.resolve(); a.work.mkdir(exist_ok=False); a.evidence.mkdir(parents=True,exist_ok=True)
    source=a.work/'source.mp4'
    command=[shutil.which('ffmpeg'),'-v','error','-f','lavfi','-i','testsrc2=size=320x180:rate=30:duration=4','-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=4','-c:v','libx264','-pix_fmt','yuv420p','-g','30','-keyint_min','30','-sc_threshold','0','-c:a','aac',str(source)]
    subprocess.run(command,check=True,capture_output=True)
    _,events,_=holding_encoder(a.work/'gate')
    before={'binary':sha(a.binary),'source':sha(source)}
    process=subprocess.Popen([str(a.binary),'real-crash',str(a.work)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    witness=None; restart=None
    try:
        deadline=time.monotonic()+60
        while time.monotonic()<deadline and process.poll() is None:
            if (a.work/'crash-ready').exists(): break
            time.sleep(.02)
        if not (a.work/'crash-ready').is_file():
            if process.poll() is None: process.kill()
            stdout,stderr=process.communicate(timeout=10)
            (a.evidence/'failed-launch-stdout.log').write_bytes(stdout); (a.evidence/'failed-launch-stderr.log').write_bytes(stderr)
            raise AssertionError(('No real Core running/staging witness',process.returncode,stderr.decode()))
        witness=next(row for row in event_rows(events) if row['kind']=='staging-ready')
        stage=Path(witness['output']); assert stage.is_file() and stage.stat().st_size>0 and pid_alive(witness['pid'])
        assert stage.resolve().is_relative_to(a.work.resolve())
        persisted=json.loads((a.work/'real-history.json').read_text())
        assert len(persisted['videos'])==1 and persisted['videos'][0]['state']=='running'
        process.kill(); stdout,stderr=process.communicate(timeout=10)
        (a.evidence/'crashed-stdout.log').write_bytes(stdout); (a.evidence/'crashed-stderr.log').write_bytes(stderr)
        assert process.returncode == -signal.SIGKILL
        leftover={'helper_alive':pid_alive(witness['pid']),'stage_exists':stage.is_file(),'destination_exists':(a.work/'must-not-publish.mp4').exists()}
        restart=subprocess.run([str(a.binary),'real-restart',str(a.work)],capture_output=True,text=True,timeout=30)
        (a.evidence/'restart-stdout.json').write_text(restart.stdout); (a.evidence/'restart-stderr.log').write_text(restart.stderr)
        assert restart.returncode==0, restart.stderr
        report=json.loads(restart.stdout); assert report['result']=='PASS',report
        assert not (a.work/'must-not-publish.mp4').exists()
        unchanged=before=={'binary':sha(a.binary),'source':sha(source)}; assert unchanged
        summary={'result':'PASS','scope':'Actual production native model / signed baseline Core / real FFmpeg; SIGKILL; no GUI claim','source_generation_argv':command,'hashes':before,'unchanged':unchanged,'real_staging_witness':witness,'crash_exit':process.returncode,'leftovers_before_restart':leftover,'restart_checks':report['checks'],'cleanup':'QA observer manually terminates owned helper and removes only own staging after restart; not product crash cleanup'}
        (a.evidence/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n'); print(json.dumps(summary,ensure_ascii=False))
    finally:
        if process.poll() is None: process.kill(); process.communicate(timeout=10)
        if witness:
            if pid_alive(witness['pid']): os.kill(witness['pid'],signal.SIGTERM)
            stage=Path(witness['output'])
            if stage.resolve().is_relative_to(a.work.resolve()) and stage.is_file(): stage.unlink()
if __name__=='__main__':main()
