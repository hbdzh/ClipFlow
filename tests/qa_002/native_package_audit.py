#!/usr/bin/env python3
"""Bounded local development-bundle audit, not distribution/legal acceptance."""
import argparse,hashlib,json,plistlib,re,subprocess
from pathlib import Path

def digest(path):
 h=hashlib.sha256()
 with path.open('rb') as stream:
  for data in iter(lambda:stream.read(1024*1024),b''):h.update(data)
 return h.hexdigest()
def run(args):
 r=subprocess.run(list(map(str,args)),capture_output=True,text=True)
 return {'argv':list(map(str,args)),'exit':r.returncode,'stdout':r.stdout,'stderr':r.stderr}
def audit(app):
 app=app.resolve(strict=True);info=plistlib.loads((app/'Contents/Info.plist').read_bytes());commands=[]
 verify=run(['codesign','--verify','--deep','--strict',app]);commands.append(verify)
 sign=run(['codesign','-d','--verbose=4',app]);commands.append(sign)
 ent=run(['codesign','-d','--entitlements',':-',app]);commands.append(ent)
 entitlements=plistlib.loads(ent['stdout'].encode())
 macho=[];external=[]
 names=['Contents/MacOS/'+info['CFBundleExecutable'],'Contents/MacOS/ffmpeg','Contents/MacOS/ffprobe',
        'Contents/MacOS/media-ai-worker','Contents/Frameworks/libmedia_ffi.dylib','Contents/Frameworks/libonnxruntime.dylib']
 for name in names:
  path=app/name;deps=run(['otool','-L',path]);identity=run(['otool','-D',path]);loads=run(['otool','-l',path]);arch=run(['lipo','-archs',path]);commands += [deps,identity,loads,arch]
  own_ids={line.strip() for line in identity['stdout'].splitlines() if line.strip().startswith('@')}
  values=[]
  for line in deps['stdout'].splitlines():
   if not line.startswith('\t'):continue
   dependency=line.strip().split(' (compatibility')[0];values.append(dependency)
   if dependency in own_ids or dependency.startswith(('/usr/lib/','/System/Library/')):continue
   if dependency.startswith('@rpath/') and (app/'Contents/Frameworks'/dependency.removeprefix('@rpath/')).is_file():continue
   external.append({'binary':name,'dependency':dependency})
  raw=path.read_bytes()
  macho.append({'file':name,'sha256':digest(path),'architectures':arch['stdout'].strip(),
                'dependencies':values,'rpaths':re.findall(r'cmd LC_RPATH\n.*?path ([^\n]+?) \(offset',loads['stdout'],re.S),
                'has_source_debug_project_strings':b'/Volumes/External/Projects/' in raw,
                'has_build_prefix_strings':b'--prefix=' in raw})
 graph_root=app/'Contents/Resources/ai/graphs';graphs=json.loads((graph_root/'manifest.json').read_text());model=app/'Contents/Resources/ai/model.onnx'
 graph_checks={name:digest(graph_root/name)==sha for name,sha in graphs['graphs'].items()}
 assert len(graph_checks)==8 and all(graph_checks.values())
 assert digest(model)==graphs['model_sha256']=='f27742bb52b24cb039614dc76d0764e279df3eb2a9d6daa82c6daee3369ec747'
 main=(app/'Contents/MacOS'/info['CFBundleExecutable']).read_bytes()
 homebrew_defaults=any(value in main for value in [b'/opt/homebrew/bin/ffmpeg',b'/opt/homebrew/bin/ffprobe'])
 symlinks=[str(p.relative_to(app)) for p in app.rglob('*') if p.is_symlink()]
 files={str(p.relative_to(app)):digest(p) for p in sorted(app.rglob('*')) if p.is_file()}
 assert verify['exit']==0 and not symlinks and not external
 assert entitlements.get('com.apple.security.app-sandbox') is True
 assert not entitlements.get('com.apple.security.network.client',False)
 return {'result':'FAIL_IMPLICIT_HOMEBREW_DEFAULT' if homebrew_defaults else 'PASS_LOCAL_RUNTIME_DEPENDENCIES',
         'scope':'Local macOS arm64 developer bundle only; no clean OS/new OS account/release/legal acceptance',
         'app':str(app),'bundle_id':info['CFBundleIdentifier'],'minimum_os':info.get('LSMinimumSystemVersion'),
         'file_count':len(files),'files':files,'macho':macho,'unbundled_non_system_load_dependencies':external,
         'implicit_Homebrew_default_literal_in_main':homebrew_defaults,'entitlements':entitlements,
         'ad_hoc_signature':'Signature=adhoc' in sign['stderr'],'notarization_or_public_distribution_approved':False,
         'all_8_fixed_graphs_match_manifest':True,'model_sha256':digest(model),
         'license_files':{k:v for k,v in files.items() if k.startswith('Contents/Resources/licenses/')},
         'path_string_interpretation':'Debug/source paths and FFmpeg configure prefixes are reported as informational; LC_LOAD_DYLIB/rpath and actual relocated runtime determine load dependencies. This does not claim all absolute text strings removed.',
         'commands':commands}
def main():
 p=argparse.ArgumentParser();p.add_argument('--app',required=True,type=Path);p.add_argument('--report',required=True,type=Path);a=p.parse_args()
 d=audit(a.app);a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({k:d[k] for k in ['result','bundle_id','file_count','implicit_Homebrew_default_literal_in_main','unbundled_non_system_load_dependencies']},ensure_ascii=False))
if __name__=='__main__':main()
