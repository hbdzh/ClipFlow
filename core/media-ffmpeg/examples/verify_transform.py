#!/usr/bin/env python3
"""Generate controlled local fixture and independently inspect transform output pixels.

Usage: python3 -B verify_transform.py /absolute/path/to/transform_smoke
This checks a small CFR H.264/AAC case, not the complete media matrix.
"""
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import tempfile

if len(sys.argv) != 2 or not Path(sys.argv[1]).is_absolute():
    raise SystemExit('pass absolute path to built transform_smoke')
exe = sys.argv[1]
ffmpeg = '/opt/homebrew/bin/ffmpeg'
ffprobe = '/opt/homebrew/bin/ffprobe'
root = Path(tempfile.mkdtemp(prefix='mediastudio-transform-check-'))
source = root / 'source.mp4'
subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
    'color=black:s=320x180:r=30:d=2,drawbox=x=0:y=0:w=160:h=90:color=red:t=fill,drawbox=x=160:y=0:w=160:h=90:color=green:t=fill,drawbox=x=0:y=90:w=160:h=90:color=blue:t=fill,drawbox=x=160:y=90:w=160:h=90:color=yellow:t=fill',
    '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=2',
    '-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(source)],check=True)
source_hash = hashlib.sha256(source.read_bytes()).hexdigest()

def run(name, rotation, scale, x, y):
    out=root/(name+'.mp4')
    cmd=[exe,str(source),str(out),str(rotation),str(scale),str(x),str(y)]
    completed=subprocess.run(cmd, text=True, capture_output=True, check=True)
    result=json.loads(completed.stdout)
    probe=json.loads(subprocess.check_output([ffprobe,'-v','error','-show_streams','-show_format','-of','json',str(out)]))
    video=next(s for s in probe['streams'] if s['codec_type']=='video')
    assert video['sample_aspect_ratio']=='1:1' and float(probe['format']['duration'])==2.0
    assert any(s['codec_type']=='audio' for s in probe['streams'])
    subprocess.run([ffmpeg,'-v','error','-xerror','-i',str(out),'-f','null','-'],check=True)
    data=subprocess.check_output([ffmpeg,'-v','error','-i',str(out),'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','-'])
    w,h=video['width'],video['height']
    def pixel(x,y):
        i=(y*w+x)*3
        return tuple(data[i:i+3])
    return out,result,(w,h),pixel

def is_red(c): return c[0]>180 and c[1]<40 and c[2]<40
def is_green(c): return c[1]>85 and c[0]<40 and c[2]<40
def is_blue(c): return c[2]>180 and c[0]<40 and c[1]<40
def is_yellow(c): return c[0]>180 and c[1]>180 and c[2]<40
def is_black(c): return max(c)<20

rot, r, size, p=run('rotation',90,100,0,0)
assert size==(180,320) and is_blue(p(45,80)) and is_red(p(135,80)) and is_yellow(p(45,240)) and is_green(p(135,240))
assert r['transcode_plan']['video_transform']=={'rotation_degrees':90,'scale_percent':100,'offset_x':0,'offset_y':0}
moved, m, size, p=run('moved',0,100,20,-10)
assert size==(320,180) and is_black(p(8,40)) and is_red(p(40,40)) and is_black(p(40,175)) and is_green(p(280,40))
assert m['transcode_plan']['visible_content_rect']=={'x':20,'y':0,'width':300,'height':170}
zoom, z, size, p=run('zoom',270,150,20,-10)
assert size==(180,320) and z['transcode_plan']['video_transform']['scale_percent']==150
saved=hashlib.sha256(moved.read_bytes()).hexdigest()
rejected=subprocess.run([exe,str(source),str(moved),'0','100','20','-10'],capture_output=True,text=True)
assert rejected.returncode != 0 and 'OUTPUT_EXISTS' in rejected.stderr and hashlib.sha256(moved.read_bytes()).hexdigest()==saved
bad=root/'odd.mp4'
rejected=subprocess.run([exe,str(source),str(bad),'0','100','1','0'],capture_output=True,text=True)
assert rejected.returncode != 0 and not bad.exists()
assert hashlib.sha256(source.read_bytes()).hexdigest()==source_hash
print(json.dumps({'result':'PASS','directory':str(root),'source_sha256':source_hash,'checked':['rotation pixels','translation and black edges','zoom result','output probe','complete decode','no replacement','invalid offset']},ensure_ascii=False,indent=2))
