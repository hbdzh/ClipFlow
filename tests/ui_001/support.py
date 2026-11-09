"""Independent native UI-001 test helpers; no implementation imports."""
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_report(path, data):
    path = Path(path)
    if not path.is_absolute():
        path = ROOT / path
    if HERE not in path.resolve().parents or '..' in path.relative_to(HERE).parts:
        raise ValueError('QA evidence must stay within tests/ui_001')
    current = HERE
    for part in path.relative_to(HERE).parts:
        current /= part
        if current.is_symlink():
            raise ValueError('No redirected evidence path')
        if current.exists() and current.is_file() and current.stat().st_nlink != 1:
            raise ValueError('No hardlinked evidence output')
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, prefix='.ui001-', delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class Commands:
    def __init__(self):
        self.records = []

    def run(self, argv, expected=0, timeout=90, env=None):
        argv = list(map(str, argv))
        start = time.monotonic()
        try:
            p = subprocess.run(argv, cwd=ROOT, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, timeout=timeout, env=env)
        except subprocess.TimeoutExpired as error:
            self.records.append({'argv': argv, 'returncode': None, 'timeout_seconds': timeout,
                                 'stdout': error.stdout.decode(errors='replace') if isinstance(error.stdout, bytes) else error.stdout,
                                 'stderr': error.stderr.decode(errors='replace') if isinstance(error.stderr, bytes) else error.stderr})
            raise
        self.records.append({'argv': argv, 'returncode': p.returncode,
                             'elapsed_seconds': round(time.monotonic()-start, 6),
                             'stdout': p.stdout, 'stderr': p.stderr})
        if expected is not None and p.returncode != expected:
            raise AssertionError((argv, p.returncode, p.stdout, p.stderr))
        return p


    def run_bytes(self, argv, timeout=30):
        argv=list(map(str,argv))
        start=time.monotonic()
        p=subprocess.run(argv,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE,timeout=timeout)
        self.records.append({'argv':argv,'returncode':p.returncode,
                             'elapsed_seconds':round(time.monotonic()-start,6),
                             'stdout_bytes':len(p.stdout),
                             'stdout_sha256':hashlib.sha256(p.stdout).hexdigest(),
                             'stderr':p.stderr.decode(errors='replace')})
        if p.returncode:raise AssertionError((argv,p.returncode,p.stderr))
        return p.stdout


def source_hashes():
    files = [p for base in ['apps/macos/Sources', 'core', 'contracts'] for p in (ROOT/base).rglob('*')
             if p.is_file() and 'target' not in p.parts and p.suffix in {'.swift', '.rs', '.toml', '.lock', '.h', '.json'}]
    return {str(p.relative_to(ROOT)): sha256(p) for p in sorted(files)}


def make_fixtures(directory, commands):
    directory = Path(directory)
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise AssertionError('BLOCKED: ffmpeg not found')
    prefix = [ffmpeg, '-nostdin', '-hide_banner', '-v', 'error', '-n']
    video = ['-f', 'lavfi', '-i', 'testsrc2=size=160x96:rate=30:duration=4']
    audio = ['-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=4']
    codec = ['-c:v', 'libx264', '-threads:v', '1', '-g', '30', '-keyint_min', '30',
             '-sc_threshold', '0', '-bf', '2', '-pix_fmt', 'yuv420p', '-video_track_timescale', '30000']
    files = {}
    for name, inputs, maps, extras in [
        ('cfr', video+audio, ['-map','0:v:0','-map','1:a:0'], ['-c:a','aac','-threads:a','1']),
        ('offset', video+audio, ['-map','0:v:0','-map','1:a:0'], ['-c:a','aac','-threads:a','1','-output_ts_offset','5']),
        ('noaudio', video, ['-map','0:v:0'], []),
        ('multi', video+audio+['-f','lavfi','-i','sine=frequency=880:sample_rate=48000:duration=4'],
         ['-map','0:v:0','-map','1:a:0','-map','2:a:0'], ['-c:a','aac','-threads:a','1']),
    ]:
        path = directory/(name+'.mp4')
        commands.run(prefix+inputs+maps+codec+extras+[path])
        files[name] = path
    special = directory/"Unicode 空格 ; $() [片段] '引号'.mp4"
    special.write_bytes(files['cfr'].read_bytes())
    files['unicode'] = special
    bad = directory/'truncated.mp4'; bad.write_bytes(files['cfr'].read_bytes()[:32]); files['bad'] = bad
    return files


