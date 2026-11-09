#!/usr/bin/env python3
"""Local research checks: requires explicitly supplied pinned model and Python deps."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
import soundfile as sf

HERE = Path(__file__).resolve().parent
MODEL = Path(os.environ['AI001_TEST_MODEL'])
SPEC = importlib.util.spec_from_file_location('research_worker', HERE / 'worker.py')
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)

class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='ai001-worker-test-')
        self.root = Path(self.tmp.name)
        self.src = self.root / 'input.wav'
        t = np.arange(44100, dtype=np.float32) / 44100
        self.x = np.column_stack((np.sin(2*np.pi*300*t)*0.1, np.sin(2*np.pi*420*t)*0.1)).astype('float32')
        sf.write(self.src, self.x, 44100, subtype='FLOAT')
        self.stage = self.root / 'stage'
        self.stage.mkdir(mode=0o700)

    def tearDown(self):
        self.tmp.cleanup()

    def run_worker(self, *args):
        command = [sys.executable, str(HERE / 'worker.py'), '--model', str(MODEL), '--input', str(self.src), '--staging', str(self.stage)]
        command.extend(args)
        return subprocess.run(command, capture_output=True, text=True, timeout=45)

    def assert_failure(self, result, code):
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {'schema_version': 1, 'error': code})
        self.assertEqual(result.stderr, '')
        self.assertNotIn(str(self.root), result.stdout)

    def test_success_real_cpu_and_sample_reconstruction(self):
        before = hashlib.sha256(self.src.read_bytes()).hexdigest()
        p = self.run_worker()
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        r = json.loads(p.stdout)
        self.assertEqual((r['frames'], r['sample_rate'], r['channels']), (44100, 44100, 2))
        self.assertEqual(set(os.listdir(self.stage)), {'vocals.wav', 'accompaniment.wav'})
        v, sr = sf.read(self.stage / 'vocals.wav', dtype='float32', always_2d=True)
        a, sr2 = sf.read(self.stage / 'accompaniment.wav', dtype='float32', always_2d=True)
        self.assertEqual((sr, sr2, v.shape, a.shape), (44100, 44100, self.x.shape, self.x.shape))
        self.assertTrue(np.isfinite(v).all() and np.isfinite(a).all())
        self.assertLess(float(np.max(np.abs(v + a - self.x))), 1e-6)
        self.assertEqual(before, hashlib.sha256(self.src.read_bytes()).hexdigest())

    def test_unknown_argument_rejected(self):
        self.assert_failure(self.run_worker('--other', 'bad'), 'INVALID_ARGUMENT')
        self.assertEqual(os.listdir(self.stage), [])

    def test_missing_bad_and_symlink_model(self):
        for model, code in ((self.root/'missing.onnx','MODEL_MISSING'), (self.src,'MODEL_INVALID')):
            p = subprocess.run([sys.executable, str(HERE/'worker.py'), '--model', str(model), '--input', str(self.src), '--staging', str(self.stage)], capture_output=True, text=True, timeout=45)
            self.assert_failure(p, code)
        link = self.root / 'model-link'
        link.symlink_to(MODEL)
        p = subprocess.run([sys.executable, str(HERE/'worker.py'), '--model', str(link), '--input', str(self.src), '--staging', str(self.stage)], capture_output=True, text=True, timeout=45)
        self.assert_failure(p, 'MODEL_MISSING')
        self.assertEqual(os.listdir(self.stage), [])

    def test_invalid_input_and_nonfinite_rejected(self):
        sf.write(self.src, self.x, 44100, subtype='PCM_16')
        self.assert_failure(self.run_worker(), 'INVALID_INPUT')
        bad = self.x.copy()
        bad[0, 0] = np.nan
        sf.write(self.src, bad, 44100, subtype='FLOAT')
        self.assert_failure(self.run_worker(), 'INVALID_INPUT')
        self.assertEqual(os.listdir(self.stage), [])

    def test_short_and_overpeak_rejected(self):
        sf.write(self.src, self.x[:44099], 44100, subtype='FLOAT')
        self.assert_failure(self.run_worker(), 'INVALID_INPUT')
        bad = self.x.copy()
        bad[0,0] = 1.01
        sf.write(self.src, bad, 44100, subtype='FLOAT')
        self.assert_failure(self.run_worker(), 'INVALID_INPUT')
        self.assertEqual(os.listdir(self.stage), [])

    def test_thirty_second_silence_and_overlimit(self):
        silence = np.zeros((44100 * 30, 2), dtype='float32')
        sf.write(self.src, silence, 44100, subtype='FLOAT')
        p = self.run_worker()
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        self.assertEqual(json.loads(p.stdout)['frames'], 44100 * 30)
        data, sr = sf.read(self.stage/'vocals.wav', dtype='float32', always_2d=True)
        self.assertEqual((data.shape, sr), (silence.shape, 44100))
        self.assertTrue(np.isfinite(data).all())
        for f in self.stage.iterdir(): f.unlink()
        sf.write(self.src, np.zeros((44100 * 30 + 1, 2), dtype='float32'), 44100, subtype='FLOAT')
        self.assert_failure(self.run_worker(), 'INVALID_INPUT')
        self.assertEqual(os.listdir(self.stage), [])

    def test_stage_not_empty_or_world_readable(self):
        occupied = self.stage / 'vocals.wav'
        occupied.write_bytes(b'do not touch')
        self.assert_failure(self.run_worker(), 'STAGING_INVALID')
        self.assertEqual(occupied.read_bytes(), b'do not touch')
        occupied.unlink()
        os.chmod(self.stage, 0o755)
        self.assert_failure(self.run_worker(), 'STAGING_INVALID')
        self.assertEqual(os.listdir(self.stage), [])

    def test_stage_and_input_symlink_refused(self):
        target = self.root / 'stage-link'
        target.symlink_to(self.stage, target_is_directory=True)
        p = subprocess.run([sys.executable, str(HERE/'worker.py'), '--model', str(MODEL), '--input', str(self.src), '--staging', str(target)], capture_output=True, text=True, timeout=45)
        self.assert_failure(p, 'STAGING_INVALID')
        link = self.root / 'input-link'
        link.symlink_to(self.src)
        p = subprocess.run([sys.executable, str(HERE/'worker.py'), '--model', str(MODEL), '--input', str(link), '--staging', str(self.stage)], capture_output=True, text=True, timeout=45)
        self.assert_failure(p, 'INPUT_UNAVAILABLE')
        self.assertEqual(os.listdir(self.stage), [])

    def test_second_output_failure_removes_first(self):
        original = sf.SoundFile
        count = 0
        def fail_second(*args, **kwargs):
            nonlocal count
            mode = kwargs.get('mode', args[1] if len(args) > 1 else 'r')
            if mode == 'w':
                count += 1
                if count == 2:
                    raise OSError('controlled simulated failure')
            return original(*args, **kwargs)
        with mock.patch.object(sf, 'SoundFile', side_effect=fail_second):
            with self.assertRaises(OSError):
                worker.execute({'--model':str(MODEL),'--input':str(self.src),'--staging':str(self.stage)})
        self.assertEqual(os.listdir(self.stage), [])

if __name__ == '__main__':
    unittest.main(verbosity=2)
