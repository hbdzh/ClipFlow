#!/usr/bin/env python3
"""External failure contracts written before CORE-002 implementation."""
import argparse
import ctypes as C
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
from support import API, Buffer, backend_config, encoded, probe_request, trim_request

API_INSTANCE = None


class Contract(unittest.TestCase):
    def engine(self, config=None):
        e = API_INSTANCE.create(config or backend_config())
        self.addCleanup(e.cleanup)
        return e

    def test_default_engine_remains_mock_only(self):
        e = self.engine({'schema_version': 0})
        c = e.query('ms_get_capabilities')
        self.assertFalse(c['features']['probe']['available'])
        self.assertFalse(c['features']['trim_stream_copy']['available'])
        rc, _ = e.submit(probe_request(Path('/EXAMPLE/input.mp4')))
        self.assertEqual(rc, -8)

    def test_opt_in_exposes_only_implemented_media_modes(self):
        e = self.engine()
        c = e.query('ms_get_capabilities')
        self.assertTrue(c['features']['probe']['available'])
        self.assertTrue(c['features']['trim_stream_copy']['available'])
        self.assertFalse(c['features']['accurate_reencode']['available'])
        self.assertFalse(c['features']['ai_separation']['available'])

    def test_backend_config_strict_fields_and_bounds(self):
        for key, value in [('timeout_ms', 0),('timeout_ms', 300001),('timeout_ms', True),
                           ('max_output_bytes',1023),('max_output_bytes',64*1024*1024+1),
                           ('ffmpeg',''),('ffprobe',''),('extra','untrusted')]:
            with self.subTest(key=key,value=value):
                cfg = backend_config();cfg['media_backend'][key]=value
                b=encoded(cfg);h=C.c_void_p(1)
                rc=API_INSTANCE.lib.ms_engine_create(C.c_char_p(b),len(b),C.byref(h))
                self.assertEqual(rc,-1)
                self.assertFalse(h.value)

    def test_request_shape_uri_and_range_failures_have_no_ghost_job(self):
        e=self.engine()
        good=trim_request(Path('/EXAMPLE/source.mp4'),Path('/EXAMPLE/out.mp4'))
        requests=[]
        for start,end in [(0,0),(2,1),(-1,1),(0,1.5),(False,1000000)]:
            q=json.loads(json.dumps(good));q['range']={'start_us':start,'end_us':end};requests.append(q)
        for uri in ['https://example.invalid/video.mp4','file://remote-host/video.mp4',
                    'file:///x%00.mp4','file:///x.mp4?query=1','file:///x.mp4#fragment','relative.mp4']:
            q=json.loads(json.dumps(good));q['source']['uri']=uri;requests.append(q)
        for key,value in [('strategy','accurate_reencode'),('stream_selection','video_only'),
                          ('allow_reencode_fallback',True),('extra_args',['-y'])]:
            q=json.loads(json.dumps(good));q[key]=value;requests.append(q)
        for q in requests:
            with self.subTest(request=q):
                rc,out=e.submit(q)
                self.assertNotEqual(rc,0)
                self.assertIsNone(out)
        self.assertEqual(e.destroy(),0,'Rejected inputs must not leave live tasks')


def main():
    global API_INSTANCE
    parser=argparse.ArgumentParser();parser.add_argument('--library',required=True)
    args,remaining=parser.parse_known_args();API_INSTANCE=API(Path(args.library).resolve(strict=True))
    unittest.main(argv=[sys.argv[0],*remaining])


if __name__=='__main__':main()
