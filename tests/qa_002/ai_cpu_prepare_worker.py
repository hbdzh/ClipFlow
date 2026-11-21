#!/usr/bin/env python3
"""Prepare standalone QA worker by changing only its inherit-only signature.

Every file-backed Mach-O code/data section must remain byte-identical. This is
not a substitute for the original signed sandbox helper's own execution test.
"""
import argparse,hashlib,json,shutil,struct,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--app',type=Path,required=True);p.add_argument('--work',type=Path,required=True);p.add_argument('--report',type=Path,required=True);a=p.parse_args();a.work.mkdir(mode=0o700,parents=True,exist_ok=False);assert not a.report.exists();app=a.app.resolve();source=app/'Contents/MacOS/media-ai-worker';worker=a.work/'media-ai-worker';shutil.copy2(source,worker)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def sections(p):
 b=p.read_bytes();assert struct.unpack_from('<I',b)[0]==0xfeedfacf;n=struct.unpack_from('<I',b,16)[0];pos=32;out={}
 for _ in range(n):
  cmd,size=struct.unpack_from('<II',b,pos);assert size>=8 and pos+size<=len(b)
  if cmd==0x19:
   count=struct.unpack_from('<I',b,pos+64)[0];assert size>=72+count*80
   for j in range(count):
    q=pos+72+j*80;name=b[q:q+16].split(b'\0')[0].decode();segment=b[q+16:q+32].split(b'\0')[0].decode();length=struct.unpack_from('<Q',b,q+40)[0];offset=struct.unpack_from('<I',b,q+48)[0];flags=struct.unpack_from('<I',b,q+64)[0]
    if offset and (flags&0xff) not in (1,0xc,0x12):
     assert offset+length<=len(b);out[segment+','+name]={'bytes':length,'sha256':hashlib.sha256(b[offset:offset+length]).hexdigest()}
  pos+=size
 assert '__TEXT,__text' in out
 return out
before=sections(source);commands=[]
for argv in [['codesign','--remove-signature',str(worker)],['codesign','--force','--sign','-','--timestamp=none',str(worker)],['codesign','--verify','--strict',str(worker)]]:
 r=subprocess.run(argv,capture_output=True,text=True);commands.append({'argv':argv,'exit':r.returncode,'stderr':r.stderr});assert r.returncode==0
assert sections(worker)==before
ent=subprocess.run(['codesign','-d','--entitlements',':-',str(worker)],capture_output=True,text=True);assert 'app-sandbox' not in ent.stdout and 'inherit' not in ent.stdout
r=subprocess.run([str(worker),'--help'],capture_output=True,text=True);assert r.returncode==1 and r.stderr.strip()=='invalid arguments';commands.append({'argv':[str(worker),'--help'],'exit':r.returncode,'stdout':r.stdout,'stderr':r.stderr})
d={'result':'PASS','scope':'Final native helper copy, test-only signature changed; standalone CPU QA, not signed sandbox execution evidence','source_worker':str(source),'source_sha256':sha(source),'worker':str(worker),'worker_sha256':sha(worker),'all_MachO_file_backed_sections_identical':True,'sections':before,'commands':commands,'entitlements_after':ent.stdout,'test_script_sha256':sha(Path(__file__))};a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(json.dumps(d,indent=2)+'\n');print(str(worker))
