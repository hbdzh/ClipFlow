#!/usr/bin/env python3
"""Compile the current native client against an immutable accepted Core."""
import argparse,hashlib,json,subprocess
from pathlib import Path
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser()
 for key in ['frameworks','output-base','work','report']:p.add_argument('--'+key,type=Path,required=True)
 a=p.parse_args();root=Path(__file__).resolve().parents[2];a.work.mkdir(exist_ok=False);binary=a.output_base/'Contents/MacOS/MediaStudio';binary.parent.mkdir(parents=True,exist_ok=False)
 sources=sorted((root/'apps/macos/Sources').glob('*.swift'));before={str(x.relative_to(root)):sha(x) for x in sources};sdk=subprocess.check_output(['xcrun','--sdk','macosx','--show-sdk-path'],text=True).strip()
 args=['xcrun','swiftc','-swift-version','6','-strict-concurrency=complete','-warnings-as-errors','-parse-as-library','-target','arm64-apple-macosx27.0','-sdk',sdk,'-module-cache-path',str(a.work/'swift-cache'),'-import-objc-header',str(root/'contracts/media_core_v0.h'),'-L',str(a.frameworks),'-lmedia_ffi','-Xlinker','-rpath','-Xlinker','@executable_path/../Frameworks',*map(str,sources),'-o',str(binary)]
 r=subprocess.run(args,capture_output=True,text=True);stable=before=={str(x.relative_to(root)):sha(x) for x in sources};d={'result':'PASS' if r.returncode==0 and stable else 'FAIL','argv':args,'exit':r.returncode,'stderr':r.stderr,'sources_unchanged':stable,'source_files':before,'immutable_core_sha256':sha(a.frameworks/'libmedia_ffi.dylib')};a.report.write_text(json.dumps(d,indent=2)+'\n');assert r.returncode==0 and stable,r.stderr
if __name__=='__main__':main()
