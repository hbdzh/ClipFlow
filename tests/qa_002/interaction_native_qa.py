#!/usr/bin/env python3
"""Production Swift/Core/AVFoundation QA; not GUI evidence."""
import argparse, hashlib, json, os, subprocess, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,d):p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
def main():
 p=argparse.ArgumentParser()
 for k in ['frameworks','work','evidence','config']:p.add_argument('--'+k,required=True,type=Path)
 p.add_argument('--driver',type=Path,default=ROOT/'tests/qa_002/interaction_native_driver.swift')
 p.add_argument('--ffmpeg',type=Path,default=Path('/opt/homebrew/bin/ffmpeg'));p.add_argument('--ffprobe',type=Path,default=Path('/opt/homebrew/bin/ffprobe'))
 a=p.parse_args();a.work.mkdir(parents=True,exist_ok=False);a.evidence.mkdir(parents=True,exist_ok=True)
 sources=[s for s in sorted((ROOT/'apps/macos/Sources').glob('*.swift')) if s.name not in ['App.swift','AISeparationView.swift']]
 driver=a.driver.resolve();snapshots={str(s.relative_to(ROOT)):sha(s) for s in sources+[driver]}
 binary=a.work/'InteractionObservation';sdk=subprocess.check_output(['xcrun','--sdk','macosx','--show-sdk-path'],text=True).strip()
 argv=['xcrun','swiftc','-swift-version','6','-strict-concurrency=complete','-warnings-as-errors','-parse-as-library','-target','arm64-apple-macosx27.0','-sdk',sdk,'-module-cache-path',str(a.work/'swift-cache'),'-import-objc-header',str(ROOT/'contracts/media_core_v0.h'),'-L',str(a.frameworks),'-lmedia_ffi','-Xlinker','-rpath','-Xlinker',str(a.frameworks),*map(str,sources),str(driver),'-o',str(binary)]
 build=subprocess.run(argv,capture_output=True,text=True);(a.evidence/'native-build.log').write_text(build.stdout+build.stderr);write(a.evidence/'native-build-command.json',{'argv':argv,'exit':build.returncode});assert build.returncode==0,build.stderr
 config=json.loads(a.config.read_text());config['work']=str(a.work);config['real_ffmpeg']=str(a.ffmpeg.resolve());config_path=a.work/'config.json';write(config_path,config)
 env=dict(os.environ);env.update(MEDIASTUDIO_FFMPEG=str(a.ffmpeg.resolve()),MEDIASTUDIO_FFPROBE=str(a.ffprobe.resolve()))
 if 'preview_ready_marker' in config:env['QA002_AUDIO_PREVIEW_READY']=config['preview_ready_marker']
 core_before=sha(a.frameworks/'libmedia_ffi.dylib')
 command=[str(binary),'--config',str(config_path)];started=time.monotonic();r=subprocess.run(command,env=env,capture_output=True,text=True,timeout=600)
 (a.evidence/'native-stdout.json').write_text(r.stdout);(a.evidence/'native-stderr.log').write_text(r.stderr);write(a.evidence/'native-config.json',config);d=json.loads(r.stdout)
 stable=snapshots=={str(s.relative_to(ROOT)):sha(s) for s in sources+[driver]} and core_before==sha(a.frameworks/'libmedia_ffi.dylib')
 summary={'result':'PASS' if r.returncode==0 and d['passed'] and stable else 'FAIL','scope':d['scope'],'argv':command,'exit':r.returncode,'seconds':time.monotonic()-started,'checks_passed':sum(c['result']=='PASS' for c in d['checks']),'checks_failed':sum(c['result']=='FAIL' for c in d['checks']),'source_files':snapshots,'sources_unchanged':stable,'core_sha256':sha(a.frameworks/'libmedia_ffi.dylib'),'driver_sha256':sha(driver),'binary_sha256':sha(binary)}
 write(a.evidence/'native-summary.json',summary);print(json.dumps({k:v for k,v in summary.items() if k!='source_files'},ensure_ascii=False));assert summary['result']=='PASS',d['checks'][-1]
if __name__=='__main__':main()
