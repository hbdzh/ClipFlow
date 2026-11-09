#!/usr/bin/env python3
"""Independent actual native app self-test and physical export acceptance."""
import argparse
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest

sys.dont_write_bytecode = True
from support import (HERE, ROOT, Commands, MediaVerifier, export_hang_tool,
                     make_fixtures, sha256, source_hashes, write_report)

OPTIONS = None


class Native(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = source_hashes()
        cls.commands = Commands(); cls.verifier = MediaVerifier(cls.commands)
        build = HERE/'build'
        if build.is_symlink() or (build.exists() and not build.is_dir()):
            raise ValueError('No redirected build root')
        build.mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix='native-', dir=build)
        cls.directory = Path(cls.temp.name)
        cls.files = make_fixtures(cls.directory, cls.commands)
        cls.original = {name:sha256(path) for name,path in cls.files.items()}
        cls.observations = []
        cls.binary_sha = sha256(OPTIONS.binary)
        cls.library = OPTIONS.binary.parent.parent/'Frameworks/libmedia_ffi.dylib'
        cls.library_sha = sha256(cls.library)
        cls.native_source_timestamps_before_binary = all(p.stat().st_mtime_ns <= OPTIONS.binary.stat().st_mtime_ns for p in [*(ROOT/'apps/macos/Sources').glob('*.swift'),ROOT/'contracts/media_core_v0.h'])
        cls.commands.run([shutil.which('ffmpeg'),'-version'])
        cls.commands.run([shutil.which('ffprobe'),'-version'])
        cls.commands.run(['sw_vers'])

    @classmethod
    def tearDownClass(cls):
        after = source_hashes()
        unchanged = cls.before == after
        sources_preserved = all(sha256(path)==cls.original[name] for name,path in cls.files.items())
        result = {'captured_at':datetime.now(timezone(timedelta(hours=8))).isoformat(),
                  'scope':'Actual macOS app same WorkspaceModel/PreviewController/CoreBridge self-test plus independent physical output validation; CUA separate',
                  'binary':str(OPTIONS.binary),'binary_sha256':cls.binary_sha,'library_sha256':cls.library_sha,
                  'source_before':cls.before,'source_after':after,'source_unchanged':unchanged,
                  'native_sources_not_newer_than_binary':cls.native_source_timestamps_before_binary,
                  'test_source_sha256':{str(p.relative_to(ROOT)):sha256(p) for p in sorted(HERE.glob('*.py'))},
                  'input_hashes':cls.original,'source_media_preserved':sources_preserved,
                  'observations':cls.observations,'commands':cls.commands.records}
        write_report(OPTIONS.output,result)
        cls.temp.cleanup()
        if not cls.native_source_timestamps_before_binary:raise AssertionError('Native source/header newer than tested binary; rebuild required')
        if not unchanged:raise AssertionError('Implementation changed during independent run')
        if not sources_preserved:raise AssertionError('Native app changed source media bytes')
        if sha256(OPTIONS.binary)!=cls.binary_sha or sha256(cls.library)!=cls.library_sha:
            raise AssertionError('App binary/library changed during independent run')

    def setUp(self):
        self.case = tempfile.TemporaryDirectory(prefix='case-',dir=self.directory)
        self.outdir = Path(self.case.name); self.addCleanup(self.case.cleanup)

    def app(self, source=None, destination=None, extra=(), env=None, start_us=1100000, end_us=2600000):
        source = source or self.files['cfr']; destination = destination or self.outdir/'result.mp4'
        argv = [OPTIONS.binary,'--media-self-test','--source',source,'--destination',destination,
                '--start-us',start_us,'--end-us',end_us,'--preview-frame',self.outdir/'preview.png',*extra]
        if env is None:
            env=os.environ.copy();env['MEDIASTUDIO_FFMPEG']=shutil.which('ffmpeg');env['MEDIASTUDIO_FFPROBE']=shutil.which('ffprobe')
            env['MEDIASTUDIO_TIMEOUT_MS']='30000';env['MEDIASTUDIO_MAX_OUTPUT_BYTES']='8388608'
        p = self.commands.run(argv,env=env)
        value = json.loads(p.stdout)
        self.observations.append({'test':self.id(),'source':str(source),'destination':str(destination),'report':value})
        return value,destination

    def common(self, report):
        self.assertEqual(report['schema_version'],0)
        self.assertTrue(report['passed'])
        self.assertTrue(report['closed'])
        diagnostics = report['bridge_diagnostics']
        self.assertTrue(diagnostics['all_calls_off_main_thread'])
        self.assertEqual(diagnostics['output_calls'],diagnostics['buffer_release_calls'])
        self.assertGreater(diagnostics['output_calls'],0)
        self.assertTrue(diagnostics['error_outputs_were_empty'])
        self.assertGreater(report['heartbeat_count'],0)
        self.assertEqual(report['access_diagnostics']['active_granted'],0)
        self.assertEqual(report['access_diagnostics']['granted'],report['access_diagnostics']['stops'])
        self.assertFalse(list(self.outdir.glob('.mediastudio*')))

    def preview(self, report, expected_offset_us):
        preview = report['preview']
        self.assertTrue(preview['ready'])
        self.assertTrue(preview['seek_completed'])
        self.assertTrue(preview['first_visible_image_confirmed'])
        self.assertGreaterEqual(preview['first_visible_image_item_us'],expected_offset_us)
        self.assertLessEqual(abs(preview['image_actual_item_us']-(1100000+expected_offset_us)),50000)
        self.assertEqual(preview['seek_normalized_us'],1100000)
        self.assertEqual(preview['timeline_offset_us'],expected_offset_us)
        self.assertLessEqual(abs(preview['observed_item_us']-(1100000+expected_offset_us)),50000)

    def successful(self, source, offset=0):
        report,destination = self.app(source=source)
        self.common(report); self.preview(report,offset)
        self.assertEqual(report['final']['state'],'succeeded')
        result = report['final']['result']
        self.assertFalse(result['mock_only']); self.assertTrue(result['file_created'])
        verification = self.verifier.verify(source,destination,result)
        self.observations[-1]['independent_physical_verification'] = verification
        self.observations[-1]['preview_content_verification'] = self.verifier.verify_preview_frame(source,self.outdir/'preview.png')
        self.assertEqual(sha256(source),self.original[next(name for name,path in self.files.items() if path==source)])

    def test_01_real_import_preview_selection_and_result(self):
        self.successful(self.files['cfr'])

    def test_02_nonzero_source_pts_preview_and_export(self):
        self.successful(self.files['offset'],5000000)

    def test_03_unicode_spaces_and_shell_literal_path(self):
        self.successful(self.files['unicode'])

    def test_04_no_audio_track_remains_video_only(self):
        self.successful(self.files['noaudio'])

    def test_05_all_audio_tracks_are_retained(self):
        self.successful(self.files['multi'])

    def test_06_corrupt_import_does_not_fake_ready(self):
        report,destination = self.app(source=self.files['bad'],extra=['--expect-import-failure'])
        self.common(report)
        self.assertFalse(destination.exists())
        self.assertFalse(report['preview']['ready'])
        self.assertNotEqual((report.get('final') or {}).get('state'),'succeeded')

    def test_07_existing_output_is_not_overwritten(self):
        destination = self.outdir/'existing.mp4'; destination.write_bytes(b'KEEP EXACT OWNED TARGET')
        before = sha256(destination)
        report,_ = self.app(destination=destination,extra=['--expect-state','failed'])
        self.common(report)
        self.assertEqual(report['final']['state'],'failed')
        self.assertEqual(report['final']['error']['code'],'OUTPUT_EXISTS')
        self.assertEqual(sha256(destination),before)

    def test_08_missing_dependency_has_no_fake_export(self):
        env = os.environ.copy(); env['MEDIASTUDIO_FFMPEG']=str(self.outdir/'missing-ffmpeg')
        report,destination = self.app(extra=['--expect-import-failure'],env=env)
        self.common(report)
        self.assertFalse(destination.exists())
        self.assertNotEqual((report.get('final') or {}).get('state'),'succeeded')

    def hang(self, closing):
        shim,marker = export_hang_tool(self.outdir/'shim')
        env=os.environ.copy();env['MEDIASTUDIO_FFMPEG']=str(shim);env['MEDIASTUDIO_TIMEOUT_MS']='10000'
        extra = ['--close-during-job','--close-after-ms','2000','--expect-state','cancelled'] if closing else ['--cancel-after-ms','2000','--expect-state','cancelled']
        observed_partial=[]
        stop=threading.Event()
        def observe_partial():
            while not stop.is_set():
                for path in self.outdir.glob('.mediastudio-*/output.mp4'):
                    try:
                        size=path.stat().st_size
                        if size>0:
                            observed_partial.append({'path':str(path),'bytes':size,'sha256':sha256(path),
                                                     'observed_monotonic':time.monotonic()})
                            return
                    except FileNotFoundError:
                        pass
                stop.wait(.01)
        observer=threading.Thread(target=observe_partial,name='ui001-partial-witness')
        observer.start()
        try:
            report,destination = self.app(extra=extra,env=env)
        finally:
            stop.set();observer.join(timeout=2)
        self.assertFalse(observer.is_alive())
        self.assertTrue(observed_partial,'Independent observer must see actual nonempty staging before cancellation/close returns')
        self.common(report)
        self.assertTrue(marker.exists(),'Fault injection must enter real export stage and create partial output')
        pid=int(marker.read_text())
        with self.assertRaises(ProcessLookupError):os.kill(pid,0)
        self.assertFalse(destination.exists())
        self.assertFalse(list(self.outdir.glob('.mediastudio*')))
        self.assertGreaterEqual(report['heartbeat_count'],10)
        self.observations[-1]['owned_process_cleanup']={'pid':pid,'gone_after_terminal':True,'partial_physically_observed_during_job':observed_partial[0],'final_absent':True,'staging_absent':True}
        return report

    def test_09_active_cancel_cleans_partial_before_terminal(self):
        report=self.hang(False)
        self.assertEqual(report['final']['state'],'cancelled')

    def test_10_active_close_waits_for_cleanup_off_main(self):
        report=self.hang(True)
        self.assertEqual(report['final']['state'],'cancelled')

    def test_11_invalid_ui_range_does_not_submit_trim(self):
        report,destination=self.app(extra=['--expect-state','rejected'],start_us=2000000,end_us=1000000)
        self.common(report)
        self.assertTrue(report['rejected'])
        self.assertIsNone(report.get('final'))
        self.assertTrue(report['error'])
        self.assertFalse(any(snapshot['operation']=='trim' for snapshot in report['trace']))
        self.assertFalse(destination.exists())


def main():
    global OPTIONS
    parser=argparse.ArgumentParser()
    parser.add_argument('--binary',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=HERE/'evidence/native-run.json')
    OPTIONS,remaining=parser.parse_known_args();OPTIONS.binary=OPTIONS.binary.resolve(strict=True)
    unittest.main(argv=[sys.argv[0],*remaining])


if __name__=='__main__':main()
