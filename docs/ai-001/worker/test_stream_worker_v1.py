#!/usr/bin/env python3
"""Local development checks; the model and interpreter are explicit inputs."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import unittest
import numpy as np
import soundfile as sf

WORKER = Path(__file__).resolve().with_name('stream_worker_v1.py')
MODEL = Path(os.environ.get('AI001_TEST_MODEL','/missing/ai001-model.onnx'))
RATE = 44100

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

class StreamTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='ai001-worker-v1-test-')
        self.root = Path(self.tmp.name)
        self.stage = self.root/'stage'
        self.stage.mkdir(mode=0o700)
        self.source = self.root/'input.wav'
    def tearDown(self):
        self.tmp.cleanup()
    def input(self, n, amplitude=0.2, rate=RATE, subtype='FLOAT'):
        t=np.arange(n,dtype='float32')/RATE
        s=(amplitude*np.sin(t*2*np.pi*440)).astype('float32')
        sf.write(self.source,np.stack((s,s),axis=1),rate,subtype=subtype)
    def run_worker(self, model=None, source=None, stage=None, extra=()):
        cmd=[sys.executable,str(WORKER),'--model',str(model or MODEL),'--input',str(source or self.source),'--staging',str(stage or self.stage),*extra]
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=45)
        self.assertNotIn(str(self.root),p.stdout+p.stderr)
        self.assertEqual('',p.stderr)
        return p,json.loads(p.stdout)
    def require_model(self):
        if not MODEL.is_file(): self.skipTest('AI001_TEST_MODEL missing')
    def test_bad_arguments(self):
        self.input(44100)
        p,response=self.run_worker(extra=['--unexpected','value'])
        self.assertNotEqual(0,p.returncode); self.assertEqual('INVALID_ARGUMENT',response['error']); self.assertEqual([],list(self.stage.iterdir()))
    def test_missing_bad_and_symlink_model(self):
        self.input(44100)
        bad=self.root/'bad.onnx'; bad.write_bytes(b'invalid')
        bad_same_size=self.root/'bad-same-size.onnx'
        with bad_same_size.open('wb') as f: f.truncate(35623512)
        for model in [self.root/'missing',bad,bad_same_size]:
            p,response=self.run_worker(model=model)
            self.assertNotEqual(0,p.returncode); self.assertIn(response['error'],('INPUT_UNAVAILABLE','MODEL_INVALID'))
            self.assertEqual([],list(self.stage.iterdir()))
        self.require_model()
        link=self.root/'model-symlink'; link.symlink_to(MODEL)
        p,response=self.run_worker(model=link)
        self.assertNotEqual(0,p.returncode); self.assertEqual('INPUT_UNAVAILABLE',response['error'])
    def test_bad_inputs(self):
        self.require_model()
        for count,amplitude,rate,subtype in [(2048,0.1,RATE,'FLOAT'),(44100,2.01,RATE,'FLOAT'),(44100,0.1,48000,'FLOAT'),(44100,0.1,RATE,'PCM_16')]:
            self.input(count,amplitude,rate,subtype)
            p,response=self.run_worker()
            self.assertNotEqual(0,p.returncode); self.assertEqual('INVALID_INPUT',response['error']); self.assertEqual([],list(self.stage.iterdir()))
        sf.write(self.source,np.full((44100,2),np.nan,dtype='float32'),RATE,subtype='FLOAT')
        p,response=self.run_worker(); self.assertNotEqual(0,p.returncode); self.assertEqual('INVALID_INPUT',response['error'])
    def test_nonempty_or_link_stage_rejected(self):
        self.require_model(); self.input(44100)
        sentinel=self.stage/'existing'; sentinel.write_text('untouched')
        p,response=self.run_worker(); self.assertNotEqual(0,p.returncode); self.assertEqual('STAGING_INVALID',response['error']); self.assertEqual('untouched',sentinel.read_text())
        link=self.root/'link'; link.symlink_to(self.stage,target_is_directory=True)
        p,response=self.run_worker(stage=link); self.assertNotEqual(0,p.returncode); self.assertEqual('STAGING_INVALID',response['error'])
    def test_symlink_input_and_above_ten_minutes(self):
        self.require_model(); self.input(44100)
        link=self.root/'input-symlink'; link.symlink_to(self.source)
        p,response=self.run_worker(source=link); self.assertNotEqual(0,p.returncode); self.assertEqual('INPUT_UNAVAILABLE',response['error'])
        # Sparse IEEE-float WAV: legitimate declared shape, no bulk disk write.
        count=RATE*600+1; size=count*2*4
        with self.source.open('wb') as f:
            f.write(struct.pack('<4sI4s4sIHHIIHH4sI',b'RIFF',36+size,b'WAVE',b'fmt ',16,3,2,RATE,RATE*8,8,32,b'data',size))
            f.truncate(44+size)
        p,response=self.run_worker(); self.assertNotEqual(0,p.returncode); self.assertEqual('INVALID_INPUT',response['error']); self.assertEqual([],list(self.stage.iterdir()))
    def test_private_mode_enforced(self):
        self.require_model(); self.input(44100); self.stage.chmod(0o755)
        p,response=self.run_worker(); self.assertNotEqual(0,p.returncode); self.assertEqual('STAGING_INVALID',response['error'])
    def test_real_cpu_short_and_silence(self):
        self.require_model()
        for count,amplitude in [(2049,0.2),(44100,0.0),(44100,2.0),(132001,0.2)]:
            for p in self.stage.iterdir(): p.unlink()
            self.input(count,amplitude); before=digest(self.source)
            p,response=self.run_worker()
            self.assertEqual(0,p.returncode,p.stdout); self.assertEqual(count,response['frames']); self.assertEqual(44100,response['sample_rate']); self.assertEqual(2,response['channels'])
            self.assertEqual(['accompaniment.wav','vocals.wav'],sorted(i.name for i in self.stage.iterdir()))
            a,ar=sf.read(self.stage/'accompaniment.wav',dtype='float32',always_2d=True)
            v,vr=sf.read(self.stage/'vocals.wav',dtype='float32',always_2d=True)
            x,xr=sf.read(self.source,dtype='float32',always_2d=True)
            self.assertEqual((count,2),v.shape); self.assertEqual(v.shape,a.shape); self.assertEqual((RATE,RATE,RATE),(ar,vr,xr))
            self.assertTrue(np.isfinite(a).all() and np.isfinite(v).all())
            self.assertLess(float(np.max(np.abs(v+a-x))),1e-6)
            if amplitude==0: self.assertLessEqual(float(np.max(np.abs(v))),1e-6)
            self.assertEqual(before,digest(self.source))

if __name__=='__main__': unittest.main(verbosity=2)
