#!/usr/bin/env python3
"""Independent C ABI negative tests for the explicit private development root.

A rejection is accepted only when no job is created and no existing fixture is
modified. Positive processing belongs to run_e2e.py, not these tests.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
sys.path.insert(0,str(ROOT/'tests'/'core_004'))
from support import API, configuration

OPTIONS=None

def config(root):
    cfg=configuration()
    cfg['media_backend']['ai_backend']={'python':str(OPTIONS.python),'worker':str(OPTIONS.worker),
        'model':str(OPTIONS.model),'timeout_ms':600000,'development_root':str(root)}
    return cfg

def request(source,destination):
    return {'schema_version':0,'operation':'ai_separate','source':{'uri':source.as_uri()},
            'destination':{'uri':destination.as_uri()},'model_id':'umx_hq_vocals_onnx_v1'}

class RootTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api=API(OPTIONS.library)
        # Do not count unconditional UNSUPPORTED as a passed path guard: the
        # same explicitly enabled engine must first accept a real valid job.
        root=Path(tempfile.mkdtemp(prefix='ai001-root-positive-',dir=OPTIONS.temp_parent)).resolve()
        os.chmod(root,0o700)
        source=root/'source.wav'; shutil.copyfile(OPTIONS.source,source)
        engine=None; job=None; closed=False
        try:
            engine=cls.api.create(config(root))
            rc,reply=engine.submit(request(source,root/'valid-result'))
            if rc != 0 or not reply or not reply.get('job_id'):
                raise AssertionError(('valid development job not accepted; root guard tests cannot pass by blanket rejection',rc,reply))
            job=reply['job_id']
            final=engine.wait(job,timeout=180)
            if final['state'] != 'succeeded':raise AssertionError(('valid development job failed',final))
            target=root/'valid-result'
            if not target.is_dir() or {p.name for p in target.iterdir()} != {'vocals.wav','accompaniment.wav'}:
                raise AssertionError('positive control did not actually publish both stems')
            if any((target/n).stat().st_size==0 for n in ('vocals.wav','accompaniment.wav')):
                raise AssertionError('positive control published an empty stem')
            if engine.destroy()!=0:raise AssertionError('positive control destroy failed')
            closed=True
        finally:
            if engine and not closed:
                try:
                    if job:
                        engine.cancel(job); engine.wait(job,timeout=20)
                    closed=engine.destroy()==0
                except Exception:
                    closed=False
            if closed or engine is None:
                shutil.rmtree(root)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='ai001-root-contract-',dir=OPTIONS.temp_parent)
        self.base=Path(self.temp.name).resolve(); os.chmod(self.base,0o700)
        self.root=self.base/'private root'; self.root.mkdir(mode=0o700)
        self.source=self.root/'input.wav'; shutil.copyfile(OPTIONS.source,self.source)
        self.target=self.root/'new result'
        self.outside=self.base/'outside.wav'; shutil.copyfile(OPTIONS.source,self.outside)
        self.sentinel=self.base/'sentinel'; self.sentinel.write_bytes(b'unchanged')
        self.before=self.source.read_bytes()

    def tearDown(self):
        self.assertEqual(self.api.outputs,self.api.frees,'FFI buffer allocation/free mismatch')
        self.temp.cleanup()

    def rejected(self,cfg,item,allowed=('INVALID_INPUT','PERMISSION_DENIED','OUTPUT_EXISTS')):
        """Allow create-time invalid config or request-time business rejection."""
        rc,engine=self.api.create_raw(cfg)
        if rc:
            self.assertEqual(rc,-1,('unexpected engine create result',rc)); self.assertIsNone(engine)
            return
        try:
            rc,before=engine.submit({'schema_version':0,'operation':'queue_snapshot'})
            self.assertEqual(rc,0)
            rc,reply=engine.submit(item)
            if rc==0:
                self.assertIsInstance(reply,dict)
                self.assertIs(reply.get('accepted'),False,reply)
                self.assertNotIn('job_id',reply); self.assertFalse(reply.get('job_ids'))
                self.assertTrue(reply.get('errors'),reply)
                self.assertIn(reply['errors'][0]['code'],allowed,reply)
                # Public errors do not expose source, destination or root.
                for secret in (str(self.base),str(self.root),str(self.source),str(self.target)):
                    self.assertNotIn(secret,json.dumps(reply,ensure_ascii=False))
            else:
                self.assertEqual(rc,-1,('unexpected rejection code',rc,reply))
                self.assertIsNone(reply)
            self.assertEqual(engine.submit({'schema_version':0,'operation':'queue_snapshot'}),(0,before))
        finally:
            self.assertEqual(engine.destroy(),0)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.sentinel.read_bytes(),b'unchanged')
        self.assertEqual(self.source.read_bytes(),self.before)

    def test_missing_root_valid_request_does_not_start(self):
        cfg=config(self.root); del cfg['media_backend']['ai_backend']['development_root']
        engine=self.api.create(cfg)
        try:
            self.assertFalse(engine.query('ms_get_capabilities')['features']['ai_separation']['available'])
            self.assertEqual(engine.submit(request(self.source,self.target)),(-8,None))
        finally:self.assertEqual(engine.destroy(),0)
        self.assertFalse(self.target.exists())

    def test_bad_root_configuration_values(self):
        for value in ['', 'relative', None, True, 1, '/tmp/bad\0name']:
            with self.subTest(root=value):
                cfg=config(self.root); cfg['media_backend']['ai_backend']['development_root']=value
                rc,engine=self.api.create_raw(cfg)
                if engine is not None:engine.destroy()
                self.assertEqual(rc,-1); self.assertIsNone(engine)

    def test_untrusted_root_types_and_permissions(self):
        world=self.base/'world'; world.mkdir(mode=0o700); os.chmod(world,0o777)
        shutil.copyfile(self.source,world/'input.wav')
        # The link points at a root containing a valid source, so rejection
        # cannot be explained only by a missing input file.
        link=self.base/'root-link'; link.symlink_to(self.root, target_is_directory=True)
        ordinary=self.base/'ordinary'; ordinary.write_bytes(b'not a directory')
        # root/input.wav is valid for both world and link.
        for root in [world,link,ordinary,self.base/'missing']:
            with self.subTest(root=root.name):
                self.rejected(config(root),request(root/'input.wav',root/'new result'))

    def test_source_destination_must_be_single_direct_leaf(self):
        sub=self.root/'sub'; sub.mkdir(mode=0o700)
        nested=sub/'source.wav'; shutil.copyfile(self.source,nested)
        for source,dest in [(self.outside,self.target),(nested,self.target),
                            (self.source,sub/'out'),(self.source,self.base/'outside-result')]:
            with self.subTest(source=source,dest=dest):self.rejected(config(self.root),request(source,dest))

    def test_source_symlink_and_hardlink_do_not_start(self):
        symlink=self.root/'source-link.wav'; symlink.symlink_to(self.source)
        hard=self.root/'source-hard.wav'; os.link(self.source,hard)
        self.assertGreater(os.stat(self.source).st_nlink,1)
        for source in [symlink,hard,self.source]:
            with self.subTest(source=source.name):self.rejected(config(self.root),request(source,self.target))

    def test_existing_dangling_destination_is_never_overwritten(self):
        self.target.symlink_to(self.root/'missing-target',target_is_directory=True)
        self.rejected(config(self.root),request(self.source,self.target))
        self.assertTrue(stat.S_ISLNK(os.lstat(self.target).st_mode))

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for name in ['library','python','worker','model','source','temp-parent']:
        parser.add_argument('--'+name,type=lambda p:Path(p).absolute(),required=True)
    OPTIONS=parser.parse_args()
    unittest.main(argv=[sys.argv[0]],verbosity=2)
