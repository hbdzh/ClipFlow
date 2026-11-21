#!/usr/bin/env python3
"""Supplemental sandbox production-model active AI close, with unchanged final components."""
import argparse,hashlib,json,os,plistlib,shutil,subprocess,sys,time,traceback,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(Path(__file__).resolve().parent))
from queue_native_ai_package import processes,check,write,sha

def main():
 p=argparse.ArgumentParser();p.add_argument('--app',required=True);p.add_argument('--evidence',required=True);p.add_argument('--work',required=True);p.add_argument('--driver-evidence',required=True);p.add_argument('--source',required=True);a=p.parse_args();candidate=Path(a.app).resolve();evidence=Path(a.evidence).resolve();evidence.mkdir(parents=True,exist_ok=True);work=Path(a.work).resolve();work.mkdir(parents=True,exist_ok=False);prior=json.loads(Path(a.driver_evidence).read_text());driver_source=prior['driver_source'];swift=[p for p in sorted((ROOT/'apps/macos/Sources').glob('*.swift')) if p.name!='App.swift'];sources={str(p.relative_to(ROOT)):sha(p) for p in swift};commands=[];process=None;worker=None;result='FAIL';detail={}
 def command(args):
  r=subprocess.run(list(map(str,args)),capture_output=True,text=True);commands.append({'argv':list(map(str,args)),'exit':r.returncode,'stderr':r.stderr});check(r.returncode==0,commands[-1]);return r
 before={str(p.relative_to(candidate)):sha(p) for p in candidate.rglob('*') if p.is_file()}
 try:
  driver=work/'AIObservation.swift';driver.write_text(driver_source);compiled=work/'AIObservation';sdk=subprocess.check_output(['xcrun','--sdk','macosx','--show-sdk-path'],text=True).strip();framework=candidate/'Contents/Frameworks'
  command(['xcrun','swiftc','-swift-version','6','-strict-concurrency=complete','-warnings-as-errors','-parse-as-library','-target','arm64-apple-macosx27.0','-sdk',sdk,'-module-cache-path',work/'swift-cache','-import-objc-header',ROOT/'contracts/media_core_v0.h','-L',framework,'-lmedia_ffi','-Xlinker','-rpath','-Xlinker','@executable_path/../Frameworks',*swift,driver,'-o',compiled])
  check(sources=={p:sha(ROOT/p) for p in sources},'Production source changed while compiling observation driver')
  app=work/'MediaStudioAIClose.app';shutil.copytree(candidate,app,symlinks=True);binary=app/'Contents/MacOS/MediaStudio';shutil.copy2(compiled,binary)
  entitlements=work/'entitlements.plist';entitlements.write_text(command(['codesign','-d','--entitlements',':-',candidate]).stdout);command(['codesign','--force','--sign','-','--timestamp=none','--entitlements',entitlements,app]);command(['codesign','--verify','--deep','--strict',app])
  unchanged=[name for name in before if name not in ['Contents/MacOS/MediaStudio','Contents/_CodeSignature/CodeResources']];check(all(sha(app/name)==before[name] for name in unchanged),'Copied component changed')
  bundle_id=plistlib.loads((candidate/'Contents/Info.plist').read_bytes())['CFBundleIdentifier'];folder=Path.home()/f'Library/Containers/{bundle_id}/Data/tmp'/('qa002-active-ai-close-'+uuid.uuid4().hex);folder.mkdir(mode=0o700,parents=True,exist_ok=False);source=folder/'long.wav';shutil.copyfile(a.source,source);ready=folder/'ready.json'
  env=dict(os.environ);env.update(MEDIASTUDIO_FFMPEG=str(app/'Contents/MacOS/ffmpeg'),MEDIASTUDIO_FFPROBE=str(app/'Contents/MacOS/ffprobe'),MEDIASTUDIO_AI_NATIVE_WORKER=str(app/'Contents/MacOS/media-ai-worker'),MEDIASTUDIO_AI_NATIVE_GRAPHS=str(app/'Contents/Resources/ai/graphs'),MEDIASTUDIO_AI_NATIVE_RUNTIME=str(app/'Contents/Frameworks/libonnxruntime.dylib'),MEDIASTUDIO_AI_MODEL=str(app/'Contents/Resources/ai/model.onnx'))
  argv=[str(binary),str(source),str(ready),'close'];process=subprocess.Popen(argv,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True);deadline=time.monotonic()+120;witness=None
  while process.poll() is None and time.monotonic()<deadline:
   if worker is None:
    hits=[v for v in processes() if v[1]==process.pid and str(app/'Contents/MacOS/media-ai-worker') in v[3] and '--staging ' in v[3]]
    if hits:worker=hits[0];stage=Path(worker[3].split('--staging ',1)[1].strip())
   if worker:
    entries=[{'name':p.name,'bytes':p.stat().st_size} for p in stage.glob('*') if p.is_file() and p.stat().st_size>0]
    if entries and any(v[0]==worker[0] for v in processes()):witness={'pid':worker[0],'pgid':worker[2],'stage':str(stage),'nonempty_entries':entries};write(ready,witness);break
   time.sleep(.01)
  out,err=process.communicate(timeout=30);(evidence/'native-ai-active-close-stdout.json').write_text(out);check(process.returncode==0,{'exit':process.returncode,'stderr':err});report=json.loads(out);check(report['passed'] and report['operation']=='close',report);check(witness is not None,'Missing worker witness');survivors=[v[:3] for v in processes() if v[2]==worker[2]];check(not survivors and not stage.exists() and not stage.parents[1].exists(),{'survivors':survivors,'stage':str(stage)});check(sha(source)==sha(Path(a.source)),'Source changed')
  check(before=={str(p.relative_to(candidate)):sha(p) for p in candidate.rglob('*') if p.is_file()},'Final candidate changed');detail={'report':report,'worker_witness':witness,'survivors':survivors,'stage_and_workspace_absent':True,'input_unchanged':True,'exact_copied_components':unchanged,'driver_binary_sha256':sha(binary),'app':str(app),'folder':str(folder)};result='PASS'
 except Exception:detail={'error':traceback.format_exc()}
 finally:
  if process and process.poll() is None:
   try:write(ready,{'cleanup_release':True});process.communicate(timeout=30)
   except Exception:process.kill();process.communicate()
 value={'result':result,'scope':'Supplemental sandbox observation app with identical production Swift sources, final Core/worker/ORT/model. Actual ApplicationShutdown closes a running AISeparationModel after independent real-worker/nonempty-output witness. Not final-candidate main binary or GUI evidence.','driver_source':driver_source,'production_source_hashes':sources,'detail':detail,'commands':commands};write(evidence/'native-ai-active-close-summary.json',value);print(json.dumps({'result':result,'detail':detail},ensure_ascii=False));raise SystemExit(0 if result=='PASS' else 1)
if __name__=='__main__':main()
