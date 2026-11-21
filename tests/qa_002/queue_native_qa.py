#!/usr/bin/env python3
"""QA-002 production WorkspaceModel / CoreBridge checks; not GUI acceptance."""
import argparse,hashlib,json,os,shutil,subprocess,sys,threading,time,traceback
from pathlib import Path
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tests/core_004'))
from fault_tools import holding_encoder,event_rows,pid_alive

def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,d):Path(p).write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
def check(ok,data):
 if not ok:raise AssertionError(data)
def step(d,a,i=0):return [s for s in d['steps'] if s['action']==a][i]
def task(s,n):return next(t for t in s['tasks'] if t['destinationName']==n)
def imp(*p):return {'action':'import','paths':list(map(str,p))}
def rng():return {'action':'range','start_us':1100000,'end_us':2600000}
def mode():return {'action':'mode','value':'accurateReencode'}
def obs(e):return {'action':'wait-observation','path':str(e),'contains':'staging-ready'}
class Runner:
 def __init__(self,a):
  self.binary=Path(a.binary).resolve();self.source=Path(a.source).resolve();self.work=Path(a.work).resolve();self.work.mkdir(parents=True,exist_ok=False);self.library_dir=Path(a.library_dir).resolve() if a.library_dir else None
  self.evidence=Path(a.evidence).resolve();self.evidence.mkdir(parents=True,exist_ok=True);self.before={str(p):digest(p) for p in [self.binary,self.source]};self.commands=[];self.checks=[]
 def run(self,name,actions,env=None,watch=None,timeout=180,flag='--queue-self-test'):
  f=self.work/name;f.mkdir(exist_ok=True);scenario=f/'scenario.json';write(scenario,{'schema_version':1,'history_path':str(f/'history.json'),'actions':actions})
  stop=threading.Event();seen=[];errors=[]
  def worker():
   try:watch(stop,seen)
   except Exception:errors.append(traceback.format_exc())
  t=threading.Thread(target=worker) if watch else None
  if t:t.start()
  started=time.monotonic();cmd=[str(self.binary),flag,'--scenario',str(scenario)]
  if self.library_dir:
   env=dict(env or os.environ);env['DYLD_LIBRARY_PATH']=str(self.library_dir);env['DYLD_PRINT_LIBRARIES']='1'
  try:p=subprocess.run(cmd,cwd=ROOT,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=timeout,env=env)
  finally:
   stop.set()
   if t:t.join(timeout=5)
  record={'argv':cmd,'exit':p.returncode,'elapsed_seconds':time.monotonic()-started,'stderr':p.stderr,'observations':seen,'watch_errors':errors};self.commands.append(record)
  (self.evidence/f'native-{name}-stdout.json').write_text(p.stdout);write(self.evidence/f'native-{name}-scenario.json',json.loads(scenario.read_text()))
  check(p.returncode==0,record);check(not errors,errors)
  if self.library_dir:check(str(self.library_dir/'libmedia_ffi.dylib') in p.stderr,'Expected final signed Core not observed by dyld')
  d=json.loads(p.stdout);check(d['passed'],d)
  bd=d['bridgeDiagnostics'];check(bd['allCallsOffMainThread'] and bd['outputCalls']==bd['bufferReleaseCalls'],bd)
  return d,seen
 def wrapper(self,name):
  f=self.work/name;f.mkdir(exist_ok=True);return holding_encoder(f/'gate')
 def general(self):
  result={}
  for action in ['cancel','shutdown']:
   w,e,r=self.wrapper('normal-'+action);dst=self.work/('normal-'+action)/'absent.mp4'
   actions=[{'action':'configure','ffmpeg':str(w),'ffprobe':shutil.which('ffprobe')},imp(self.source),rng(),mode(),{'action':'export','destination':str(dst),'wait':False},obs(e),{'action':action},{'action':'wait'}]
   d,_=self.run('normal-'+action,actions);witness=next(v for v in event_rows(e) if v['kind']=='staging-ready')
   check(not pid_alive(witness['pid']) and not Path(witness['output']).exists() and not dst.exists(),witness);check(task(d['steps'][-1],'absent.mp4')['state']=='cancelled',d['steps'][-1]);check(d['accessDiagnostics']['heldLeases']==0,d)
   result[action]={'real_nonempty_staging':witness,'confirmed_cleaned':True,'closed':d['closed'],'heartbeat_max_interval_ms':d['heartbeatMaxIntervalMs']}
  return result
 def capacity(self):
  w,e,r=self.wrapper('capacity');f=self.work/'capacity';names=[f/f'job-{i:02d}.mp4' for i in range(32)]
  actions=[{'action':'configure','ffmpeg':str(w),'ffprobe':shutil.which('ffprobe')},imp(self.source),rng(),mode(),{'action':'batch','indices':[0]*33,'destinations':[str(f/f'overflow-{i}.mp4') for i in range(33)],'wait':False},{'action':'batch','indices':[0]*32,'destinations':list(map(str,names)),'wait':False},obs(e),{'action':'batch','indices':[0],'destinations':[str(f/'extra.mp4')],'wait':False},{'action':'cancel-task','value':'job-31.mp4'},{'action':'reorder','value':'reverse'},{'action':'cancel'},{'action':'wait'}]
  d,_=self.run('capacity',actions);b=[s for s in d['steps'] if s['action']=='batch'];check(not b[0]['tasks'] and b[0]['error'],b[0]);check(len(b[1]['tasks'])==32 and b[1]['queue']['activeCount']==32,b[1]);check(len(set(t['coreJobId'] for t in b[1]['tasks']))==32,b[1]);check(len(b[2]['tasks'])==32 and b[2]['submissionErrors'],b[2])
  c=step(d,'cancel-task');check(task(c,'job-31.mp4')['state']=='cancelled',c);s=step(d,'reorder');check(s['queue']['queuedJobIds']==list(reversed(c['queue']['queuedJobIds'])),s);check(s['queue']['runningJobId']==b[1]['queue']['runningJobId'],s)
  check(len([v for v in event_rows(e) if v['kind']=='encode-start'])==1,event_rows(e));check(all(t['state']=='cancelled' for t in d['steps'][-1]['tasks']),d['steps'][-1]);check(not any(f.glob('*.mp4')),list(f.iterdir()))
  return {'accepted':32,'overflow_atomic':True,'extra_rejected':True,'pending_reorder_only':True,'queued_cancel_no_media_process':True,'encoder_starts':1,'final_all_cancelled':True}
 def timed_cancel(self):
  w,e,r=self.wrapper('timed-cancel');f=self.work/'timed-cancel';dst=f/'absent.mp4'
  actions=[{'action':'configure','ffmpeg':str(w),'ffprobe':shutil.which('ffprobe')},imp(self.source),rng(),mode(),{'action':'export','destination':str(dst),'wait':False},obs(e),{'action':'cancel-and-wait'}]
  d,_=self.run('timed-cancel',actions);s=step(d,'cancel-and-wait');witness=next(v for v in event_rows(e) if v['kind']=='staging-ready')
  check(s['actionWallTimeMs']<=5000 and task(s,'absent.mp4')['state']=='cancelled',s);check(not pid_alive(witness['pid']) and not Path(witness['output']).exists() and not dst.exists(),witness)
  check(d['heartbeatMaxIntervalMs']<1000,d['heartbeatMaxIntervalMs']);check(any(v['state']=='cancelling' for v in s['trace']),s['trace'])
  return {'cancel_to_confirmed_model_cleanup_ms':s['actionWallTimeMs'],'witness':witness,'cancelling_observed':True,'owned_pid_and_stage_gone':True,'heartbeat_max_interval_ms':d['heartbeatMaxIntervalMs']}
 def timeout(self):
  w,e,r=self.wrapper('timeout');f=self.work/'timeout';dst=f/'absent.mp4';env=dict(os.environ);env['MEDIASTUDIO_ACCURATE_TIMEOUT_MS']='4000'
  actions=[{'action':'configure','ffmpeg':str(w),'ffprobe':shutil.which('ffprobe')},imp(self.source),rng(),mode(),{'action':'export','destination':str(dst),'wait':False},obs(e),{'action':'await-queue'}]
  d,_=self.run('timeout',actions,env=env);s=step(d,'await-queue');t=task(s,'absent.mp4');witness=next(v for v in event_rows(e) if v['kind']=='staging-ready')
  check(t['state']=='failed' and t['error']['code']=='TIMEOUT',t);check(not pid_alive(witness['pid']) and not Path(witness['output']).exists() and not dst.exists(),witness);check(s['accessDiagnostics']['heldLeases']==1,s['accessDiagnostics'])
  return {'configured_processing_timeout_ms':4000,'state':t['state'],'code':t['error']['code'],'real_staging_witness':witness,'owned_pid_and_stage_gone':True}
 def close_watchdog(self):
  w,e,r=self.wrapper('watchdog');f=self.work/'watchdog';framework=self.binary.parents[1]/'Frameworks';shim=f/'close-delay.dylib';marker=f/'cancel-delay.jsonl';dst=f/'absent.mp4'
  command=['xcrun','clang','-std=c11','-Wall','-Wextra','-Werror','-dynamiclib','-I',str(ROOT/'contracts'),str(ROOT/'tests/mac_002/close_delay.c'),'-L',str(framework),'-lmedia_ffi','-Wl,-rpath,'+str(framework),'-o',str(shim)]
  subprocess.run(command,check=True,capture_output=True);env=dict(os.environ);env.update(DYLD_INSERT_LIBRARIES=str(shim),MAC002_CANCEL_DELAY_MARKER=str(marker),MAC002_EXPORT_READY_MARKER=str(e))
  def watch(stop,seen):
   while not stop.wait(.01):
    rows=[v for v in event_rows(e) if v['kind']=='staging-ready']
    try:lines=[json.loads(line) for line in marker.read_text().splitlines()];start=next(v['monotonic_seconds'] for v in lines if v['phase']=='real-cancel-delayed')
    except (OSError,ValueError,StopIteration):continue
    # C interposer records CLOCK_MONOTONIC; Python time.monotonic uses a
    # different Darwin clock origin. Compare values from the same clock.
    elapsed=time.clock_gettime(time.CLOCK_MONOTONIC)-start
    if rows and 15.05<=elapsed<16.9:
     v=rows[0];stage=Path(v['output'])
     if stage.is_file() and stage.stat().st_size>0 and pid_alive(v['pid']):seen.append({'seconds_since_cancel_entered':elapsed,'staging':str(stage),'bytes':stage.stat().st_size,'pid':v['pid'],'pid_alive':True});return
  actions=[{'action':'configure','ffmpeg':str(w),'ffprobe':shutil.which('ffprobe')},imp(self.source),rng(),mode(),{'action':'export','destination':str(dst),'wait':False},obs(e),{'action':'shutdown-expect-timeout'},{'action':'sleep','milliseconds':500},{'action':'await-shutdown'},{'action':'wait'}]
  d,seen=self.run('watchdog',actions,env=env,watch=watch,timeout=100);s=step(d,'shutdown-expect-timeout');done=step(d,'await-shutdown');diag=s['shutdownDiagnostics']
  check(seen,seen);check(14900<=s['actionWallTimeMs']<16500 and diag['watchdogTimeouts']==1 and diag['cleanupPending'] and not diag['cleanupCompleted'],s);check(not s['closed'] and s['accessDiagnostics']['heldLeases']>0 and not s['canImport'] and not s['canExport'],s)
  check(done['closed'] and done['shutdownDiagnostics']['cleanupCompleted'] and done['shutdownDiagnostics']['cleanupAttempts']==1 and done['accessDiagnostics']['heldLeases']==0,done);witness=next(v for v in event_rows(e) if v['kind']=='staging-ready');check(not pid_alive(witness['pid']) and not Path(witness['output']).exists() and not dst.exists(),witness);check(d['heartbeatMaxIntervalMs']<1000,d['heartbeatMaxIntervalMs'])
  return {'watchdog_ms':s['actionWallTimeMs'],'held_leases_at_timeout':s['accessDiagnostics']['heldLeases'],'pending_cleanup_retained':True,'same_cleanup_attempt_completed':True,'after_15s_witness':seen,'heartbeat_max_interval_ms':d['heartbeatMaxIntervalMs'],'interposer_sha256':digest(shim),'compile_argv':command}
 def cleanup_failure(self):
  w,e,r=self.wrapper('cleanup-failure');f=self.work/'cleanup-failure';marker=f/'permissions-removed.json';dst=f/'absent.mp4';stage_parent=[]
  def watch(stop,seen):
   while not stop.wait(.01):
    rows=[v for v in event_rows(e) if v['kind']=='staging-ready']
    if not rows:continue
    v=rows[0];stage=Path(v['output']);parent=stage.parent
    check(stage.is_file() and stage.stat().st_size>0 and pid_alive(v['pid']),v);check(parent.name.startswith('.mediastudio-') and parent.parent==f,(str(parent),str(f)))
    stage_parent.append(parent);parent.chmod(0o500);v={**v,'mode_before':0o700,'mode_after':0o500};seen.append(v);write(marker,{'ready':True});return
  actions=[{'action':'configure','ffmpeg':str(w),'ffprobe':shutil.which('ffprobe')},imp(self.source),rng(),mode(),{'action':'export','destination':str(dst),'wait':False},{'action':'wait-observation','path':str(marker),'contains':'ready'},{'action':'cancel-and-wait'},{'action':'shutdown-expect-cleanup-failure'}]
  try:
   d,seen=self.run('cleanup-failure',actions,watch=watch);s=step(d,'shutdown-expect-cleanup-failure');t=task(s,'absent.mp4');check(t['state']=='failed' and t['error']['code']=='CLEANUP_FAILED',t)
   check(not d['closed'] and d['expectedCleanupFailure'] and d['accessDiagnostics']['heldLeases']>0 and not s['canImport'] and not s['canExport'],d);check(s['shutdownDiagnostics']['cleanupFailed'],s);check(len(seen)==1 and not pid_alive(seen[0]['pid']) and Path(seen[0]['output']).exists() and not dst.exists(),seen)
   return {'actual_permission_denial':seen,'state':t['state'],'code':t['error']['code'],'closed':False,'owner_authorization_retained':True,'new_work_disabled':True,'test_only_retained_output_removed_after_process_exit':True}
  finally:
   for parent in stage_parent:
    if parent.exists():parent.chmod(0o700);shutil.rmtree(parent)
 def snapshot_retry(self):
  w,e,r=self.wrapper('snapshot-retry');f=self.work/'snapshot-retry';b=f/'second-source.mp4'
  subprocess.run([shutil.which('ffmpeg'),'-v','error','-f','lavfi','-i','color=c=blue:s=320x180:r=30:d=4','-c:v','libx264','-pix_fmt','yuv420p','-color_primaries','bt709','-color_trc','bt709','-colorspace','bt709',str(b)],check=True,capture_output=True)
  conflict=f/'existing.mp4';conflict.write_bytes(b'KEEP TARGET EXACTLY');before=digest(conflict)
  def watch(stop,seen):
   first=None
   while not stop.wait(.01):
    rows=[v for v in event_rows(e) if v['kind']=='staging-ready']
    if rows and first is None:first=rows[0]
    if first and not pid_alive(first['pid']):r.touch(exist_ok=False);seen.append({'first_pid_exited':first['pid'],'release_later_encodes':True});return
  actions=[{'action':'configure','ffmpeg':str(w),'ffprobe':shutil.which('ffprobe')},imp(self.source,b),{'action':'select','index':0},rng(),mode(),{'action':'batch','indices':[0],'destinations':[str(f/'first.mp4')],'wait':False},obs(e),{'action':'select','index':1},{'action':'range','start_us':400000,'end_us':1700000},{'action':'mode','value':'streamCopyV1'},{'action':'cancel-task','value':'first.mp4'},{'action':'await-queue'},{'action':'retry','value':'first.mp4','destination':str(conflict)},{'action':'retry','value':'first.mp4','destination':str(f/'retry.mp4')},{'action':'retry','value':'retry.mp4','destination':str(f/'must-not-repeat.mp4')}]
  d,seen=self.run('snapshot-retry',actions,watch=watch);old=task(d['steps'][-1],'first.mp4');new=task(d['steps'][-1],'retry.mp4')
  check(old['state']=='cancelled' and new['state']=='succeeded' and old['coreJobId']!=new['coreJobId'],[old,new]);check(old['sourceAssetId']==new['sourceAssetId']==d['steps'][1]['assets'][0]['id'],[old,new]);check(new['requestedRange']=={'startUs':1100000,'endUs':2600000} and new['mode']=='accurateReencode',new);check(len(d['steps'][-1]['tasks'])==2 and not (f/'must-not-repeat.mp4').exists(),d['steps'][-1]);check(digest(conflict)==before and not (f/'first.mp4').exists(),str(conflict))
  decode=subprocess.run([shutil.which('ffmpeg'),'-v','error','-xerror','-i',str(f/'retry.mp4'),'-map','0','-f','null','-'],capture_output=True);check(decode.returncode==0,decode.stderr.decode());pr=json.loads(subprocess.check_output([shutil.which('ffprobe'),'-v','error','-count_frames','-show_entries','format=duration:stream=codec_type,nb_read_frames','-of','json',str(f/'retry.mp4')],text=True));check(float(pr['format']['duration'])==1.5,pr)
  check(digest(b)!=digest(self.source) and any(s['codec_type']=='audio' for s in pr['streams']),pr)
  history=json.loads((f/'history.json').read_text());allowed={'id','sourceAssetId','sourceName','destinationName','mode','requestedRange','createdAt','coreJobId','state','actualRange','failure','interrupted','destinationBookmark','audioSelection','sessionId'}
  check(len(history)==2 and all(set(v)<=allowed and 'result' not in v for v in history),history)
  return {'frozen_source_range_mode':True,'retry_new_job_id':True,'existing_target_preserved':True,'success_not_retried':True,'distinct_B_source_sha256':digest(b),'probe':pr,'decode_exit':decode.returncode,'output_sha256':digest(f/'retry.mp4'),'bounded_history_summary_only':True,'observations':seen}
 def duplicate_pending(self):
  w,e,r=self.wrapper('duplicate-pending');f=self.work/'duplicate-pending';marker=f/'before-connect.txt'
  actions=[{'action':'configure','ffmpeg':str(w),'ffprobe':shutil.which('ffprobe')},imp(self.source),rng(),mode(),{'action':'submission-boundary-observation','path':str(marker),'value':'before-connect','milliseconds':1800},{'action':'batch','indices':[0],'destinations':[str(f/'once.mp4')],'submission_wait':False},{'action':'wait-observation','path':str(marker),'contains':'before-connect'},{'action':'batch','indices':[0],'destinations':[str(f/'duplicate.mp4')],'wait':False},{'action':'range','start_us':400000,'end_us':1700000},{'action':'await-submission'},obs(e),{'action':'cancel-and-wait'}]
  d,_=self.run('duplicate-pending',actions);b=[s for s in d['steps'] if s['action']=='batch'];check(b[0]['pendingSubmissionToken'] and b[0]['pendingSubmissionToken']==b[1]['pendingSubmissionToken'],b)
  final=d['steps'][-1];check(len(final['tasks'])==1 and not (f/'duplicate.mp4').exists(),final);t=task(final,'once.mp4');check(t['requestedRange']=={'startUs':1100000,'endUs':2600000},t);check(len([v for v in event_rows(e) if v['kind']=='encode-start'])==1,event_rows(e))
  return {'single_submission_identity_held':True,'tasks_created':1,'second_click_rejected':True,'frozen_request_after_edits':True,'no_duplicate_output':True}
 def main(self,names):
  for name in names:
   try:self.checks.append({'name':name,'result':'PASS','details':getattr(self,name)()})
   except Exception:self.checks.append({'name':name,'result':'FAIL','error':traceback.format_exc()});break
  stable={str(p):digest(p) for p in map(Path,self.before)}==self.before;result={'scope':'Production native model/CoreBridge/AVFoundation; no GUI claim','binary':str(self.binary),'before_hashes':self.before,'binary_and_source_stable':stable,'checks':self.checks,'commands':self.commands};result['result']='PASS' if stable and all(v['result']=='PASS' for v in self.checks) else 'FAIL';write(self.evidence/'native-summary.json',result);print(json.dumps({'result':result['result'],'checks':self.checks},ensure_ascii=False));return result['result']=='PASS'
def main():
 p=argparse.ArgumentParser();p.add_argument('--binary',required=True);p.add_argument('--source',required=True);p.add_argument('--work',required=True);p.add_argument('--evidence',required=True);p.add_argument('--library-dir');p.add_argument('--only',nargs='+',default=['general','capacity']);a=p.parse_args();raise SystemExit(0 if Runner(a).main(a.only) else 1)
if __name__=='__main__':main()
