#!/usr/bin/env python3
"""Negative tests for independent AC21 evaluator, not production tests."""
import tempfile
import unittest
from pathlib import Path
import numpy as np
import soundfile as sf
from verify_audio import metrics, check_audio
import shutil


class VerifierTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='ai001-verifier-')
        self.root = Path(self.tmp.name)
        t = np.arange(44101, dtype=np.float32) / 44100
        self.signal = np.stack((0.4*np.sin(2*np.pi*377*t), 0.2*np.sin(2*np.pi*613*t)), axis=1)
        self.ref = self.write('ref.wav', self.signal)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, samples, subtype='FLOAT'):
        path = self.root/name
        sf.write(path, samples, 44100, subtype=subtype)
        return path

    def test_exact_signal_has_zero_error(self):
        got = metrics(self.ref, self.write('same.wav', self.signal))
        self.assertEqual(got['relative_rms_error'], 0.0)
        self.assertTrue(got['ac21_reference_match'])

    def test_one_sample_shift_is_not_silently_realigned(self):
        shifted = np.zeros_like(self.signal)
        shifted[1:] = self.signal[:-1]
        got = metrics(self.ref, self.write('shift.wav', shifted))
        self.assertFalse(got['ac21_reference_match'])

    def test_short_candidate_rejected(self):
        with self.assertRaises(AssertionError):
            metrics(self.ref, self.write('short.wav', self.signal[:-1]))

    def test_silence_uses_absolute_error_threshold(self):
        zero = np.zeros_like(self.signal)
        silence = self.write('zero.wav', zero)
        output = self.write('tiny.wav', zero + 2e-7)
        result = metrics(silence, output)
        self.assertIsNone(result['si_sdr_db'])
        self.assertFalse(result['ac21_reference_match'])

    def test_nonfinite_rejected(self):
        invalid = self.signal.copy()
        invalid[5, 1] = np.nan
        with self.assertRaises(AssertionError):
            metrics(self.ref, self.write('bad.wav', invalid))

    def test_non_float_wav_is_rejected(self):
        ffprobe, ffmpeg = shutil.which('ffprobe'), shutil.which('ffmpeg')
        if not ffprobe or not ffmpeg:
            self.skipTest('ffprobe/ffmpeg absent')
        with self.assertRaises(AssertionError):
            check_audio(self.write('pcm16.wav', self.signal, 'PCM_16'), ffprobe, ffmpeg)


if __name__ == '__main__':
    unittest.main(verbosity=2)
