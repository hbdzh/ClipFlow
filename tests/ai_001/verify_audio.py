#!/usr/bin/env python3
"""Independent AI stem physical/quality verifier; never imports production code.

All files are read-only. Input and optional references must already be the
Core-normalized 44.1 kHz stereo WAV sample timeline, not original encoded
source. No cross-correlation shifting or loudness matching is applied to E.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess

import numpy as np
import soundfile as sf

RATE = 44100
WINDOW = 44100


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def check_audio(path, ffprobe, ffmpeg, allow_wavex=False):
    probe = subprocess.run([ffprobe, '-v', 'error', '-show_streams', '-of', 'json', str(path)],
                           stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=60)
    streams = json.loads(probe.stdout)['streams']
    if len(streams) != 1 or streams[0]['codec_type'] != 'audio':
        raise AssertionError('expected exactly one audio stream')
    stream = streams[0]
    if (stream['codec_name'], int(stream['sample_rate']), stream['channels']) != ('pcm_f32le', RATE, 2):
        raise AssertionError('expected 44.1 kHz stereo float PCM')
    with sf.SoundFile(path) as audio:
        if ((audio.samplerate, audio.channels, audio.subtype) != (RATE, 2, 'FLOAT')
                or audio.format not in (('WAV', 'WAVEX') if allow_wavex else ('WAV',))):
            raise AssertionError('WAV format mismatch')
        frames = len(audio)
        if frames <= 0:
            raise AssertionError('empty output')
        peak = 0.0
        while True:
            values = audio.read(WINDOW, dtype='float32', always_2d=True)
            if not len(values):
                break
            if not np.isfinite(values).all():
                raise AssertionError('non-finite samples')
            peak = max(peak, float(np.max(np.abs(values))))
    completed = subprocess.run([ffmpeg, '-v', 'error', '-i', str(path), '-f', 'null', '-'],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE, timeout=180)
    if completed.returncode:
        raise AssertionError('FFmpeg full decode failed')
    return {'sha256': sha(path), 'frames': frames, 'rate': RATE, 'channels': 2,
            'codec': 'pcm_f32le', 'peak_abs': peak, 'ffmpeg_decode_exit': completed.returncode}


def metrics(reference, candidate):
    """AC21 E and SI-SDR (flatten stereo) with no time shift."""
    cross = energy_reference = energy_candidate = squared_error = 0.0
    max_error = 0.0
    with sf.SoundFile(reference) as ref, sf.SoundFile(candidate) as got:
        if (ref.samplerate, ref.channels, len(ref)) != (got.samplerate, got.channels, len(got)):
            raise AssertionError('reference/candidate timeline mismatch')
        count = len(ref) * ref.channels
        if count == 0:
            raise AssertionError('empty reference')
        while True:
            x = ref.read(WINDOW, dtype='float64', always_2d=True)
            y = got.read(WINDOW, dtype='float64', always_2d=True)
            if len(x) != len(y):
                raise AssertionError('truncated candidate')
            if not len(x):
                break
            if not np.isfinite(x).all() or not np.isfinite(y).all():
                raise AssertionError('non-finite samples')
            d = x-y
            max_error = max(max_error, float(np.max(np.abs(d))))
            squared_error += float(np.sum(d*d))
            energy_reference += float(np.sum(x*x))
            energy_candidate += float(np.sum(y*y))
            cross += float(np.sum(x*y))
    ref_rms = math.sqrt(energy_reference / count)
    rel = math.sqrt(squared_error / count) / max(ref_rms, 1e-8)
    if energy_reference <= 1e-12 or energy_candidate <= 1e-12:
        sisdr = None
    else:
        projection = cross * cross / energy_reference
        residual = max(energy_candidate - projection, 1e-12)
        sisdr = 10.0 * math.log10(max(projection, 1e-12) / residual)
    return {'reference_sha256': sha(reference), 'candidate_sha256': sha(candidate),
            'reference_rms': ref_rms, 'relative_rms_error': rel,
            'peak_abs_error': max_error, 'si_sdr_db': sisdr,
            'ac21_reference_match': (max_error <= 1e-7 if ref_rms < 1e-8 else rel <= 1e-4)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--reference-vocals', type=Path)
    parser.add_argument('--reference-accompaniment', type=Path)
    parser.add_argument('--ffprobe', default=shutil.which('ffprobe'))
    parser.add_argument('--ffmpeg', default=shutil.which('ffmpeg'))
    args = parser.parse_args()
    if not args.ffprobe or not args.ffmpeg:
        raise SystemExit('ffprobe/ffmpeg unavailable')
    if args.output.is_symlink() or not args.output.is_dir():
        raise SystemExit('output must be a real directory')
    paths = {'input': args.input, 'vocals': args.output/'vocals.wav',
             'accompaniment': args.output/'accompaniment.wav'}
    if {p.name for p in args.output.iterdir()} != {'vocals.wav', 'accompaniment.wav'}:
        raise SystemExit('output directory contents do not match the two-stem contract')
    evidence = {'schema': 1, 'files': {}, 'reference_comparison': {},
                'limitations': ['does not assess subjective quality', 'does not prove production lifecycle']}
    for name, path in paths.items():
        if path.is_symlink() or not path.is_file():
            raise SystemExit('symlink or non-file output')
        evidence['files'][name] = check_audio(path, args.ffprobe, args.ffmpeg, allow_wavex=name=='input')
    n = evidence['files']['input']['frames']
    if any(evidence['files'][name]['frames'] != n for name in ('vocals', 'accompaniment')):
        raise SystemExit('output sample count differs from normalized input')
    for name, reference in [('vocals', args.reference_vocals), ('accompaniment', args.reference_accompaniment)]:
        if reference:
            value = metrics(reference, paths[name])
            evidence['reference_comparison'][name] = value
            if not value['ac21_reference_match']:
                print(json.dumps(evidence, indent=2, allow_nan=False))
                raise SystemExit(1)
    print(json.dumps(evidence, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
