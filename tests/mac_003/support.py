"""Independent MAC-003 QA helpers. No production validation code is imported."""
from pathlib import Path
from fractions import Fraction
import hashlib
import json
import platform
import re
import shutil
import subprocess
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FFMPEG = shutil.which('ffmpeg')
FFPROBE = shutil.which('ffprobe')


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


class Commands:
    def __init__(self):
        self.records = []

    def run(self, args, timeout=180, check=True, binary=False):
        args = list(map(str, args))
        started = time.monotonic()
        result = subprocess.run(args, capture_output=True, text=not binary, timeout=timeout)
        def summary(value):
            if isinstance(value, str):
                data = value.encode()
                return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'tail': value[-2000:]}
            return {'bytes': len(value), 'sha256': hashlib.sha256(value).hexdigest()}
        self.records.append({'args': args, 'exit_code': result.returncode,
                             'elapsed_ms': round((time.monotonic()-started)*1000, 2),
                             'stdout': summary(result.stdout), 'stderr': summary(result.stderr)})
        if check and result.returncode:
            raise AssertionError(self.records[-1])
        return result


def metadata(commands, path, packets=False):
    return json.loads(commands.run([FFPROBE, '-v', 'error', '-show_streams', '-show_format',
                                   *(['-show_packets'] if packets else []), '-of', 'json', path]).stdout)


def verify_output(commands, source, output, dimensions, audio_indices, expected_range=None):
    """Inspect actual streams/frames/packets and decode every stream; no success-JSON trust."""
    src = metadata(commands, source)
    out = metadata(commands, output, packets=True)
    videos = [s for s in out['streams'] if s['codec_type'] == 'video']
    audios = [s for s in out['streams'] if s['codec_type'] == 'audio']
    assert len(videos) == 1 and videos[0]['codec_name'] == 'h264'
    video = videos[0]
    assert (video['width'], video['height']) == tuple(dimensions), video
    assert video['pix_fmt'] == 'yuv420p' and video.get('sample_aspect_ratio', '1:1') == '1:1'
    assert len(audios) == len(audio_indices), (len(audios), audio_indices)
    for audio, index in zip(audios, audio_indices):
        original = next(s for s in src['streams'] if s['index'] == index)
        assert original['codec_type'] == 'audio' and audio['codec_name'] == 'aac'
        assert audio['sample_rate'] == original['sample_rate'] and audio['channels'] == original['channels']
        assert audio.get('tags', {}).get('language', 'und') == original.get('tags', {}).get('language', 'und')
    frames = json.loads(commands.run([FFPROBE, '-v', 'error', '-select_streams', 'v:0', '-show_frames',
                                    '-show_entries', 'frame=best_effort_timestamp', '-of', 'json', output]).stdout)['frames']
    pts = [int(f['best_effort_timestamp']) for f in frames]
    assert pts and all(a < b for a, b in zip(pts, pts[1:])), pts
    fps = Fraction(video['avg_frame_rate'])
    expected_duration = Fraction(2) if expected_range is None else Fraction(expected_range[1]-expected_range[0], 1_000_000)
    assert abs(Fraction(len(pts), 1)/fps-expected_duration) <= 1/fps
    packet_counts = {}
    ends = []
    starts = []
    for stream in out['streams']:
        index = stream['index']
        packets = [p for p in out['packets'] if p['stream_index'] == index]
        dts = [int(p['dts']) for p in packets]
        assert dts and all(a < b for a, b in zip(dts, dts[1:])), (index, dts)
        tb = Fraction(stream['time_base'])
        starts.append(min(Fraction(p['pts'])*tb for p in packets))
        ends.append(max(Fraction(int(p['pts'])+int(p.get('duration', 0)))*tb for p in packets))
        packet_counts[str(index)] = len(packets)
    assert max(ends)-min(ends) <= Fraction(1, 10), ends
    commands.run([FFMPEG, '-nostdin', '-v', 'error', '-xerror', '-i', output,
                  '-map', '0:v?', '-map', '0:a?', '-f', 'null', '-'])
    original_video = next(s for s in src['streams'] if s['codec_type'] == 'video')
    source_fps = Fraction(original_video['avg_frame_rate'])
    first = 0 if expected_range is None else -(-(expected_range[0]*source_fps.numerator)//(1_000_000*source_fps.denominator))
    end = 50 if expected_range is None else -(-(expected_range[1]*source_fps.numerator)//(1_000_000*source_fps.denominator))
    filters = f'[0:v:0]trim=start_frame={first}:end_frame={end},setpts=PTS-STARTPTS'
    if (original_video['width'], original_video['height']) != tuple(dimensions):
        filters += f',scale={dimensions[0]}:{dimensions[1]}:flags=bicubic+accurate_rnd'
    filters += ',setsar=1[reference];[1:v:0]setpts=PTS-STARTPTS[output];[reference][output]psnr'
    quality = commands.run([FFMPEG, '-nostdin', '-hide_banner', '-v', 'info', '-i', source,
                            '-i', output, '-filter_complex', filters, '-an', '-f', 'null', '-']).stderr
    match = re.search(r'PSNR y:([\d.]+|inf) u:([\d.]+|inf) v:([\d.]+|inf) average:([\d.]+|inf)', quality)
    assert match, quality[-1000:]
    plane_psnr = dict(zip(['y', 'u', 'v', 'average'], map(float, match.groups())))
    assert min(plane_psnr.values()) > 28, plane_psnr
    # Spectrum location distinguishes the two generated source tracks, independent of metadata.
    tones = []
    for index, source_index in enumerate(audio_indices):
        raw = commands.run([FFMPEG, '-nostdin', '-v', 'error', '-i', output, '-map', f'0:a:{index}',
                            '-t', '1', '-ar', '8000', '-ac', '1', '-f', 's16le', '-'], binary=True).stdout
        import array, math
        samples = array.array('h', raw)
        powers = {}
        for frequency in [431, 1044]:
            omega = 2*math.pi*frequency/8000
            real = sum(v*math.cos(omega*n) for n, v in enumerate(samples))
            imaginary = sum(v*math.sin(omega*n) for n, v in enumerate(samples))
            powers[frequency] = real*real+imaginary*imaginary
        original_audio = [s for s in src['streams'] if s['codec_type'] == 'audio']
        tone = [431, 1044][next(i for i, s in enumerate(original_audio) if s['index'] == source_index)]
        assert powers[tone] > powers[1475-tone]*20, powers
        tones.append({'source_stream_index': source_index, 'dominant_generated_tone_hz': tone,
                      'tone_power_ratio': powers[tone]/max(1, powers[1475-tone])})
    return {'sha256': sha256(output), 'dimensions': list(dimensions), 'audio_source_indices': audio_indices,
            'decoded_video_frames': len(pts), 'full_decode': True, 'dts_monotonic_all_streams': True,
            'packet_counts': packet_counts, 'stream_start_seconds': list(map(str, starts)),
            'stream_end_seconds': list(map(str, ends)), 'maximum_end_difference_seconds': str(max(ends)-min(ends)),
            'synthetic_audio_track_identity': tones,
            'independent_decoded_sequence_psnr_db': {k: ('infinite_identical' if v == float('inf') else v) for k, v in plane_psnr.items()},
            'subjective_full_band_audio_visual_fidelity': 'NOT_RUN'}


def environment():
    return {'platform': platform.platform(), 'machine': platform.machine(),
            'ffmpeg': FFMPEG, 'ffprobe': FFPROBE, 'python': platform.python_version()}
