"""Test-only wrappers forward real tools; never manufacture successful media."""
from pathlib import Path
import os
import shutil
import sys


def holding_encoder(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=False)
    wrapper=directory/'ffmpeg-hold';events=directory/'events.jsonl';release=directory/'release'
    script='''#!PYTHON
import hashlib,json,os,pathlib,signal,subprocess,sys,time
REAL=REAL_TOOL
EVENTS=pathlib.Path(EVENT_PATH)
RELEASE=pathlib.Path(RELEASE_PATH)
args=sys.argv[1:]
encode=bool(args and args[-1].endswith('output.mp4') and '-c:v' in args)
def record(kind,**values):
 with EVENTS.open('a') as stream:
  stream.write(json.dumps({'kind':kind,'pid':os.getpid(),'monotonic_seconds':time.monotonic(),**values})+'\\n');stream.flush();os.fsync(stream.fileno())
if not encode:sys.exit(subprocess.call([REAL,*args]))
record('encode-start',output=args[-1])
code=subprocess.call([REAL,*args])
if code:sys.exit(code)
path=pathlib.Path(args[-1])
record('staging-ready',output=str(path),bytes=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest())
while not RELEASE.exists():time.sleep(.02)
record('released')
sys.exit(0)
'''.replace('PYTHON',sys.executable).replace('REAL_TOOL',repr(shutil.which('ffmpeg'))).replace('EVENT_PATH',repr(str(events))).replace('RELEASE_PATH',repr(str(release)))
    wrapper.write_text(script);wrapper.chmod(0o700)
    return wrapper,events,release

def event_rows(path):
    import json
    if not Path(path).exists():return []
    rows=[]
    for line in Path(path).read_text().splitlines():
        try:rows.append(json.loads(line))
        except ValueError:pass
    return rows

def wait_event(path,kind,timeout=60,count=1):
    import time
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        rows=[row for row in event_rows(path) if row['kind']==kind]
        if len(rows)>=count:return rows
        time.sleep(.015)
    raise AssertionError(('Real tool event not observed',str(path),kind,count,event_rows(path)))

def pid_alive(pid):
    try:os.kill(pid,0);return True
    except ProcessLookupError:return False

def pause_before_encoder(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=False)
    wrapper=directory/'ffmpeg-before-write';events=directory/'events.jsonl';release=directory/'release'
    script='''#!PYTHON
import json,os,pathlib,subprocess,sys,time
args=sys.argv[1:]
if args and args[-1].endswith('output.mp4') and '-c:v' in args:
 with open(EVENT_PATH,'a') as stream:
  stream.write(json.dumps({'kind':'before-real-encode','pid':os.getpid(),'output':args[-1],'monotonic_seconds':time.monotonic()})+'\\n');stream.flush();os.fsync(stream.fileno())
 while not pathlib.Path(RELEASE_PATH).exists():time.sleep(.02)
sys.exit(subprocess.call([REAL_TOOL,*args]))
'''.replace('PYTHON',sys.executable).replace('EVENT_PATH',repr(str(events))).replace('RELEASE_PATH',repr(str(release))).replace('REAL_TOOL',repr(shutil.which('ffmpeg')))
    wrapper.write_text(script);wrapper.chmod(0o700);return wrapper,events,release
