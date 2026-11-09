#!/usr/bin/env python3
"""Independent strict CORE-003 external ABI request/config failure contracts."""
import argparse
import copy
import ctypes as C
import json
from pathlib import Path
import sys
import unittest
sys.dont_write_bytecode=True
from support import API,Buffer,HERE,accurate_request,copy_v1_request,backend_config,encoded,report

API_INSTANCE=None
OBSERVATIONS=[]


class Contract(unittest.TestCase):
    def engine(self,config=None):
        value=API_INSTANCE.create(config or backend_config())
        self.addCleanup(value.cleanup)
        return value

    def test_mock_engine_rejects_new_profiles(self):
        e=self.engine({'schema_version':0})
        for request in [accurate_request(HERE/'build/missing.mp4',HERE/'build/output.mp4'),
                        copy_v1_request(HERE/'build/missing.mp4',HERE/'build/output.mp4')]:
            rc,value=e.submit(request);self.assertEqual(rc,-8);self.assertIsNone(value)
        self.assertFalse(e.query('ms_get_capabilities')['features']['accurate_reencode']['available'])

    def test_old_deadline_bounds_remain_and_new_deadline_strict(self):
        for key,value in [('timeout_ms',0),('timeout_ms',300001),('timeout_ms',True),
            ('accurate_reencode_timeout_ms',0),('accurate_reencode_timeout_ms',7200001),
            ('accurate_reencode_timeout_ms',True),('accurate_reencode_timeout_ms',-1),
            ('accurate_reencode_timeout_ms',1.5),('accurate_reencode_timeout_ms','600000'),
            ('accurate_reencode_timeout_ms',2**64),('accurate_timeout_ms',600000),('core003_enabled',1),('core003_enabled','true'),('core003_enabled',None)]:
            with self.subTest(key=key,value=value):
                cfg=backend_config();cfg['media_backend'][key]=value;b=encoded(cfg);h=C.c_void_p(1)
                rc=API_INSTANCE.lib.ms_engine_create(C.c_char_p(b),len(b),C.byref(h))
                self.assertEqual(rc,-1);self.assertFalse(h.value)
        for value in [1,600000,7200000]:
            cfg=backend_config();cfg['media_backend']['accurate_reencode_timeout_ms']=value
            e=self.engine(cfg);self.assertEqual(e.destroy(),0)

    def test_profiles_are_exact_and_missing_profile_never_enables_accurate(self):
        e=self.engine();base=accurate_request(HERE/'build/missing.mp4',HERE/'build/output.mp4');bad=[]
        for strategy,profile in [('accurate_reencode',None),('accurate_reencode',''),
            ('accurate_reencode','mp4_h264_copy_v1'),('stream_copy','mp4_h264_standard_v1'),
            ('stream_copy','unknown'),('accurate_reencode','mp4_h264_standard_v2'),
            ('accurate_reencode',True),('accurate_reencode',{'codec':'h264'})]:
            q=copy.deepcopy(base);q['strategy']=strategy
            if profile is None:q.pop('profile')
            else:q['profile']=profile
            bad.append(q)
        for key,value in [('codec','libx264'),('crf',0),('extra_args',['-y']),('allow_reencode_fallback',True),
                          ('stream_selection','video_only'),('filter','null')]:
            q=copy.deepcopy(base);q[key]=value;bad.append(q)
        for q in bad:
            with self.subTest(request=q):
                rc,value=e.submit(q);self.assertEqual(rc,-1);self.assertIsNone(value)
        self.assertEqual(e.destroy(),0,'rejected inputs must not create ghost jobs')

    def test_new_profiles_preserve_uri_range_schema_utf8_bounds(self):
        e=self.engine();base=accurate_request(HERE/'build/missing.mp4',HERE/'build/output.mp4')
        for start,end in [(-1,1),(0,0),(2,1),(False,1000000),(0,2**63),(0,1.2)]:
            q=copy.deepcopy(base);q['range']={'start_us':start,'end_us':end}
            rc,value=e.submit(q);self.assertEqual(rc,-1);self.assertIsNone(value)
        for uri in ['file://host/a.mp4','https://example.invalid/a.mp4','file:///a%00.mp4',
                    'file:///a.mp4?x=1','file:///a.mp4#fragment']:
            q=copy.deepcopy(base);q['source']['uri']=uri
            rc,value=e.submit(q);self.assertEqual(rc,-1);self.assertIsNone(value)
        q=copy.deepcopy(base);q['schema_version']=1;self.assertEqual(e.submit(q)[0],-4)
        self.assertEqual(e.submit(b'\xff')[0],-3)
        self.assertEqual(e.submit(b'x'*65537)[0],-9)
        self.assertEqual(e.destroy(),0)

    def test_default_backend_preserves_legacy_capabilities_and_blocks_new_profile_without_io(self):
        e=self.engine()
        self.assertFalse(e.query('ms_get_capabilities')['features']['accurate_reencode']['available'])
        for request in [accurate_request(HERE/'build/not-created.mp4',HERE/'build/new-out.mp4'),
                        copy_v1_request(HERE/'build/not-created.mp4',HERE/'build/new-out.mp4')]:
            rc,out=e.submit(request);self.assertEqual(rc,0)
            final=e.terminal(out['job_id']);self.assertEqual(final['state'],'failed')
            self.assertEqual(final['error']['code'],'UNSUPPORTED_BACKEND')
        OBSERVATIONS.append({'case':'default disabled','trace':list(e.trace)})

    def test_valid_profiles_reach_worker_with_new_result_errors(self):
        for request in [accurate_request(HERE/'build/not-created.mp4',HERE/'build/new-out.mp4'),
                        copy_v1_request(HERE/'build/not-created.mp4',HERE/'build/new-out.mp4')]:
            cfg=backend_config();cfg['media_backend']['core003_enabled']=True
            e=self.engine(cfg);rc,out=e.submit(request);self.assertEqual(rc,0)
            final=e.terminal(out['job_id']);self.assertEqual(final['state'],'failed')
            self.assertEqual(final['error']['code'],'INVALID_INPUT')
            OBSERVATIONS.append({'request':request,'trace':list(e.trace)})


def main():
    global API_INSTANCE
    parser=argparse.ArgumentParser();parser.add_argument('--library',required=True);parser.add_argument('--output',type=Path,default=HERE/'evidence/contract-run.json')
    args,rest=parser.parse_known_args();API_INSTANCE=API(Path(args.library).resolve(strict=True))
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Contract))
    report(args.output,{'scope':'CORE-003 independent strict C ABI JSON contract tests; not media correctness',
        'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
        'result':'PASS' if result.wasSuccessful() else 'FAIL','observations':OBSERVATIONS})
    raise SystemExit(not result.wasSuccessful())


if __name__=='__main__':main()
