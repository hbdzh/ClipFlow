#!/usr/bin/env python3
"""Final sandbox candidate's own AI self-test with real-worker cancellation witness."""
import argparse,hashlib,json,os,plistlib,shutil,subprocess,sys,time,traceback,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,d):Path(p).write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
def check(ok,d):
 if not ok:raise AssertionError(d)
C_SOURCE=r'''/* Test-only: hold a real running-job cancellation until external worker witness,
 * then call the unmodified Core. No fake job state, result, or buffer. */
#include <errno.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include "media_core_v0.h"
static atomic_bool held = 0;
static double now(void) { struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return (double)t.tv_sec+(double)t.tv_nsec/1e9; }
static void mark(const char *phase) {
 const char *p=getenv("QA002_AI_CANCEL_MARKER");if(!p)return;FILE *f=fopen(p,"a");if(!f)return;
 fprintf(f,"{\"phase\":\"%s\",\"pid\":%d,\"monotonic_seconds\":%.9f}\n",phase,getpid(),now());fflush(f);fsync(fileno(f));fclose(f);
}
static int32_t observed_cancel(ms_engine *engine,const uint8_t *job,size_t length) {
 const char *ready=getenv("QA002_AI_CANCEL_READY");ms_buffer out={NULL,0};int running=0;
 if(ready && !atomic_load(&held) && ms_get_job_snapshot(engine,job,length,&out)==MS_OK) {
  char *text=malloc(out.len+1);if(text){memcpy(text,out.ptr,out.len);text[out.len]=0;running=strstr(text,"\"state\":\"running\"")!=NULL;free(text);}ms_buffer_free(out);
 }
 if(running && !atomic_exchange(&held,1)) {
  mark("real-cancel-waiting-for-worker-witness");double end=now()+120;
  while(access(ready,F_OK)!=0 && now()<end){struct timespec t={0,10000000};while(nanosleep(&t,&t)!=0 && errno==EINTR){}}
  mark("forwarding-real-cancel");
 }
 int32_t rc=ms_cancel_job(engine,job,length);if(running)mark("real-cancel-returned");return rc;
}
__attribute__((used)) static struct {const void *replacement;const void *replacee;} interpose[] __attribute__((section("__DATA,__interpose")))={{(const void*)observed_cancel,(const void*)ms_cancel_job}};
'''
def processes():
 p=subprocess.run(['/bin/ps','-ww','-axo','pid=,ppid=,pgid=,command='],check=True,capture_output=True,text=True);rows=[]
 for line in p.stdout.splitlines():
  v=line.strip().split(None,3)
  if len(v)==4 and all(n.isdigit() for n in v[:3]):rows.append((int(v[0]),int(v[1]),int(v[2]),v[3]))
 return rows

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--app',required=True);parser.add_argument('--evidence',required=True);a=parser.parse_args();app=Path(a.app).resolve();evidence=Path(a.evidence).resolve();evidence.mkdir(parents=True,exist_ok=True)
 bundle_id=plistlib.loads((app/'Contents/Info.plist').read_bytes())['CFBundleIdentifier'];folder=Path.home()/f'Library/Containers/{bundle_id}/Data/tmp'/('qa002-ai-'+uuid.uuid4().hex);folder.mkdir(mode=0o700,parents=True,exist_ok=False);commands=[];checks=[];witness=None;process=None
 before={str(p.relative_to(app)):sha(p) for p in app.rglob('*') if p.is_file()}
 def command(argv,**kw):
  p=subprocess.run(list(map(str,argv)),capture_output=True,text=True,**kw);commands.append({'argv':list(map(str,argv)),'exit':p.returncode,'stderr':p.stderr});check(p.returncode==0,commands[-1]);return p
 try:
  short=folder/'short.wav';long=folder/'long.wav'
  for source,duration in [(short,3),(long,60)]:command([shutil.which('ffmpeg'),'-v','error','-f','lavfi','-i',f'sine=frequency=440:sample_rate=44100:duration={duration}','-ac','2','-c:a','pcm_f32le',source])
  input_hashes={str(p):sha(p) for p in [short,long]};c=folder/'observe_cancel.c';c.write_text(C_SOURCE);dylib=folder/'observe_cancel.dylib';framework=app/'Contents/Frameworks';ready=folder/'real-worker-ready.json';marker=folder/'real-cancel.jsonl'
  command(['xcrun','clang','-std=c11','-Wall','-Wextra','-Werror','-dynamiclib','-I',ROOT/'contracts',c,'-L',framework,'-lmedia_ffi','-Wl,-rpath,'+str(framework),'-o',dylib]);command(['codesign','--force','--sign','-','--timestamp=none',dylib])
  env=dict(os.environ);env.update(DYLD_INSERT_LIBRARIES=str(dylib),QA002_AI_CANCEL_READY=str(ready),QA002_AI_CANCEL_MARKER=str(marker),TMPDIR=str(folder)+'/',MEDIASTUDIO_FFMPEG=str(app/'Contents/MacOS/ffmpeg'),MEDIASTUDIO_FFPROBE=str(app/'Contents/MacOS/ffprobe'),MEDIASTUDIO_AI_NATIVE_WORKER=str(app/'Contents/MacOS/media-ai-worker'),MEDIASTUDIO_AI_NATIVE_GRAPHS=str(app/'Contents/Resources/ai/graphs'),MEDIASTUDIO_AI_NATIVE_RUNTIME=str(app/'Contents/Frameworks/libonnxruntime.dylib'),MEDIASTUDIO_AI_MODEL=str(app/'Contents/Resources/ai/model.onnx'))
  argv=[str(app/'Contents/MacOS/MediaStudio'),'--ai-self-test','--ai-source',str(short),'--ai-cancel-source',str(long),'--ai-export',str(folder/'exported')];started=time.monotonic();process=subprocess.Popen(argv,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True);deadline=started+180;worker=None
  while process.poll() is None and time.monotonic()<deadline:
   if marker.exists() and 'real-cancel-waiting-for-worker-witness' in marker.read_text():
    if worker is None:
     rows=[v for v in processes() if v[1]==process.pid and str(app/'Contents/MacOS/media-ai-worker') in v[3] and '--staging ' in v[3]]
     if rows:worker=rows[0];stage=Path(worker[3].split('--staging ',1)[1].strip())
    if worker:
     entries=[{'name':p.name,'bytes':p.stat().st_size} for p in stage.glob('*') if p.is_file() and p.stat().st_size>0]
     if entries and any(v[0]==worker[0] for v in processes()):
      witness={'pid':worker[0],'ppid':worker[1],'pgid':worker[2],'stage':str(stage),'nonempty_entries':entries,'monotonic_seconds':time.clock_gettime(time.CLOCK_MONOTONIC),'clock':'CLOCK_MONOTONIC, matching the C interposer'};write(ready,witness);break
   time.sleep(.01)
  out,err=process.communicate(timeout=40);commands.append({'argv':argv,'exit':process.returncode,'stderr':err,'elapsed_seconds':time.monotonic()-started});(evidence/'native-ai-final-package-stdout.json').write_text(out);check(process.returncode==0,commands[-1]);report=json.loads(out);check(report['passed'],report);check(witness is not None,'No real inference worker and nonempty staging witness')
  lines=[json.loads(v) for v in marker.read_text().splitlines()];forward=next(v['monotonic_seconds'] for v in lines if v['phase']=='forwarding-real-cancel');after=time.clock_gettime(time.CLOCK_MONOTONIC);survivors=[v[:3] for v in processes() if v[2]==worker[2]]
  check(witness['monotonic_seconds']<=forward,'Real worker witness must precede forwarded cancellation')
  workspace=stage.parents[1]
  check(not survivors and not stage.exists() and not workspace.exists(),{'survivors':survivors,'stage_exists':stage.exists(),'workspace_exists':workspace.exists()});check(after-forward<=5,after-forward);check(input_hashes=={str(p):sha(p) for p in [short,long]},'Input modified')
  checks.append({'name':'actual_final_package_worker_cancel_and_shutdown','result':'PASS','worker_witness':witness,'cancel_events':lines,'core_cancel_to_process_exit_model_shutdown_upper_bound_ms':(after-forward)*1000,'process_group_survivors':survivors,'staging_absent':True,'workspace_absent':True,'generated_source_hashes_unchanged':True,'model_report':report})
  media=[]
  for name in ['vocals.wav','accompaniment.wav']:
   output=folder/'exported'/name;probe=json.loads(command([shutil.which('ffprobe'),'-v','error','-show_entries','stream=codec_name,sample_rate,channels,duration:format=duration','-of','json',output]).stdout);command([shutil.which('ffmpeg'),'-v','error','-xerror','-i',output,'-map','0:a:0','-f','null','-']);media.append({'name':name,'sha256':sha(output),'probe':probe,'full_decode_exit':0})
  checks.append({'name':'short_synthetic_export_structure','result':'PASS','media':media,'quality_or_listening_acceptance':False});check(before=={str(p.relative_to(app)):sha(p) for p in app.rglob('*') if p.is_file()},'Candidate modified');result='PASS'
 except Exception:result='FAIL';checks.append({'result':'FAIL','error':traceback.format_exc()})
 finally:
  if process and process.poll() is None:
   try:write(folder/'real-worker-ready.json',{'cleanup_release':True});process.communicate(timeout=30)
   except Exception:process.kill();process.communicate()
 value={'result':result,'scope':'Actual unchanged final sandbox candidate --ai-self-test; test-only C interposition postpones running cancellation until independently observed real native worker/nonempty staging, then forwards original Core. Not GUI, ordinary cancellation latency, quality or listening acceptance.','app':str(app),'folder':str(folder),'app_files_before':before,'test_c_source':C_SOURCE,'checks':checks,'commands':commands};write(evidence/'native-ai-final-package-summary.json',value);print(json.dumps({'result':result,'checks':checks},ensure_ascii=False));raise SystemExit(0 if result=='PASS' else 1)
if __name__=='__main__':main()
