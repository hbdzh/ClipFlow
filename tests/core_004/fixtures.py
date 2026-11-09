"""Self-authored frame-code/color/audio fixtures, never user media."""
from pathlib import Path
import shutil
from support import sha256

FONT='/System/Library/Fonts/Menlo.ttc'

def make_fixtures(directory,commands):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    ffmpeg=shutil.which('ffmpeg');ffprobe=shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:raise RuntimeError('BLOCKED: FFmpeg/ffprobe missing')
    common=[ffmpeg,'-nostdin','-hide_banner','-v','error','-y']
    encoding=['-c:v','libx264','-threads:v','1','-crf','14','-preset','veryfast','-g','24','-keyint_min','24','-sc_threshold','0','-bf','2','-pix_fmt','yuv420p']
    tagged=['-color_primaries','bt709','-color_trc','bt709','-colorspace','bt709','-color_range','tv']
    specifications=[
        ('landscape',1920,1080,'24',1,48000,False,True),
        ('portrait',1080,1920,'24',1,48000,False,True),
        ('round-landscape',1920,1078,'24',1,48000,False,True),
        ('round-portrait',1078,1920,'24',1,48000,False,True),
        ('small',320,180,'30',1,48000,False,True),
        ('small-portrait',180,320,'25',0,48000,False,True),
        ('multi',480,270,'25',2,48000,True,True),
        ('noaudio',320,180,'30',0,48000,False,True),
        ('unlabelled',320,180,'30',1,48000,False,False),
        ('fraction',320,180,'30000/1001',1,44100,False,True)]
    result={}
    for name,width,height,rate,audio_count,sample_rate,offset,color in specifications:
        large=min(width,height)>=720;cw=480 if large else 160;ch=128 if large else 48;font=92 if large else 32
        counter=f'drawbox=x=0:y=0:w={cw}:h={ch}:color=black:t=fill,drawtext=fontfile={FONT}:text=%{{n}}:x=8:y=4:fontsize={font}:fontcolor=white'
        filters=counter+(',setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709' if color else '')
        args=['-f','lavfi','-i',f'testsrc2=size={width}x{height}:rate={rate}:duration=2']
        for index in range(audio_count):
            # Pulsed tones differentiate track mapping and time offset, with no user voice/music.
            args+=['-f','lavfi','-i',f'aevalsrc=0.22*sin(2*PI*{431+index*613}*t)*(0.35+0.65*gt(mod(t\\,0.4)\\,0.1)):s={sample_rate}:d=2']
        args+=['-map','0:v:0']
        for index in range(audio_count):args+=['-map',f'{index+1}:a:0']
        args+=['-vf',filters,*encoding]
        if color:args+=tagged
        if audio_count:
            args+=['-c:a','aac','-b:a','192k','-threads:a','1']
            for index in range(audio_count):
                args += [f'-metadata:s:a:{index}',f'language={"eng" if index==0 else "zho"}',f'-ac:a:{index}',str(1 if index==0 else 2)]
        if offset:args+=['-output_ts_offset','5']
        path=directory/(name+'.mp4');commands.run(common+args+[path],timeout=120)
        result[name]={'path':str(path),'sha256':sha256(path),'width':width,'height':height,'fps':rate,'audio_count':audio_count,'sample_rate':sample_rate,'offset_seconds':5 if offset else 0,'tagged_709':color,'counter_width':cw,'counter_height':ch}
    special=directory/"Unicode 空格 ; $() '引号'.mp4";special.write_bytes(Path(result['multi']['path']).read_bytes());result['unicode']={**result['multi'],'path':str(special),'sha256':sha256(special)}
    bad=directory/'broken.mp4';bad.write_bytes(b'CORE004 independent broken file\x00\x00');result['broken']={'path':str(bad),'sha256':sha256(bad)}
    rotated=directory/'rotated.mp4';commands.run(common+['-display_rotation:v:0','90','-noautorotate','-i',result['small']['path'],'-map','0','-c','copy',rotated]);result['rotated']={'path':str(rotated),'sha256':sha256(rotated)}
    audio_first=directory/'audio-first.mp4';commands.run(common+['-i',result['multi']['path'],'-map','0:a:0','-map','0:v:0','-map','0:a:1','-c','copy',audio_first]);result['audio-first']={**result['multi'],'path':str(audio_first),'sha256':sha256(audio_first)}
    result.update(add_gap_fixtures(directory,commands))
    return result

def add_gap_fixtures(directory,commands):
    directory=Path(directory);ffmpeg=shutil.which('ffmpeg');common=[ffmpeg,'-nostdin','-hide_banner','-v','error','-y']
    video=directory/'gap-video.mp4';counter=f'drawbox=x=0:y=0:w=160:h=48:color=black:t=fill,drawtext=fontfile={FONT}:text=%{{n}}:x=8:y=4:fontsize=32:fontcolor=white'
    commands.run(common+['-f','lavfi','-i','testsrc2=size=320x180:rate=30:duration=4','-vf',counter+',setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709','-c:v','libx264','-threads:v','1','-crf','14','-preset','veryfast','-g','30','-bf','2','-pix_fmt','yuv420p','-color_primaries','bt709','-color_trc','bt709','-colorspace','bt709','-color_range','tv',video])
    results={}
    for name,kind in [('late-only','late'),('dual-gaps','dual'),('late-surround','surround')]:
        args=['-i',video]
        if kind=='dual':args+=['-f','lavfi','-i','aevalsrc=0.3*sin(2*PI*431*t)*(0.35+0.65*gt(mod(t\\,0.4)\\,0.1)):s=48000:d=1']
        args+=['-itsoffset','2','-f','lavfi','-i',('anullsrc=channel_layout=5.1:sample_rate=48000' if kind=='surround' else 'aevalsrc=0.3*sin(2*PI*1044*t)*(0.35+0.65*gt(mod(t\\,0.4)\\,0.1)):s=48000:d=1')]
        args+=['-map','0:v:0']
        if kind=='dual':args+=['-map','1:a:0','-map','2:a:0']
        else:args+=['-map','1:a:0']
        args+=['-c:v','copy','-c:a','aac','-threads:a','1','-b:a','192k','-metadata:s:a:0','language=eng']
        if kind=='dual':args+=['-metadata:s:a:1','language=zho','-ac:a:0','1','-ac:a:1','2']
        if kind=='surround':args+=['-t','4']
        path=directory/(name+'.mp4');commands.run(common+args+[path])
        results[name]={'path':str(path),'sha256':sha256(path),'width':320,'height':180,'fps':'30','audio_count':2 if kind=='dual' else 1,'sample_rate':48000,'offset_seconds':0,'tagged_709':True,'counter_width':160,'counter_height':48}
    return results
