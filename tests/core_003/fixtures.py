"""Self-authored, deterministic CORE-003 media fixtures; no user material."""
from pathlib import Path
import shutil

FONT = '/System/Library/Fonts/Menlo.ttc'


def make_fixtures(directory, commands):
    directory = Path(directory)
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('BLOCKED: FFmpeg absent')
    prefix = [ffmpeg, '-nostdin', '-hide_banner', '-v', 'error', '-y']
    common = ['-c:v', 'libx264', '-threads:v', '1', '-crf', '16', '-g', '30',
              '-keyint_min', '30', '-sc_threshold', '0', '-bf', '2', '-pix_fmt', 'yuv420p']
    color = ['-color_primaries','bt709','-colorspace','bt709','-color_trc','bt709','-color_range','tv']
    counter = f'drawbox=x=0:y=0:w=160:h=48:color=black:t=fill,drawtext=fontfile={FONT}:text=%{{n}}:x=8:y=4:fontsize=32:fontcolor=white'
    files = {}
    for name, rate, audio_count, offset, size in [
        ('cfr','30',1,False,'320x180'), ('stereo','30',1,False,'320x180'), ('audio44100','30',1,False,'320x180'), ('multi','30',2,False,'320x180'),
        ('noaudio','30',0,False,'320x180'), ('offset','30',1,True,'320x180'),
        ('fps24','24',1,False,'320x180'), ('fps25','25',1,False,'320x180'),
        ('fps60','60',1,False,'320x180'), ('fps23976','24000/1001',1,False,'320x180'),
        ('fps2997','30000/1001',1,False,'320x180'),
        ('quality1080','30',1,False,'1920x1080')]:
        inputs = ['-f','lavfi','-i',f'testsrc2=size={size}:rate={rate}:duration=4']
        for i in range(audio_count):
            sample_rate=44100 if name=='audio44100' else 48000
            inputs += ['-f','lavfi','-i',f'anoisesrc=color=pink:amplitude=0.4:sample_rate={sample_rate}:duration=4:seed={1234+i}']
        maps=['-map','0:v:0']
        for i in range(audio_count):maps += ['-map',f'{i+1}:a:0']
        extras=['-vf',counter+',setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709', *color]
        if audio_count:
            extras += ['-c:a','aac','-threads:a','1','-b:a','192k']
            for i in range(audio_count):extras += [f'-metadata:s:a:{i}',f'language={"eng" if i==0 else "zho"}']
        if name=='stereo':extras += ['-ac','2']
        if offset:extras += ['-output_ts_offset','5']
        path=directory/(name+'.mp4')
        commands.run(prefix+inputs+maps+common+extras+[path], timeout=60)
        files[name]=path
    plain=directory/'unlabelled.mp4'
    commands.run(prefix+['-f','lavfi','-i','testsrc2=size=320x180:rate=30:duration=4',
                 '-vf',counter,*common,plain])
    files['unlabelled']=plain
    special=directory/"Unicode 空格 ; $() '引号'.mp4"
    special.write_bytes(files['cfr'].read_bytes());files['unicode']=special
    bad=directory/'truncated.mp4';bad.write_bytes(files['cfr'].read_bytes()[:32]);files['bad']=bad
    for name, vf, extra in [('vfr',"select=not(mod(n\\,2))+not(mod(n\\,5))",['-fps_mode','vfr']),
                             ('sar','setsar=2',[]),
                             ('hdr','setparams=range=limited:color_primaries=bt2020:color_trc=smpte2084:colorspace=bt2020nc',['-color_primaries','bt2020','-color_trc','smpte2084','-colorspace','bt2020nc'])]:
        path=directory/(name+'.mp4')
        commands.run(prefix+['-f','lavfi','-i','testsrc2=size=320x180:rate=30:duration=4',
                     '-vf',vf,*common,*extra,path]);files[name]=path
    rotation=directory/'rotation.mp4'
    commands.run(prefix+['-display_rotation:v:0','90','-noautorotate','-i',files['cfr'],'-map','0','-c','copy',rotation]);files['rotation']=rotation
    subtitle=directory/'text.srt';subtitle.write_text('1\n00:00:00,000 --> 00:00:01,000\nSelf-authored QA subtitle\n')
    subvideo=directory/'subtitle.mp4'
    commands.run(prefix+['-i',files['cfr'],'-i',subtitle,'-map','0','-map','1:0','-c','copy','-c:s','mov_text',subvideo]);files['subtitle']=subvideo
    # Explicitly foreign codec; negative fixture only, no release dependency added.
    hevc=directory/'hevc.mp4'
    commands.run(prefix+['-f','lavfi','-i','testsrc2=size=160x96:rate=30:duration=4',
                 '-c:v','libx265','-threads','1','-x265-params','pools=none:frame-threads=1:log-level=error',hevc]);files['hevc']=hevc
    multi_video=directory/'multi-video.mp4'
    commands.run(prefix+['-i',files['cfr'],'-map','0:v:0','-map','0:v:0','-c','copy',multi_video]);files['multi_video']=multi_video
    surround=directory/'surround.mp4'
    commands.run(prefix+['-i',files['noaudio'],'-f','lavfi','-i','anullsrc=channel_layout=5.1:sample_rate=48000',
        '-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-t','4',surround]);files['surround']=surround
    alpha=directory/'alpha.mov'
    commands.run(prefix+['-f','lavfi','-i','color=c=red@0.5:size=160x96:rate=30:duration=4,format=rgba',
        '-c:v','prores_ks','-threads','1','-profile:v','4','-pix_fmt','yuva444p10le',alpha]);files['alpha']=alpha
    audio_first=directory/'audio-first.mp4'
    commands.run(prefix+['-i',files['cfr'],'-map','0:a:0','-map','0:v:0','-c','copy',audio_first]);files['audio_first']=audio_first
    for name,delay,length in [('short_audio',0,2),('delayed_audio',1.5,2.5)]:
        path=directory/(name+'.mp4')
        commands.run(prefix+['-i',files['noaudio'],'-itsoffset',str(delay),'-f','lavfi','-i',
            f'anoisesrc=color=pink:amplitude=0.4:sample_rate=48000:duration={length}:seed=1234',
            '-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-b:a','192k',
            '-metadata:s:a:0','language=eng',path]);files[name]=path
    return files
