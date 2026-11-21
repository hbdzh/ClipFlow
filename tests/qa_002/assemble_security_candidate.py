#!/usr/bin/env python3
"""Create a fresh local maintenance candidate; never overwrite an existing app."""
import argparse,hashlib,json,plistlib,shutil,subprocess
from pathlib import Path

def sha(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def files(folder):return {str(p.relative_to(folder)):sha(p) for p in sorted(folder.rglob('*')) if p.is_file()}
def main():
 p=argparse.ArgumentParser()
 for k in ['previous','tools','licenses','output','evidence']:p.add_argument('--'+k,type=Path,required=True)
 p.add_argument('--runtime',type=Path,help='Independently reviewed controlled ORT dylib; only replaces the new copy')
 p.add_argument('--runtime-closure',type=Path,help='Exact build closure receipt paired with --runtime')
 a=p.parse_args();assert bool(a.runtime)==bool(a.runtime_closure),'Runtime and closure must be supplied together';assert not a.output.exists();a.evidence.mkdir(parents=True,exist_ok=True)
 assert not a.output.resolve().is_relative_to(a.previous.resolve()),'Output must be outside the previous app'
 if a.runtime:
  closure=json.loads(a.runtime_closure.read_text())
  assert closure['sha256']==sha(a.runtime),'Runtime does not match the reviewed closure artifact'
  assert not (a.previous/'Contents/Frameworks/libonnxruntime.dylib').is_symlink(),'Runtime replacement target must be a regular file'
 before=files(a.previous);commands=[];input_hashes={name:sha(a.tools/name) for name in ['ffmpeg','ffprobe']};notice_hashes=files(a.licenses)
 assert all(name in notice_hashes for name in ['ffmpeg-LICENSE.md','ffmpeg-IJG-NOTICE.txt','license-text-sha256.json'])
 def run(argv):
  r=subprocess.run(list(map(str,argv)),capture_output=True,text=True);commands.append({'argv':list(map(str,argv)),'exit':r.returncode,'stdout':r.stdout,'stderr':r.stderr});assert r.returncode==0,commands[-1];return r
 shutil.copytree(a.previous,a.output,symlinks=True)
 for name in input_hashes:shutil.copy2(a.tools/name,a.output/'Contents/MacOS'/name)
 runtime_input=None
 if a.runtime:
  runtime_input={'unsigned_sha256':sha(a.runtime),'path':str(a.runtime.resolve()),'closure_path':str(a.runtime_closure.resolve()),'closure_sha256':sha(a.runtime_closure)}
  shutil.copy2(a.runtime,a.output/'Contents/Frameworks/libonnxruntime.dylib')
 license_target=a.output/'Contents/Resources/licenses'
 # This is only the newly copied candidate's known license directory.
 assert license_target.is_dir() and not license_target.is_symlink()
 for item in license_target.rglob('*'):assert not item.is_symlink()
 shutil.rmtree(license_target);shutil.copytree(a.licenses,license_target)
 helper_entitlements=a.evidence/'release-helper-entitlements.plist'
 app_entitlements=a.evidence/'release-app-entitlements.plist'
 for path,target in [(a.previous/'Contents/MacOS/ffmpeg',helper_entitlements),(a.previous,app_entitlements)]:
  content=run(['codesign','-d','--entitlements',':-',path]).stdout;plistlib.loads(content.encode());target.write_text(content)
 for name in input_hashes:run(['codesign','--force','--sign','-','--timestamp=none','--entitlements',helper_entitlements,a.output/'Contents/MacOS'/name])
 if a.runtime:run(['codesign','--force','--sign','-','--timestamp=none',a.output/'Contents/Frameworks/libonnxruntime.dylib'])
 run(['codesign','--force','--sign','-','--timestamp=none','--entitlements',app_entitlements,a.output])
 run(['codesign','--verify','--deep','--strict',a.output])
 after=files(a.output);assert files(a.previous)==before;assert files(license_target)==notice_hashes
 allowed=['Contents/MacOS/ffmpeg','Contents/MacOS/ffprobe','Contents/MacOS/MediaStudio','Contents/_CodeSignature/CodeResources']
 if a.runtime:allowed.append('Contents/Frameworks/libonnxruntime.dylib')
 changed=[n for n in before if before[n]!=after.get(n)];assert all(n in allowed or n.startswith('Contents/Resources/licenses/') for n in changed),changed
 assert all(after[n]==before[n] for n in before if n not in changed)
 d={'result':'PASS','app':str(a.output.resolve()),'previous':str(a.previous.resolve()),'previous_unchanged':True,'bundle_id_unchanged':True,'input_unsigned_tools':input_hashes,'controlled_runtime_input':runtime_input,'license_materials':notice_hashes,'changed_previous_files':changed,'added_files':sorted(set(after)-set(before)),'bundle_files':after,'commands':commands,'scope':'Local ad-hoc sandbox candidate; no signing certificate or publishing; Core/worker/graphs/model bytes unchanged; ORT changed only when explicitly paired with a closure receipt'}
 (a.evidence/'release-assembly.json').write_text(json.dumps(d,indent=2)+'\n');print(json.dumps({'result':d['result'],'files':len(after),'changed':changed,'added':d['added_files']}))
if __name__=='__main__':main()