class MediaVerifier:
    def __init__(self, commands):
        self.commands = commands

    def metadata(self, path):
        return json.loads(self.commands.run([shutil.which('ffprobe'), '-v','error','-show_format','-show_streams','-of','json',path]).stdout)

    def frames(self, path):
        return json.loads(self.commands.run([shutil.which('ffprobe'),'-v','error','-select_streams','v:0','-show_frames',
            '-show_entries','frame=best_effort_timestamp,duration,key_frame','-of','json',path]).stdout)['frames']

    def hashes(self, path):
        output = self.commands.run([shutil.which('ffmpeg'),'-hide_banner','-nostdin','-v','error','-xerror',
            '-threads','1','-copyts','-i',path,'-map','0:v:0','-fps_mode','passthrough','-f','framehash','-hash','sha256','-']).stdout
        return [line.rsplit(',',1)[1].strip() for line in output.splitlines() if line and not line.startswith('#')]

    def packets(self, path):
        return json.loads(self.commands.run([shutil.which('ffprobe'),'-v','error','-show_packets','-show_data_hash','sha256',
            '-show_entries','packet=stream_index,pts,dts,duration,data_hash','-of','json',path]).stdout)['packets']

    def preview_metrics(self, source, png):
        # This is a temporal/content check, never colour-fidelity acceptance.
        ffmpeg=shutil.which('ffmpeg')
        actual=self.commands.run_bytes([ffmpeg,'-nostdin','-hide_banner','-v','error',
            '-i',png,'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','-'])
        frames=self.commands.run_bytes([ffmpeg,'-nostdin','-hide_banner','-v','error',
            '-i',source,'-map','0:v:0','-fps_mode','passthrough','-f','rawvideo','-pix_fmt','rgb24','-'])
        frame_size=160*96*3
        assert len(actual)==frame_size and len(frames)==120*frame_size
        source_frames=[frames[n:n+frame_size] for n in range(0,len(frames),frame_size)]
        mse=lambda a,b:sum((x-y)**2 for x,y in zip(a,b))/len(a)
        rgb_scores=[mse(actual,frame) for frame in source_frames]
        def luma(rgb):
            return [0.2126*rgb[n]+0.7152*rgb[n+1]+0.0722*rgb[n+2] for n in range(0,len(rgb),3)]
        actual_luma=luma(actual)
        target_luma_mse=mse(actual_luma,luma(source_frames[33]))
        first_luma_mse=mse(actual_luma,luma(source_frames[0]))
        return {'png_sha256':sha256(png),'expected_source_frame_index':33,
                'expected_rgb_sha256':hashlib.sha256(source_frames[33]).hexdigest(),
                'actual_rgb_sha256':hashlib.sha256(actual).hexdigest(),
                'target_rgb_mean_squared_error':rgb_scores[33],
                'first_frame_rgb_mse':rgb_scores[0],
                'rgb_nearest_source_index':min(range(120),key=lambda n:rgb_scores[n]),
                'rgb_nearest_3':sorted(enumerate(rgb_scores),key=lambda pair:pair[1])[:3],
                'target_luma_mean_squared_error':target_luma_mse,
                'first_frame_luma_mse':first_luma_mse,
                'fixed_maximum_luma_mse':144,
                'luma_weights':'BT.709 0.2126R + 0.7152G + 0.0722B, full resolution, no blur',
                'colour_fidelity_claim':False}

    def verify_preview_frame(self, source, png):
        result=self.preview_metrics(source,png)
        assert result['rgb_nearest_source_index']==33, ('Preview must be nearest to requested source frame',result)
        assert result['target_luma_mean_squared_error']<=144, ('Preview content luma exceeds fixed limit',result)
        assert result['target_luma_mean_squared_error']<result['first_frame_luma_mse']/4, ('Preview must match requested frame substantially better than first frame',result)
        return result

    def verify(self, source, output, result):
        before = self.metadata(source); after = self.metadata(output)
        assert [(s['codec_type'],s['codec_name']) for s in before['streams']] == [(s['codec_type'],s['codec_name']) for s in after['streams']]
        self.commands.run([shutil.which('ffmpeg'),'-hide_banner','-nostdin','-v','error','-xerror','-i',output,'-map','0','-f','null','-'])
        old = self.hashes(source); new = self.hashes(output)
        assert new == old[30:90], 'UI output must preserve complete source-frame interval 30..89'
        assert result['requested_range_us'] == {'start_us':1100000,'end_us':2600000}
        assert result['actual_range_us'] == {'start_us':1000000,'end_us':3000000}
        source_frames = self.frames(source)
        video = next(s for s in before['streams'] if s['codec_type']=='video')
        origin = Fraction(video['time_base'])*int(source_frames[0]['best_effort_timestamp'])
        assert result['actual_source_range_us'] == {'start_us':int((origin+1)*1000000),'end_us':int((origin+3)*1000000)}
        source_packets = self.packets(source); output_packets = self.packets(output)
        shifts = []
        for s, t in zip(before['streams'], after['streams']):
            original = [p for p in source_packets if p['stream_index']==s['index']]
            exported = [p for p in output_packets if p['stream_index']==t['index']]
            assert exported
            assert all(int(a['dts']) < int(b['dts']) for a,b in zip(exported,exported[1:]))
            hashes = [p['data_hash'] for p in exported]
            offsets = [n for n in range(len(original)-len(exported)+1) if [p['data_hash'] for p in original[n:n+len(exported)]] == hashes]
            assert offsets, 'Output packets must be a contiguous source subsequence'
            source_tb, output_tb = Fraction(s['time_base']), Fraction(t['time_base'])
            candidates=[]
            for n in offsets:
                values=[Fraction(int(new[field]))*output_tb-Fraction(int(old[field]))*source_tb
                        for old,new in zip(original[n:n+len(exported)],exported) for field in ['pts','dts']]
                if max(values)-min(values) <= Fraction(2,1000): candidates.append(values)
            assert candidates, 'Output packet timestamps must preserve one common translation'
            shifts.append(candidates)
        reference = shifts[0][0][0]
        assert all(any(all(abs(v-reference) <= Fraction(2,1000) for v in candidate) for candidate in options) for options in shifts)
        ends=[]
        for s in after['streams']:
            packets=[p for p in output_packets if p['stream_index']==s['index']]
            ends.append(max(Fraction(int(p['pts'])+int(p['duration']))*Fraction(s['time_base']) for p in packets))
        assert max(ends)-min(ends) <= Fraction(150,1000)
        return {'source_sha256':sha256(source),'output_sha256':sha256(output),
                'source_metadata':before,'output_metadata':after,'frame_indices':list(range(30,90)),
                'contiguous_packet_subsequences':True,'shared_timestamp_shift_tolerance_us':2000,
                'audio_video_end_difference_us':int((max(ends)-min(ends))*1000000),
                'source_video_origin_us':int(origin*1000000),'source_packet_count':len(source_packets),'output_packet_count':len(output_packets)}


def export_hang_tool(directory):
    directory = Path(directory); directory.mkdir()
    marker = directory/'owned.pid'
    shim = directory/'qa-ffmpeg'
    real = shutil.which('ffmpeg')
    shim.write_text('#!'+sys.executable+'\nimport os,sys,time\n'+
        'if "-c" in sys.argv and sys.argv[sys.argv.index("-c")+1]=="copy":\n'+
        ' with open('+repr(str(marker))+',"w") as f:f.write(str(os.getpid()))\n'+
        ' with open(sys.argv[-1],"wb") as f:f.write(b"QA owned partial export");f.flush();os.fsync(f.fileno())\n'+
        ' time.sleep(30)\n'+
        ' sys.exit(1)\n'+
        'os.execv('+repr(real)+',['+repr(real)+']+sys.argv[1:])\n')
    shim.chmod(0o700)
    return shim, marker
