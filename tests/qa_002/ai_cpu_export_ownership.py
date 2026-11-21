#!/usr/bin/env python3
"""Production Swift export owner double-failure observation, not GUI/ENOSPC.

A test interposer pauses after the first real file copy. The controller hides
its own generated second stem and denies removal from its owned target parent.
No production code, Core state or SecurityAccess result is fabricated.
"""
import argparse,hashlib,json,os,shutil,subprocess,sys,time,traceback
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,d):Path(p).write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
DRIVER=r'''
import AppKit
import Foundation
@main struct ExportOwnershipObservation {
 @MainActor static func main() async {
  _ = NSApplication.shared
  let a=CommandLine.arguments
  func argument(_ key:String) -> URL {
   URL(fileURLWithPath:a[a.firstIndex(of:key)!+1])
  }
  let source=argument("--source"), destination=argument("--destination")
  let ai=AISeparationModel()
  await ai.importSource(source);await ai.start()
  guard let result=ai.resultDirectory, ai.issue==nil else {
   print("{\"passed\":false,\"phase\":\"real-separation\"}");exit(1)
  }
  await ai.export(to:destination)
  let prior=ai.sourceURL
  let retained=ai.exportAccessRetained
  let stage=ai.exportCleanupDirectory
  let isolated=ai.cleanupUnconfirmed
  await ai.importSource(URL(fileURLWithPath:"/private/tmp/qa002-no-new-ai-source.wav"))
  await ai.start();await ai.export(to:destination.appendingPathExtension("retry"))
  var closeRejected=false
  do {try await ai.shutdown()} catch {closeRejected=true}
  let passed=isolated && retained && ai.exportAccessRetained && ai.cleanupUnconfirmed && ai.sourceURL==prior && closeRejected && !ai.isRunning && stage != nil
  let d:[String:Any] = ["passed":passed,"scope":"Production AISeparationModel/SecurityAccess standalone observation; not GUI or actual sandbox grant evidence","cleanup_unconfirmed":ai.cleanupUnconfirmed,"export_access_retained":ai.exportAccessRetained,"cleanup_directory":stage?.path ?? "","model_result_directory":result.path,"source_unchanged_by_new_import":ai.sourceURL==prior,"new_retry_absent":!FileManager.default.fileExists(atPath:destination.appendingPathExtension("retry").path),"shutdown_rejected":closeRejected,"issue":ai.issue ?? ""]
  let data=try! JSONSerialization.data(withJSONObject:d,options:[.sortedKeys]);FileHandle.standardOutput.write(data);FileHandle.standardOutput.write(Data("\n".utf8));exit(passed ? 0:1)
 }
}
'''
INTERPOSER=r'''
#define _DARWIN_C_SOURCE
#include <copyfile.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
static int observed_copy(const char *from,const char *to,copyfile_state_t state,copyfile_flags_t flags) {
 int rc=copyfile(from,to,state,flags);
 const char *marker=getenv("QA002_EXPORT_COPY_MARKER");const char *release=getenv("QA002_EXPORT_COPY_RELEASE");
 if(rc==0 && marker && release && strstr(to,"/.mediastudio-export-") && strlen(to)>11 && strcmp(to+strlen(to)-11,"/vocals.wav")==0) {
  FILE *f=fopen(marker,"w");if(f){fprintf(f,"%s\n%s\n",from,to);fclose(f);}
  for(int i=0;i<12000 && access(release,F_OK)!=0;i++){struct timespec wait={0,10000000};nanosleep(&wait,NULL);}
 }
 return rc;
}
__attribute__((used)) static struct{const void *replacement;const void *replacee;} replacements[] __attribute__((section("__DATA,__interpose")))={{(const void*)observed_copy,(const void*)copyfile}};
'''
def main():
 p=argparse.ArgumentParser();p.add_argument('--app',required=True,type=Path);p.add_argument('--worker',required=True,type=Path);p.add_argument('--work',required=True,type=Path);p.add_argument('--evidence',required=True,type=Path);a=p.parse_args();a.work=a.work.resolve();a.work.mkdir(mode=0o700,parents=True,exist_ok=False);a.evidence.mkdir(parents=True,exist_ok=False);app=a.app.resolve();frameworks=app/'Contents/Frameworks';sources=[p for p in sorted((ROOT/'apps/macos/Sources').glob('*.swift')) if p.name!='App.swift'];source_hashes={str(p.relative_to(ROOT)):sha(p) for p in sources};commands=[];proc=None;hidden=None;original=None;stage=None;model_workspace=None;target=a.work/'target';target.mkdir();destination=target/'new-output';marker=a.work/'copied-first.txt';release=a.work/'release';result='FAIL';detail={}
 def command(argv):
  r=subprocess.run(list(map(str,argv)),capture_output=True,text=True);commands.append({'argv':list(map(str,argv)),'exit':r.returncode,'stderr':r.stderr});assert r.returncode==0,commands[-1];return r
 try:
  source=a.work/'source.wav';command(['/opt/homebrew/bin/ffmpeg','-nostdin','-v','error','-f','lavfi','-i','sine=frequency=410:sample_rate=44100:duration=1','-ac','2','-c:a','pcm_f32le',source]);input_sha=sha(source)
  driver=a.work/'ExportOwnership.swift';driver.write_text(DRIVER);binary=a.work/'ExportOwnership';frameworks=app/'Contents/Frameworks';sdk=subprocess.check_output(['xcrun','--sdk','macosx','--show-sdk-path'],text=True).strip();command(['xcrun','swiftc','-swift-version','6','-strict-concurrency=complete','-warnings-as-errors','-parse-as-library','-target','arm64-apple-macosx27.0','-sdk',sdk,'-module-cache-path',a.work/'swift-cache','-import-objc-header',ROOT/'contracts/media_core_v0.h','-L',frameworks,'-lmedia_ffi','-Xlinker','-rpath','-Xlinker',str(frameworks),*sources,driver,'-o',binary])
  c=a.work/'copy-pause.c';c.write_text(INTERPOSER);dylib=a.work/'copy-pause.dylib';command(['xcrun','clang','-std=c11','-Wall','-Wextra','-Werror','-dynamiclib',c,'-o',dylib]);command(['codesign','--force','--sign','-','--timestamp=none',dylib])
  env=dict(os.environ);env.update(MEDIASTUDIO_FFMPEG='/opt/homebrew/bin/ffmpeg',MEDIASTUDIO_FFPROBE='/opt/homebrew/bin/ffprobe',MEDIASTUDIO_AI_NATIVE_WORKER=str(a.worker.resolve()),MEDIASTUDIO_AI_NATIVE_GRAPHS=str(app/'Contents/Resources/ai/graphs'),MEDIASTUDIO_AI_NATIVE_RUNTIME=str(app/'Contents/Frameworks/libonnxruntime.dylib'),MEDIASTUDIO_AI_MODEL=str(app/'Contents/Resources/ai/model.onnx'),TMPDIR=str(a.work)+'/',DYLD_INSERT_LIBRARIES=str(dylib),QA002_EXPORT_COPY_MARKER=str(marker),QA002_EXPORT_COPY_RELEASE=str(release))
  argv=[str(binary),'--source',str(source),'--destination',str(destination)];proc=subprocess.Popen(argv,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True);deadline=time.monotonic()+60
  while proc.poll() is None and time.monotonic()<deadline:
   if marker.exists() and len(marker.read_text().splitlines())==2:break
   time.sleep(.01)
  assert marker.exists(),('No first-copy witness',proc.poll());first_source,first_target=map(Path,marker.read_text().splitlines());assert first_target.parent.parent==target and first_target.name=='vocals.wav' and first_target.stat().st_size>0
  stage=first_target.parent;model_workspace=first_source.parent.parent;assert model_workspace.name.startswith('mediastudio-ai-') and sha(model_workspace/'input.wav')==input_sha;original=first_source.with_name('accompaniment.wav');assert original.is_file();hidden=original.with_name('.qa002-hidden-accompaniment.wav');assert not hidden.exists();original.rename(hidden);target.chmod(0o500);witness={'first_stem_bytes':first_target.stat().st_size,'stage':str(stage),'second_stem_temporarily_hidden':str(original),'target_parent_mode':'0o500'};release.write_text('release\n')
  out,err=proc.communicate(timeout=40);commands.append({'argv':argv,'exit':proc.returncode,'stderr':err});(a.evidence/'cpu-export-ownership-stdout.json').write_text(out);assert proc.returncode==0,commands[-1];observed=json.loads(out);assert observed['passed'] and stage.exists() and not destination.exists() and sha(source)==input_sha;assert source_hashes=={str(p.relative_to(ROOT)):sha(p) for p in sources};detail={'observation':observed,'witness':witness,'source_unchanged':True,'destination_unpublished':True,'stage_retained_after_cleanup_failure':True};result='PASS'
 except Exception:detail={'error':traceback.format_exc()}
 finally:
  release.write_text('QA cleanup release\n')
  if proc and proc.poll() is None:
   try:proc.communicate(timeout=10)
   except Exception:proc.kill();proc.communicate()
  target.chmod(0o700)
  if hidden and hidden.exists() and original and not original.exists():hidden.rename(original)
  if stage and stage.exists():shutil.rmtree(stage)
  if model_workspace and model_workspace.exists():
   assert sha(model_workspace/'input.wav')==input_sha;shutil.rmtree(model_workspace)
  roots=list(a.work.glob('mediastudio-ai-*'))
  for owned in roots:
   if owned.is_dir():shutil.rmtree(owned)
  detail['qa_test_teardown_after_observer_exit']={'target_permissions_restored':True,'hidden_stem_restored_before_owned_workspace_cleanup':hidden is not None,'owned_stage_and_workspace_removed':not any(a.work.glob('mediastudio-ai-*')) and (stage is None or not stage.exists()) and (model_workspace is None or not model_workspace.exists()),'not_application_safe_close_success':True}
 value={'result':result,'scope':'Production Swift owner double-failure; real first file copy, test-only temporary missing second generated stem plus real parent permission denial; non-sandbox observer, not final GUI or ENOSPC','production_source_hashes':source_hashes,'driver_source':DRIVER,'interposer_source':INTERPOSER,'component_hashes':{str(p):sha(p) for p in [frameworks/'libmedia_ffi.dylib',a.worker.resolve(),app/'Contents/Frameworks/libonnxruntime.dylib',app/'Contents/Resources/ai/model.onnx']},'detail':detail,'commands':commands};write(a.evidence/'cpu-export-ownership-summary.json',value);print(json.dumps({'result':result,'detail':detail},ensure_ascii=False));raise SystemExit(0 if result=='PASS' else 1)
if __name__=='__main__':main()
