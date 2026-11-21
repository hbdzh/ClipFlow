#!/usr/bin/env python3
"""Create owned MP4 edit-list negative controls without changing media bytes.

Requires moov after mdat, so enlarging moov never changes sample chunk offsets.
Only the first (video) trak's elst is changed. The original is never overwritten.
"""
import argparse, hashlib, json, struct
from pathlib import Path
def atoms(data):
 offset=0
 while offset<len(data):
  size,kind=struct.unpack_from('>I4s',data,offset)
  assert size>=8 and offset+size<=len(data)
  yield kind,data[offset+8:offset+size]
  offset+=size
def atom(kind,body):return struct.pack('>I4s',len(body)+8,kind)+body
def replace_edit(data,mode):
 seen=False
 def descend(data,path=()):
  nonlocal seen
  result=b''
  for kind,body in atoms(data):
   if kind==b'elst' and not seen:
    assert body[:4]==b'\0'*4 and struct.unpack_from('>I',body,4)[0]==1
    duration,media_time,rate=struct.unpack_from('>IiI',body,8);assert duration%2==0 and rate==65536
    if mode=='repeat':body=b'\0'*4+struct.pack('>I',2)+struct.pack('>IiI',duration//2,media_time,65536)*2
    else:body=b'\0'*4+struct.pack('>I',1)+struct.pack('>IiI',duration,media_time,32768)
    seen=True
   elif kind in [b'moov',b'trak',b'edts']:body=descend(body,path+(kind,))
   result+=atom(kind,body)
  return result
 out=descend(data);assert seen;return out
def main():
 p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True);a=p.parse_args();a.output_dir.mkdir(parents=True,exist_ok=False)
 data=a.source.read_bytes();kinds=[k for k,b in atoms(data)];assert kinds.index(b'moov')>kinds.index(b'mdat')
 rows=[]
 for mode in ['repeat','half-speed']:
  out=a.output_dir/(mode+'.mp4');out.write_bytes(replace_edit(data,mode));rows.append({'path':str(out),'sha256':hashlib.sha256(out.read_bytes()).hexdigest(),'mode':mode})
 print(json.dumps({'source':str(a.source),'source_sha256':hashlib.sha256(data).hexdigest(),'outputs':rows},indent=2))
if __name__=='__main__':main()
