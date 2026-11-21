#!/usr/bin/env python3
"""Build only the data store + silent file QA; never loads application code."""
import argparse,hashlib,json,subprocess,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument('--work',type=Path,required=True);p.add_argument('--evidence',type=Path,required=True);a=p.parse_args();a.work.mkdir(exist_ok=False);a.evidence.mkdir(parents=True,exist_ok=True)
 source=[ROOT/'apps/macos/Sources/AIComponentStore.swift',ROOT/'tests/qa_002/ai_component_store_driver.swift'];before={str(p.relative_to(ROOT)):sha(p) for p in source};copies=[]
 for path in source:target=a.work/path.name;shutil.copy2(path,target);copies.append(target)
 binary=a.work/'ComponentProbe';argv=['xcrun','swiftc','-swift-version','6','-strict-concurrency=complete','-warnings-as-errors','-parse-as-library','-module-cache-path',str(a.work/'swift-cache'),*map(str,copies),'-o',str(binary)]
 build=subprocess.run(argv,capture_output=True,text=True);(a.evidence/'build.log').write_text(build.stdout+build.stderr);(a.evidence/'build-command.json').write_text(json.dumps({'argv':argv,'exit':build.returncode},indent=2)+'\n');assert build.returncode==0,build.stderr
 reports=[]
 for mode in ['seed','restart','edges','atomic','final-restart']:
  run=subprocess.run([str(binary),mode,str(a.work/'cases')],capture_output=True,text=True,timeout=90);(a.evidence/(mode+'-stdout.json')).write_text(run.stdout);(a.evidence/(mode+'-stderr.log')).write_text(run.stderr);assert run.returncode==0,(mode,run.stderr);reports.append(json.loads(run.stdout))
 checks=[row for report in reports for row in report['checks']];stable=all(sha(copy)==before[str(path.relative_to(ROOT))] for path,copy in zip(source,copies));current=before=={str(path.relative_to(ROOT)):sha(path) for path in source}
 report={'result':'PASS' if stable and all(row['result']=='PASS' for row in checks) else 'FAIL','scope':'Synthetic file-only; no executable component installation or inference','source_files':before,'snapshot_stable':stable,'current_sources_stable':current,'binary_sha256':sha(binary),'checks':checks};(a.evidence/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__':main()
