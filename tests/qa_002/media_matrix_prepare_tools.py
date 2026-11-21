#!/usr/bin/env python3
"""Standalone test copies of packaged helpers; code/data remain unchanged."""
import argparse,hashlib,json,shutil,struct,subprocess
from pathlib import Path
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def sections(p):
 b=p.read_bytes();assert struct.unpack_from('<I',b)[0]==0xfeedfacf;pos=32;out={}
 for _ in range(struct.unpack_from('<I',b,16)[0]):
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
def main():
 p=argparse.ArgumentParser()
 for key in ['app','work','report']:p.add_argument('--'+key,type=Path,required=True)
 a=p.parse_args();a.work.mkdir(mode=0o700,exist_ok=False);rows=[]
 for name in ['ffmpeg','ffprobe']:
  source=a.app/'Contents/MacOS'/name;target=a.work/name;shutil.copy2(source,target);before=sections(source);commands=[]
  for argv in [['codesign','--remove-signature',str(target)],['codesign','--force','--sign','-','--timestamp=none',str(target)],['codesign','--verify','--strict',str(target)],[str(target),'-version']]:
   r=subprocess.run(argv,capture_output=True,text=True);commands.append({'argv':argv,'exit':r.returncode,'stdout':r.stdout,'stderr':r.stderr});assert r.returncode==0,commands[-1]
  assert sections(target)==before
  ent=subprocess.run(['codesign','-d','--entitlements',':-',str(target)],capture_output=True,text=True);assert 'app-sandbox' not in ent.stdout and 'inherit' not in ent.stdout
  rows.append({'name':name,'source':str(source),'source_sha256':sha(source),'test_copy':str(target),'test_copy_sha256':sha(target),'all_file_backed_code_data_sections_identical':True,'sections':before,'commands':commands})
 a.report.write_text(json.dumps({'result':'PASS','scope':'Signature-only helper copies for standalone Core QA; original signed sandbox helpers separately GUI tested','helpers':rows},indent=2)+'\n')
if __name__=='__main__':main()
