#!/usr/bin/env python3
"""Offline FFmpeg normalization experiment, not a Core implementation.

Requires NumPy, SoundFile and explicitly selected local ffmpeg.  All audio is
synthetic and stays in a temporary directory; only aggregate JSON is printed.
"""
import hashlib
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf


def run(*args):
    return subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def main():
    ffmpeg = str(Path(sys.argv[1]).absolute())
    results = []
    with tempfile.TemporaryDirectory(prefix="ai001-formats-") as directory:
        root = Path(directory)
        for rate in (44100, 48000):
            for channels in (1, 2):
                n = rate * 2 + 137
                t = np.arange(n, dtype=np.float64) / rate
                signal = np.stack([0.2 * np.sin(2 * np.pi * (351 + 137 * i) * t)
                                   for i in range(channels)], axis=1).astype("float32")
                original = root / "original.wav"
                sf.write(original, signal, rate, subtype="PCM_24")
                for fmt, codec in (("wav", "pcm_s16le"), ("flac", "flac"),
                                   ("mp3", "libmp3lame"), ("m4a", "aac")):
                    encoded = root / ("source." + fmt)
                    decoded = root / "decoded.wav"
                    normalized = root / "normalized.wav"
                    base = (ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y")
                    run(*base, "-i", str(original), "-map", "0:a:0", "-c:a", codec, str(encoded))
                    run(*base, "-i", str(encoded), "-map", "0:a:0", "-c:a", "pcm_f32le", str(decoded))
                    run(*base, "-i", str(encoded), "-map", "0:a:0", "-ar", "44100", "-ac", "2", "-c:a", "pcm_f32le", str(normalized))
                    native = sf.info(decoded)
                    data, output_rate = sf.read(normalized, dtype="float32", always_2d=True)
                    expected = math.floor(native.frames * 44100 / native.samplerate + 0.5)
                    results.append({"format": fmt, "rate": rate, "channels": channels,
                        "encoded_sha256": hashlib.sha256(encoded.read_bytes()).hexdigest(),
                        "decoded_source_frames": native.frames, "expected_frames": expected,
                        "output_frames": len(data), "frame_error": len(data) - expected,
                        "output_rate": output_rate, "output_channels": data.shape[1],
                        "finite": bool(np.isfinite(data).all()),
                        "mono_copy_max_difference": float(np.max(np.abs(data[:, 0] - data[:, 1]))) if channels == 1 else None})
    print(json.dumps({"purpose": "research_only_not_core", "ffmpeg_version": run(ffmpeg, "-version").stdout.decode().splitlines()[0], "cases": results}, indent=2))


if __name__ == "__main__":
    main()
