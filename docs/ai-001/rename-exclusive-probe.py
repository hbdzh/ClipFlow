import ctypes, os, tempfile, threading, json, errno, hashlib, platform
lib=ctypes.CDLL(None, use_errno=True)
fn=lib.renameatx_np
fn.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint]
fn.restype=ctypes.c_int
F=4
result={'platform':platform.platform(),'rename_flag':'RENAME_EXCL 0x4 from local macOS SDK sys/stdio.h','test_scope':'single private temporary directory, same parent fd; no crash/durability/network/sandbox tests','cases':{}}
with tempfile.TemporaryDirectory(prefix='ai001-rename-exclusive-') as root:
 d=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
 def make(name,val):
  os.mkdir(name,dir_fd=d,mode=0o700)
  f=os.open(name,os.O_RDONLY|os.O_DIRECTORY,dir_fd=d)
  for file in ('vocals.wav','accompaniment.wav'):
   fd=os.open(file,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600,dir_fd=f);os.write(fd,(val+file).encode());os.close(fd)
  os.close(f)
 def ren(a,b):
  ctypes.set_errno(0); rc=fn(d,a.encode(),d,b.encode(),F); return rc,ctypes.get_errno()
 def read(name,file):
  fd=os.open(name+'/'+file,os.O_RDONLY,dir_fd=d); x=os.read(fd,100);os.close(fd);return x.decode()
 make('source','S'); ok=ren('source','target'); result['cases']['absent_target']={'rc_errno':ok,'source_absent':not os.path.exists(root+'/source'),'target_names':sorted(os.listdir(root+'/target')),'contents_valid':all(read('target',x)=='S'+x for x in ('vocals.wav','accompaniment.wav'))}
 make('existing_src','D'); os.mkdir('existing_dest',dir_fd=d); fail=ren('existing_src','existing_dest');result['cases']['existing_empty_directory']={'rc_errno':fail,'eexist':fail==(-1,errno.EEXIST),'source_preserved':os.path.exists(root+'/existing_src/vocals.wav'),'destination_untouched':os.listdir(root+'/existing_dest')==[]}
 make('file_src','F'); fd=os.open('existing_file',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600,dir_fd=d);os.write(fd,b'protected');os.close(fd); fail=ren('file_src','existing_file'); result['cases']['existing_file']={'rc_errno':fail,'no_clobber':open(root+'/existing_file','rb').read()==b'protected','source_preserved':os.path.exists(root+'/file_src')}
 make('link_src','L');os.symlink('nonexistent','link_dest',dir_fd=d);fail=ren('link_src','link_dest');result['cases']['dangling_symlink_target']={'rc_errno':fail,'eexist':fail==(-1,errno.EEXIST),'link_preserved':os.path.islink(root+'/link_dest'),'source_preserved':os.path.exists(root+'/link_src')}
 races=[]
 for i in range(100):
  a='a'+str(i);b='b'+str(i);t='race'+str(i);make(a,'A');make(b,'B');bar=threading.Barrier(3);out=[]
  def run(src):
   bar.wait();out.append((src,ren(src,t)))
  ts=[threading.Thread(target=run,args=(q,)) for q in (a,b)]
  for th in ts: th.start()
  bar.wait()
  for th in ts: th.join()
  winners=[q for q,v in out if v==(0,0)];losers=[q for q,v in out if v==(-1,errno.EEXIST)]
  valid=len(winners)==1 and len(losers)==1 and os.path.exists(root+'/'+losers[0]) and read(t,'vocals.wav') in ('Avocals.wav','Bvocals.wav') and read(t,'accompaniment.wav')==read(t,'vocals.wav')[0]+'accompaniment.wav'
  races.append(valid)
 result['cases']['concurrent_100']={'passed':sum(races),'iterations':len(races),'all_pass':all(races)}
 os.close(d)
print(json.dumps(result,indent=2))
