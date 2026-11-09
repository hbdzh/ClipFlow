"""Small, self-authored real H.264/AAC fixtures and explicit child-process fault tools."""
from pathlib import Path
import shutil
import sys
from support import FFMPEG, FFPROBE, metadata, sha256


def make_fixtures(directory, commands):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if not FFMPEG:
        raise RuntimeError('BLOCKED: ffmpeg missing')
    result = {}
    for name, width, height, tracks in [('multi', 480, 270, 2), ('landscape', 1440, 810, 1),
                                         ('portrait', 810, 1440, 0), ('delay-tiny', 320, 180, 0)]:
        path = directory / (name+'.mp4')
        args = [FFMPEG, '-nostdin', '-hide_banner', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                f'testsrc2=size={width}x{height}:rate=25:duration=2']
        for index in range(tracks):
            args += ['-f', 'lavfi', '-i', f'sine=frequency={431+613*index}:sample_rate=48000:duration=2']
        args += ['-map', '0:v:0']
        for index in range(tracks):
            args += ['-map', f'{index+1}:a:0']
        args += ['-vf', 'setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709',
                 '-c:v', 'libx264', '-threads:v', '1', '-crf', '14', '-preset', 'veryfast',
                 '-g', '25', '-keyint_min', '25', '-sc_threshold', '0', '-bf', '2', '-pix_fmt', 'yuv420p',
                 '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709', '-color_range', 'tv']
        if tracks:
            args += ['-c:a', 'aac', '-threads:a', '1', '-b:a', '192k']
            for index in range(tracks):
                args += [f'-metadata:s:a:{index}', f'language={"eng" if index==0 else "zho"}',
                         f'-ac:a:{index}', str(index+1)]
        commands.run(args+[path])
        result[name] = {'path': str(path), 'sha256': sha256(path), 'width': width, 'height': height,
                        'audio_count': tracks, 'ffprobe': metadata(commands, path)}
    # A global stream index cannot be replaced by an audio ordinal: audio at 0, video at 1, audio at 2.
    path = directory / 'audio-first.mp4'
    commands.run([FFMPEG, '-nostdin', '-v', 'error', '-y', '-i', result['multi']['path'],
                  '-map', '0:a:0', '-map', '0:v:0', '-map', '0:a:1', '-c', 'copy', path])
    result['audio-first'] = {**result['multi'], 'path': str(path), 'sha256': sha256(path),
                             'ffprobe': metadata(commands, path)}
    for name in ['delay-a', 'delay-b', 'delay-c', 'fail-once']:
        path = directory / (name+'.mp4')
        shutil.copyfile(result['multi']['path'], path)
        result[name] = {**result['multi'], 'path': str(path), 'sha256': sha256(path)}
    path = directory / "Unicode 空格 ; $() '引号'.mp4"
    shutil.copyfile(result['multi']['path'], path)
    result['unicode'] = {**result['multi'], 'path': str(path), 'sha256': sha256(path)}
    path = directory / 'broken.mp4'
    path.write_bytes(b'MAC003 independent intentionally corrupt media\x00')
    result['broken'] = {'path': str(path), 'sha256': sha256(path)}
    return result


def observed_tools(directory, delay_seconds=3):
    """Delay actual encoding; fail a chosen actual encoding once. All preflight passes through."""
    directory = Path(directory)
    directory.mkdir()
    shim = directory/'observed-ffmpeg'
    observation = directory/'observations.jsonl'
    failed = directory/'did-fail-once'
    code = '#!'+sys.executable+'\n'
    code += 'import json,os,sys,time\n'
    code += 'real='+repr(FFMPEG)+'\n'
    code += 'args=sys.argv[1:]\n'
    code += 'is_export=("-c:v" in args and args[args.index("-c:v")+1]=="libx264") or ("-c" in args and args[args.index("-c")+1]=="copy")\n'
    code += 'source=args[args.index("-i")+1] if "-i" in args else ""\n'
    code += 'if is_export:\n'
    code += ' with open('+repr(str(observation))+',"a") as f: f.write(json.dumps({"source":source,"target":args[-1],"pid":os.getpid()})+"\\n");f.flush();os.fsync(f.fileno())\n'
    code += ' if "fail-once.mp4" in source and not os.path.exists('+repr(str(failed))+'):\n'
    code += '  open('+repr(str(failed))+',"x").close();sys.stderr.write("MAC003 explicit injected encoder child failure\\n");sys.exit(1)\n'
    code += ' if "delay-" in source:time.sleep('+repr(delay_seconds)+')\n'
    code += 'os.execv(real,[real]+args)\n'
    shim.write_text(code)
    shim.chmod(0o700)
    return shim, observation


def pending_preflight_tools(directory):
    """Observe and briefly gate a real ffprobe after another real encoder arms the gate."""
    directory = Path(directory)
    directory.mkdir()
    armed, marker, release = (directory/name for name in ['armed', 'preflight-observed', 'release'])
    ffmpeg = directory/'pending-ffmpeg'
    ffprobe = directory/'pending-ffprobe'
    ffmpeg.write_text('#!'+sys.executable+'\nimport os,sys,time\nreal='+repr(FFMPEG)+'\nargs=sys.argv[1:]\n'+
        'if "-c:v" in args and args[args.index("-c:v")+1]=="libx264" and any("delay-a.mp4" in x for x in args):\n'+
        ' open('+repr(str(armed))+',"a").write("real encoder armed\\n");time.sleep(5)\n'+
        'os.execv(real,[real]+args)\n')
    ffprobe.write_text('#!'+sys.executable+'\nimport os,sys,time\nreal='+repr(FFPROBE)+'\nargs=sys.argv[1:]\n'+
        'if os.path.exists('+repr(str(armed))+') and any("delay-tiny.mp4" in x for x in args):\n'+
        ' open('+repr(str(marker))+',"a").write("real preflight child reached\\n")\n'+
        ' deadline=time.monotonic()+20\n'+
        ' while not os.path.exists('+repr(str(release))+') and time.monotonic()<deadline:time.sleep(0.01)\n'+
        'os.execv(real,[real]+args)\n')
    ffmpeg.chmod(0o700); ffprobe.chmod(0o700)
    return ffmpeg, ffprobe, armed, marker, release
