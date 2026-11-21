#!/usr/bin/env python3
"""Compile and run production persistence/model tests without UI automation."""
import argparse,hashlib,json,subprocess,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser()
 p.add_argument('--driver',type=Path,default=ROOT/'tests/qa_002/native_history_driver.swift')
 for k in ['frameworks','work','evidence']:p.add_argument('--'+k,type=Path,required=True)
 p.add_argument('--mode',default='red');a=p.parse_args();a.frameworks=a.frameworks.resolve();a.work.mkdir(exist_ok=False);a.evidence.mkdir(parents=True,exist_ok=True)
 source=[s for s in sorted((ROOT/'apps/macos/Sources').glob('*.swift')) if s.name not in ['App.swift','AISeparationView.swift']];driver=a.driver.resolve();before={str(s.relative_to(ROOT)):sha(s) for s in source+[driver]}
 binary=a.work/'HistoryProbe';sdk=subprocess.check_output(['xcrun','--sdk','macosx','--show-sdk-path'],text=True).strip()
 snapshot=a.work/'source-snapshot';snapshot.mkdir();copies=[]
 for s in source+[driver]:
  target=snapshot/s.name;shutil.copy2(s,target);copies.append(target)
 argv=['xcrun','swiftc','-swift-version','6','-strict-concurrency=complete','-warnings-as-errors','-parse-as-library','-target','arm64-apple-macosx27.0','-sdk',sdk,'-module-cache-path',str(a.work/'swift-cache'),'-import-objc-header',str(ROOT/'contracts/media_core_v0.h'),'-L',str(a.frameworks),'-lmedia_ffi','-Xlinker','-rpath','-Xlinker',str(a.frameworks),*map(str,copies),'-o',str(binary)]
 r=subprocess.run(argv,capture_output=True,text=True);(a.evidence/'build.log').write_text(r.stdout+r.stderr);(a.evidence/'build-command.json').write_text(json.dumps({'argv':argv,'exit':r.returncode},indent=2)+'\n');assert r.returncode==0,r.stderr
 reports=[]
 for mode in (['seed','restart','empty','files','queue-fault'] if a.mode=='matrix' else ['unified','unified-restart','unified-empty'] if a.mode=='unified-matrix' else ['dedup','dedup-restart'] if a.mode=='dedup-matrix' else [a.mode]):
  r=subprocess.run([str(binary),mode,str(a.work/'cases')],capture_output=True,text=True,timeout=90);(a.evidence/(mode+'-stdout.json')).write_text(r.stdout);(a.evidence/(mode+'-stderr.log')).write_text(r.stderr);assert r.returncode==0,r.stderr;reports.append(json.loads(r.stdout))
 stable=all(sha(s)==before[str(original.relative_to(ROOT))] for s,original in zip(copies,source+[driver]));current=before=={str(s.relative_to(ROOT)):sha(s) for s in source+[driver]};checks=[x for d in reports for x in d['checks']];out={'result':'PASS' if stable and all(x['result']=='PASS' for x in checks) else 'FAIL','exit':r.returncode,'source_files':before,'compiled_snapshot_unchanged':stable,'current_sources_unchanged':current,'core_sha256':sha(a.frameworks/'libmedia_ffi.dylib'),'binary_sha256':sha(binary),'checks':checks};(a.evidence/'summary.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:v for k,v in out.items() if k!='source_files'},ensure_ascii=False))
if __name__=='__main__':main()
