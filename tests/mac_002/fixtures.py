"""Self-authored media for source identity, framing and UI model acceptance."""
from pathlib import Path
import shutil

FONT='/System/Library/Fonts/Menlo.ttc'


def make_fixtures(directory,commands):
    directory=Path(directory);directory.mkdir(exist_ok=True,parents=True)
    ffmpeg=shutil.which('ffmpeg');assert ffmpeg,'FFmpeg required'
    prefix=[ffmpeg,'-nostdin','-hide_banner','-v','error','-n']
    common=['-c:v','libx264','-threads:v','1','-crf','16','-g','30','-keyint_min','30','-sc_threshold','0','-bf','2','-pix_fmt','yuv420p']
    tags=['-color_range','tv','-colorspace','bt709','-color_primaries','bt709','-color_trc','bt709']
    counter=f'drawbox=x=0:y=0:w=160:h=48:color=black:t=fill,drawtext=fontfile={FONT}:text=%{{n}}:x=8:y=4:fontsize=32:fontcolor=white'
    color='setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709'
    files={}
    for name,size,rate,tracks,offset in [('a','320x180','30',1,0),('b','480x270','25',2,5),('noaudio','320x180','30',0,0),('quality1080','1920x1080','30',1,0)]:
        inputs=['-f','lavfi','-i',f'testsrc2=size={size}:rate={rate}:duration=4']
        for i in range(tracks):inputs+=['-f','lavfi','-i',f'anoisesrc=color=pink:amplitude=0.4:sample_rate=48000:duration=4:seed={1234+i}']
        maps=['-map','0:v:0']
        for i in range(tracks):maps+=['-map',f'{i+1}:a:0']
        extra=['-vf',('hue=h=75,' if name=='b' else '')+counter+','+color,*tags]
        if tracks:
            extra+=['-c:a','aac','-threads:a','1','-b:a','192k']
            for i in range(tracks):extra+=[f'-metadata:s:a:{i}',f'language={"eng" if i==0 else "zho"}']
        if offset:extra+=['-output_ts_offset',str(offset)]
        p=directory/(name+'.mp4');commands.run(prefix+inputs+maps+common+extra+[p],timeout=90);files[name]=p
    plain=directory/'legacy-unlabelled.mp4'
    commands.run(prefix+['-f','lavfi','-i','testsrc2=size=320x180:rate=30:duration=4','-vf',counter,*common,plain]);files['legacy']=plain
    special=directory/"Unicode 空格 ; $() '引号'.mp4";special.write_bytes(files['a'].read_bytes());files['unicode']=special
    bad=directory/'broken.mp4';bad.write_bytes(files['a'].read_bytes()[:32]);files['bad']=bad
    rotation=directory/'rotated.mp4';commands.run(prefix+['-display_rotation:v:0','90','-noautorotate','-i',files['a'],'-map','0','-c','copy',rotation]);files['rotation']=rotation
    return files
