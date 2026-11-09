"""Explicitly injected failures; successful media always comes from real FFmpeg."""
from pathlib import Path
import shutil
import sys


def missing_encoders(directory):
    directory=Path(directory);directory.mkdir()
    real=shutil.which('ffmpeg');shim=directory/'ffmpeg-no-fixed-encoders'
    shim.write_text('#!'+sys.executable+'\nimport os,sys,subprocess\n'+
        'real='+repr(real)+'\n'+
        'if "-encoders" in sys.argv:\n'+
        ' p=subprocess.run([real]+sys.argv[1:],stdout=subprocess.PIPE,stderr=subprocess.PIPE)\n'+
        ' sys.stdout.buffer.write(b"\\n".join(line for line in p.stdout.splitlines() if b"libx264" not in line and not (len(line.split())>1 and line.split()[1]==b"aac"))+b"\\n")\n'+
        ' sys.stderr.buffer.write(p.stderr);sys.exit(p.returncode)\n'+
        'os.execv(real,[real]+sys.argv[1:])\n')
    shim.chmod(0o700);return shim


def observed_export_hang(directory):
    directory=Path(directory);directory.mkdir()
    real=shutil.which('ffmpeg');shim=directory/'ffmpeg-hang-after-real-output';marker=directory/'owned-export.json'
    shim.write_text('#!'+sys.executable+'\nimport os,sys,subprocess,json,time\n'+
        'real='+repr(real)+'\n'+
        'args=sys.argv[1:]\n'+
        'is_export=("-c" in args and args[args.index("-c")+1]=="copy") or ("-c:v" in args and args[args.index("-c:v")+1]=="libx264")\n'+
        'if is_export:\n'+
        ' p=subprocess.run([real]+args)\n'+
        ' if p.returncode:sys.exit(p.returncode)\n'+
        ' target=args[-1]\n'+
        ' with open('+repr(str(marker))+',"x") as f:json.dump({"pid":os.getpid(),"staging":target,"bytes":os.stat(target).st_size,"real_ffmpeg_exit":p.returncode},f);f.flush();os.fsync(f.fileno())\n'+
        ' time.sleep(90);sys.exit(1)\n'+
        'os.execv(real,[real]+args)\n')
    shim.chmod(0o700);return shim,marker
