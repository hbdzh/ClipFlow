#!/usr/bin/env python3
"""Independent AI-001 ABI contract tests; no production source imports.

Valid and invalid configs check only parser/lifecycle, not inferred CPU or audio
quality. Run only against a library built from a frozen candidate source tree.
"""
import argparse
import copy
import json
import shutil
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'tests' / 'core_004'))
from support import API, configuration  # independent QA adapter

OPTIONS = None


def ai_config():
    cfg = configuration()
    cfg['media_backend']['ai_backend'] = {
        'python': str(OPTIONS.python), 'worker': str(OPTIONS.worker),
        'model': str(OPTIONS.model), 'timeout_ms': 600000,
    }
    return cfg


def request():
    return {'schema_version': 0, 'operation': 'ai_separate',
            'source': {'uri': OPTIONS.source.as_uri()},
            'destination': {'uri': (OPTIONS.target_parent/'nonexistent-result').as_uri()},
            'model_id': 'umx_hq_vocals_onnx_v1'}


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = API(OPTIONS.library)
        cls.temp = tempfile.TemporaryDirectory(prefix='ai001-contract-', dir=OPTIONS.target_parent)
        OPTIONS.target_parent = Path(cls.temp.name)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def tearDown(self):
        self.assertEqual(self.api.outputs, self.api.frees, 'ABI buffer allocation/free mismatch')

    def test_mock_baseline_still_rejects_ai(self):
        engine = self.api.create({'schema_version': 0})
        try:
            caps = engine.query('ms_get_capabilities')
            self.assertFalse(caps['features']['ai_separation']['available'])
            rc, out = engine.submit(request())
            self.assertEqual((rc, out), (-8, None))
            rc, old = engine.submit({'schema_version': 0, 'operation': 'mock'})
            self.assertEqual(rc, 0)
            self.assertEqual(engine.wait(old['job_id'])['state'], 'succeeded')
        finally:
            self.assertEqual(engine.destroy(), 0)

    def test_no_ai_config_does_not_accept_ai(self):
        engine = self.api.create(configuration())
        try:
            self.assertFalse(engine.query('ms_get_capabilities')['features']['ai_separation']['available'])
            rc, value = engine.submit(request())
            if rc == 0:
                self.assertFalse(value.get('accepted', True), value)
                self.assertNotIn('job_id', value)
                self.assertFalse(value.get('job_ids'))
            else:
                self.assertEqual((rc, value), (-8, None))
        finally:
            self.assertEqual(engine.destroy(), 0)

    def test_config_strict_types_and_dependencies(self):
        cases = []
        for field, values in [('python', ['', 'relative/python', None, 1]),
                              ('worker', ['', 'relative/worker', None, True]),
                              ('model', ['', 'relative/model', None, []]),
                              ('timeout_ms', [None, 0, -1, 7200001, 1.5, '10', True])]:
            for value in values:
                cfg = ai_config()
                cfg['media_backend']['ai_backend'][field] = value
                cases.append((field, value, cfg))
        for field in ['python', 'worker', 'model']:
            cfg = ai_config()
            del cfg['media_backend']['ai_backend'][field]
            cases.append((field, 'missing', cfg))
        cfg = ai_config()
        cfg['media_backend']['ai_backend']['arbitrary_argv'] = ['-c', 'unsafe']
        cases.append(('unknown', 'arbitrary_argv', cfg))
        for flag in ['core003_enabled', 'core004_enabled']:
            cfg = ai_config()
            cfg['media_backend'][flag] = False
            cases.append((flag, False, cfg))
        for field, value, cfg in cases:
            with self.subTest(field=field, value=value):
                rc, engine = self.api.create_raw(cfg)
                if engine is not None:
                    engine.destroy()
                self.assertEqual(rc, -1, (field, value))
                self.assertIsNone(engine)

    def test_malformed_requests_rejected_without_queue_mutation(self):
        engine = self.api.create(ai_config())
        try:
            self.assertEqual(engine.submit({'schema_version': 0, 'operation': 'queue_snapshot'})[0], 0)
            before = engine.submit({'schema_version': 0, 'operation': 'queue_snapshot'})[1]
            cases = []
            for field, values in [('model_id', ['unknown', '', None, True]),
                                  ('source', [None, {}, {'uri': 'https://example.invalid/a.wav'},
                                              {'uri': 'file:///tmp/a%00.wav'}, {'uri': 'file://relative'}]),
                                  ('destination', [None, {}, {'uri': 'https://example.invalid/out'},
                                                   {'uri': 'file:///tmp/x%00out'}])]:
                for value in values:
                    item = request()
                    item[field] = value
                    cases.append((field, value, item, -1))
            for field in ('source', 'destination', 'model_id'):
                item = request()
                del item[field]
                cases.append((field, 'missing', item, -1))
            item = request()
            item['argv'] = ['-c', 'untrusted']
            cases.append(('unknown-field', None, item, -1))
            item = request()
            item['schema_version'] = 1
            cases.append(('schema', 1, item, -4))
            cases.extend([('bad-json', None, b'{', -5),
                          ('invalid-utf8', None, b'\xff', -3),
                          ('oversized', None, b'x' * 65537, -9)])
            for name, value, item, expected in cases:
                with self.subTest(name=name, value=value):
                    rc, response = engine.submit(item)
                    self.assertEqual((rc, response), (expected, None), (name, value))
            after = engine.submit({'schema_version': 0, 'operation': 'queue_snapshot'})
            self.assertEqual(after, (0, before), 'malformed inputs must not change queue')
            self.assertFalse((OPTIONS.target_parent/'nonexistent-result').exists())
        finally:
            self.assertEqual(engine.destroy(), 0)

    def test_preflight_failure_no_jobs_no_output_and_no_overwrite(self):
        engine = self.api.create(ai_config())
        try:
            rc, before = engine.submit({'schema_version':0, 'operation':'queue_snapshot'})
            self.assertEqual(rc, 0)
            nonexistent = OPTIONS.target_parent/'not-there.wav'
            missing = request()
            missing['source'] = {'uri':nonexistent.as_uri()}
            rc, rejected = engine.submit(missing)
            self.assertEqual(rc, 0)
            self.assertFalse(rejected['accepted'])
            self.assertEqual(rejected['errors'][0]['code'], 'INVALID_INPUT')
            self.assertFalse(rejected.get('job_ids'))
            self.assertNotIn('job_id',rejected)
            existing = OPTIONS.target_parent/'existing-result'
            existing.mkdir()
            sentinel = existing/'owner.txt'
            sentinel.write_bytes(b'existing result must remain untouched')
            item=request()
            item['destination']={'uri':existing.as_uri()}
            rc,rejected=engine.submit(item)
            self.assertEqual(rc,0)
            self.assertFalse(rejected['accepted'])
            self.assertEqual(rejected['errors'][0]['code'],'OUTPUT_EXISTS')
            self.assertFalse(rejected.get('job_ids'))
            self.assertNotIn('job_id',rejected)
            self.assertEqual(sentinel.read_bytes(),b'existing result must remain untouched')
            self.assertEqual(engine.submit({'schema_version':0,'operation':'queue_snapshot'}),(0,before))
            self.assertFalse((OPTIONS.target_parent/'nonexistent-result').exists())
        finally:
            self.assertEqual(engine.destroy(),0)

    def test_well_formed_explicit_config_constructs_and_closes(self):
        rc, engine = self.api.create_raw(ai_config())
        self.assertEqual(rc, 0)
        self.assertIsNotNone(engine)
        self.assertEqual(engine.destroy(), 0)

    def test_missing_or_wrong_model_and_worker_reject_before_job(self):
        """The documented business failures must not create ghost jobs/files."""
        bad_model = OPTIONS.target_parent/'wrong-model.onnx'
        source_copy = OPTIONS.target_parent/'preflight-source.wav'
        shutil.copyfile(OPTIONS.source, source_copy)
        with bad_model.open('xb') as stream:
            # Correct length is deliberate: the implementation must check the
            # complete hash instead of only trusting filename and file size.
            stream.truncate(35_623_512)
        cases = [
            ('model', OPTIONS.target_parent/'missing-model.onnx', 'DEPENDENCY_MISSING'),
            ('model', bad_model, 'MODEL_INVALID'),
            ('worker', OPTIONS.target_parent/'missing-worker.py', 'DEPENDENCY_MISSING'),
        ]
        try:
            for field, path, expected in cases:
                with self.subTest(field=field, expected=expected):
                    cfg = ai_config()
                    cfg['media_backend']['ai_backend'][field] = str(path)
                    cfg['media_backend']['ai_backend']['development_root'] = str(OPTIONS.target_parent)
                    engine = self.api.create(cfg)
                    try:
                        rc, before = engine.submit({'schema_version':0, 'operation':'queue_snapshot'})
                        self.assertEqual(rc, 0)
                        item = request()
                        item['source'] = {'uri':source_copy.as_uri()}
                        rc, rejected = engine.submit(item)
                        self.assertEqual(rc, 0)
                        self.assertFalse(rejected['accepted'])
                        self.assertEqual(rejected['errors'][0]['code'], expected)
                        self.assertNotIn('job_id', rejected)
                        self.assertFalse(rejected.get('job_ids'))
                        self.assertEqual(engine.submit({'schema_version':0, 'operation':'queue_snapshot'}), (0, before))
                        self.assertFalse((OPTIONS.target_parent/'nonexistent-result').exists())
                    finally:
                        self.assertEqual(engine.destroy(), 0)
        finally:
            bad_model.unlink()
            source_copy.unlink()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for name in ['library', 'python', 'worker', 'model', 'source', 'target-parent']:
        parser.add_argument('--'+name, type=lambda p: Path(p).absolute(), required=True)
    OPTIONS = parser.parse_args()
    unittest.main(argv=[sys.argv[0]], verbosity=2)
