#!/usr/bin/env python3
"""Signature-only standalone copies of final inherit-only media helpers."""
import argparse,hashlib,json,shutil,struct,subprocess
from pathlib import Path
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def sections(p):
 b=p.read_bytes();assert struct.unpack_from('<I',b)[0]==0xfeedfacf;pos=32;out={}
 for _ in range(struct.unpack_from('<I',b,16)[0]):
  command,size=struct.unpack_from('<II',b,pos);assert size>=8 and pos+size<=len(b)
  if command==0x19:
   count=struct.unpack_from('<I',b,pos+64)[0];assert size>=72+80*count
   for j in range(count):
    q=pos+72+80*j;name=b[q:q+16].split(b'\0')[0].decode();segment=b[q+16:q+32].split(b'\0')[0].decode();length=struct.unpack_from('<Q',b,q+40)[0];offset=struct.unpack_from('<I',b,q+48)[0];flags=struct.unpack_from('<I',b,q+64)[0]
    if offset and flags&255 not in [1,12,18]:assert offset+length<=len(b);out[segment+','+name]={'bytes':length,'sha256':hashlib.sha256(b[offset:offset+length]).hexdigest()}
  pos+=size
 assert '__TEXT,__text' in out;return out
def main():
 p=argparse.ArgumentParser();p.add_argument('--app',type=Path,required=True);p.add_argument('--work',type=Path,required=True);p.add_argument('--report',type=Path,required=True);a=p.parse_args();a.work.mkdir(mode=0o700,parents=True,exist_ok=False);folder=a.work/'Contents/MacOS';folder.mkdir(parents=True);(a.work/'Contents/Frameworks').symlink_to(a.app.resolve()/'Contents/Frameworks',target_is_directory=True);rows=[]
 for name in ['ffmpeg','ffprobe']:
  original=a.app.resolve()/'Contents/MacOS'/name;copy=folder/name;shutil.copy2(original,copy);before=sections(original);commands=[]
  for argv in [['codesign','--remove-signature',str(copy)],['codesign','--force','--sign','-','--timestamp=none',str(copy)],['codesign','--verify','--strict',str(copy)],[str(copy),'-version']]:
   r=subprocess.run(argv,capture_output=True,text=True);commands.append({'argv':argv,'exit':r.returncode,'stderr':r.stderr,'stdout':r.stdout});assert r.returncode==0
  assert sections(copy)==before;rows.append({'source':str(original),'source_sha256':sha(original),'copy':str(copy),'copy_sha256':sha(copy),'all_file_backed_MachO_sections_identical':True,'sections':before,'commands':commands})
 a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(json.dumps({'result':'PASS','scope':'Final helper code/data unchanged; only inherit-only signature removed for standalone native QA. Original signatures and sandbox execution are separate GUI evidence.','tools':rows},indent=2)+'\n');print(str(folder))
if __name__=='__main__':main()
