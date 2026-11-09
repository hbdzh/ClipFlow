#!/usr/bin/env python3
"""Independent real-media and child-process CORE-002 acceptance.

Physical output is checked independently of Core result claims. Only locally
generated media is read. Controlled shims are failure-injection processes and
are never used to produce a successful media result.
"""
import argparse
from fractions import Fraction
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest

sys.dont_write_bytecode = True
from support import (API, Commands, HERE, ROOT, backend_config, digest,
                     make_fixtures, probe_request, report, trim_request)

OPTIONS = None


class Media(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = API(OPTIONS.library)
        build = HERE/'build'
        if build.is_symlink() or (build.exists() and not build.is_dir()):
            raise ValueError('No redirected build root')
        build.mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix='independent-',dir=build)
        cls.directory = Path(cls.temp.name)
        cls.commands = Commands()
        cls.commands.run([shutil.which('ffmpeg'),'-version'])
        cls.commands.run([shutil.which('ffprobe'),'-version'])
        cls.files = make_fixtures(cls.directory,cls.commands)
        cls.original_hashes = {name:digest(path) for name,path in cls.files.items()}
        cls.observations = {}

    @classmethod
    def tearDownClass(cls):
        after = {name:digest(path) for name,path in cls.files.items()}
        if after != cls.original_hashes:
            raise AssertionError('Core modified source fixtures')
        cls.observations['input_hashes'] = cls.original_hashes
        report(OPTIONS.output, {'scope':'Independent CORE-002 real FFmpeg/ABI acceptance; no native UI/platform claim',
               'source_hashes_preserved':True,'observations':cls.observations,'commands':cls.commands.records})
        cls.temp.cleanup()

    def setUp(self):
        self.case = tempfile.TemporaryDirectory(prefix='case-',dir=self.directory)
        self.outdir = Path(self.case.name)
        self.addCleanup(self.case.cleanup)

    def engine(self, config=None):
        e=self.api.create(config or backend_config())
        self.addCleanup(e.cleanup)
        return e

    def run_job(self,e,request,expected='succeeded'):
        rc,submitted=e.submit(request)
        self.assertEqual(rc,0)
        self.assertIn(submitted['state'],('queued','running'))
        done=e.terminal(submitted['job_id'])
        self.assertEqual(done['state'],expected,done)
        self.observations.setdefault('jobs',[]).append({'request':request,'trace':list(e.trace)})
        return done

    def metadata(self,path):
        p=self.commands.run([shutil.which('ffprobe'),'-v','error','-show_format','-show_streams','-of','json',path])
        return json.loads(p.stdout)

    def raw_frames(self,path):
        p=self.commands.run([shutil.which('ffprobe'),'-v','error','-select_streams','v:0','-show_frames',
           '-show_entries','frame=best_effort_timestamp,duration,pkt_duration,key_frame,pict_type','-of','json',path])
        return json.loads(p.stdout)['frames']

    def hashes(self,path):
        p=self.commands.run([shutil.which('ffmpeg'),'-hide_banner','-nostdin','-v','error','-xerror',
           '-threads','1','-copyts','-i',path,'-map','0:v:0','-fps_mode','passthrough','-f','framehash','-hash','sha256','-'])
        return [line.split(',')[-1].strip() for line in p.stdout.splitlines() if line and not line.startswith('#')]

    def packets(self,path):
        p=self.commands.run([shutil.which('ffprobe'),'-v','error','-show_packets','-show_data_hash','sha256',
           '-show_entries','packet=stream_index,pts,dts,data_hash','-of','json',path])
        return json.loads(p.stdout)['packets']

    def verify_output(self,source,destination,result,expected_indices=None,requested=None):
        before=self.metadata(source);after=self.metadata(destination)
        self.assertEqual([(s['codec_type'],s['codec_name']) for s in before['streams']],
                         [(s['codec_type'],s['codec_name']) for s in after['streams']])
        self.commands.run([shutil.which('ffmpeg'),'-hide_banner','-nostdin','-v','error','-xerror',
                          '-i',destination,'-map','0','-f','null','-'])
        source_hashes=self.hashes(source);output_hashes=self.hashes(destination)
        indices=[]
        for value in output_hashes:
            matches=[i for i,h in enumerate(source_hashes) if h==value]
            self.assertEqual(len(matches),1,'Frame content is missing, changed or ambiguous')
            indices.append(matches[0])
        self.assertTrue(indices)
        self.assertEqual(indices,list(range(indices[0],indices[-1]+1)),'B-frame boundary caused missing/duplicate content')
        # The independent fixture has closed 1-second GOPs. The reviewed policy
        # encloses requested [1.1,2.6) with [1,3), not the QA-001 tuned duration.
        self.assertEqual(indices,list(range(30,90)) if expected_indices is None else expected_indices)
        fps=Fraction(before['streams'][0]['time_base'])
        frames=self.raw_frames(source)
        origin=Fraction(frames[0]['best_effort_timestamp'])*fps
        first=Fraction(frames[indices[0]]['best_effort_timestamp'])*fps
        last=frames[indices[-1]]
        frame_duration=last.get('duration',last.get('pkt_duration'))
        self.assertIsNotNone(frame_duration,'Independent source frame duration must be known')
        end=(Fraction(last['best_effort_timestamp'])+Fraction(frame_duration))*fps
        for actual,expected in [(result['actual_range_us']['start_us'],(first-origin)*1000000),
                                (result['actual_range_us']['end_us'],(end-origin)*1000000),
                                (result['actual_source_range_us']['start_us'],first*1000000),
                                (result['actual_source_range_us']['end_us'],end*1000000)]:
            self.assertLessEqual(abs(Fraction(actual)-expected),1)
        self.assertEqual(result['requested_range_us'],requested or {'start_us':1100000,'end_us':2600000})
        self.assertFalse(result['mock_only'])
        self.assertTrue(result['file_created'])
        source_packets=self.packets(source);out_packets=self.packets(destination)
        for packet in out_packets:
            self.assertTrue(any(s['stream_index']==packet['stream_index'] and s['data_hash']==packet['data_hash'] for s in source_packets),
                            'Output compressed packet not found in original stream')
        video_index=next(s['index'] for s in before['streams'] if s['codec_type']=='video')
        first_output_video=next(p for p in out_packets if p['stream_index']==video_index)
        first_source_video=next(p for p in source_packets if p['stream_index']==video_index and p['data_hash']==first_output_video['data_hash'])
        source_time={s['index']:Fraction(s['time_base']) for s in before['streams']}
        output_time={s['index']:Fraction(s['time_base']) for s in after['streams']}
        common_shift=Fraction(first_output_video['pts'])*output_time[video_index]-Fraction(first_source_video['pts'])*source_time[video_index]
        for index in {p['stream_index'] for p in out_packets}:
            original=[p for p in source_packets if p['stream_index']==index]
            exported=[p for p in out_packets if p['stream_index']==index]
            source_sequence=[p['data_hash'] for p in original]
            output_sequence=[p['data_hash'] for p in exported]
            offsets=[i for i in range(len(source_sequence)-len(output_sequence)+1)
                     if source_sequence[i:i+len(output_sequence)]==output_sequence]
            self.assertTrue(offsets,'Compressed output packets must form a continuous source-stream subsequence')
            self.assertTrue(any(all(abs(Fraction(new[field])*output_time[index]-Fraction(old[field])*source_time[index]-common_shift)<=Fraction(2,1000)
                                   for old,new in zip(original[offset:offset+len(exported)],exported) for field in ['pts','dts'])
                                for offset in offsets),'Every audio/video packet must preserve one common source-relative mux timestamp shift within 2ms')
            dts=[int(p['dts']) for p in exported]
            self.assertTrue(all(a<b for a,b in zip(dts,dts[1:])))
        ends=[Fraction(s.get('start_time','0'))+Fraction(s['duration']) for s in after['streams']]
        self.assertLessEqual(max(ends)-min(ends),Fraction(15,100),'AV stream-end difference exceeds reference tolerance')
        self.observations.setdefault('physical_outputs',[]).append({'source_sha256':digest(source),'output_sha256':digest(destination),
            'source_frame_indices':indices,'source_content_start_seconds':str(first),'source_content_end_exclusive_seconds':str(end),
            'normalized_first_video_pts_seconds':str(origin),'output_ffprobe':after})

    def test_probe_cfr_vfr_multi_noaudio_unicode_and_nonzero_pts(self):
        e=self.engine()
        for name in ['cfr','vfr','multi','noaudio','unicode','offset']:
            with self.subTest(name=name):
                done=self.run_job(e,probe_request(self.files[name]))
                result=done['result'];info=result['media_info']
                self.assertFalse(result['mock_only'])
                self.assertFalse(result['file_created'])
                self.assertIsInstance(info['duration_us'],int)
                self.assertIsInstance(info['video_origin_us'],int)
                self.assertGreater(info['duration_us'],3500000)
                self.assertLess(info['duration_us'],4500000)
                expected=self.metadata(self.files[name])['streams']
                self.assertEqual([(s['codec_type'],s['codec_name']) for s in info['streams']],
                                 [(s['codec_type'],s['codec_name']) for s in expected])
                if name=='offset':self.assertGreaterEqual(info['video_origin_us'],5000000)
                if name=='vfr':
                    times=[int(f['best_effort_timestamp']) for f in self.raw_frames(self.files[name])]
                    self.assertGreaterEqual(len(set(b-a for a,b in zip(times,times[1:]))),2)

    def test_real_trim_preserves_all_streams_and_contiguous_frame_content(self):
        for name in ['cfr','multi','noaudio','unicode','offset']:
            with self.subTest(name=name):
                e=self.engine();destination=self.outdir/(name+'.mp4')
                done=self.run_job(e,trim_request(self.files[name],destination))
                self.verify_output(self.files[name],destination,done['result'])

    def assert_not_success(self,e,request,expected_code=None):
        rc,value=e.submit(request)
        if rc:
            self.assertIsNone(value)
            return
        done=e.terminal(value['job_id'])
        self.assertEqual(done['state'],'failed',done)
        self.assertIsNone(done['result'])
        self.assertTrue(done['error']['code'])
        if expected_code:self.assertEqual(done['error']['code'],expected_code)

    def test_corrupt_input_and_range_past_duration_fail_without_output(self):
        for source,start,end,code in [(self.files['bad'],0,1000000,'INVALID_MEDIA'),(self.files['cfr'],0,5000000,'INVALID_INPUT')]:
            e=self.engine();destination=self.outdir/'invalid.mp4'
            self.assert_not_success(e,trim_request(source,destination,start,end),code)
            self.assertFalse(destination.exists())
            self.assertFalse(list(self.outdir.iterdir()))

    def test_source_existing_output_symlink_hardlink_and_missing_parent_protected(self):
        for kind in ['source','existing','symlink','hardlink','missing_parent']:
            with self.subTest(kind=kind):
                source=self.files['cfr'];destination=self.outdir/(kind+'.mp4')
                if kind=='source':destination=source
                if kind=='existing':destination.write_bytes(b'KEEP EXACT ORIGINAL TARGET')
                if kind=='symlink':destination.symlink_to(source)
                if kind=='hardlink':os.link(source,destination)
                if kind=='missing_parent':destination=self.outdir/'not-created'/'output.mp4'
                before=destination.read_bytes() if destination.exists() else None
                self.assert_not_success(self.engine(),trim_request(source,destination),'OUTPUT_EXISTS' if kind=='existing' else None)
                if before is not None:self.assertEqual(destination.read_bytes(),before)
                else:self.assertFalse(destination.exists())
                self.assertEqual(digest(source),self.original_hashes['cfr'])

    def controlled_tool(self,mode):
        directory=self.outdir/'shim';directory.mkdir()
        marker=directory/'operation.pid'
        text='''#!PYTHON
import os,sys,time
if '-version' in sys.argv:
 print(('ffprobe' if 'probe' in sys.argv[0] else 'ffmpeg')+' version 7.1.1 CORE-002 QA controlled process')
 sys.exit(0)
with open(MARKER,'w') as stream:stream.write(str(os.getpid()))
if MODE=='flood':
 sys.stdout.write('X'*100000);sys.stdout.flush()
time.sleep(30)
'''.replace('PYTHON',sys.executable).replace('MARKER',repr(str(marker))).replace('MODE',repr(mode))
        tools=[]
        for name in ['qa-ffmpeg','qa-ffprobe']:
            path=directory/name;path.write_text(text);path.chmod(0o700);tools.append(path)
        return tools,marker

    def wait_marker(self,marker):
        until=time.monotonic()+4
        while time.monotonic()<until:
            if marker.exists() and marker.read_text().strip():return int(marker.read_text())
            time.sleep(.01)
        self.fail('Controlled child did not actually enter operation')

    def assert_pid_gone(self,pid):
        try:os.kill(pid,0)
        except ProcessLookupError:return
        self.fail(f'Controlled process {pid} remains alive/zombie after terminal state')

    def test_repeated_cancel_kills_and_reaps_actual_owned_process(self):
        tools,marker=self.controlled_tool('sleep')
        e=self.engine(backend_config(*tools,timeout_ms=10000));destination=self.outdir/'cancel.mp4'
        rc,submitted=e.submit(trim_request(self.files['cfr'],destination));self.assertEqual(rc,0)
        pid=self.wait_marker(marker)
        self.assertEqual(e.destroy(),-7)
        self.assertEqual(e.cancel(submitted['job_id']),0)
        self.assertEqual(e.cancel(submitted['job_id']),0)
        done=e.terminal(submitted['job_id'],5)
        self.assertEqual(done['state'],'cancelled',done)
        self.assertEqual(done['error']['code'],'CANCELLED')
        self.assertIsNone(done['result']);self.assertFalse(destination.exists())
        self.assert_pid_gone(pid)
        self.observations['controlled_cancel']={'pid':pid,'confirmed_started':True,'confirmed_gone_after_terminal':True,'terminal':done,'trace':list(e.trace)}
        self.assertFalse([p for p in self.outdir.iterdir() if p.name.startswith('.mediastudio')])
        self.assertEqual(e.destroy(),0)

    def test_timeout_kills_and_reaps_actual_owned_process(self):
        tools,marker=self.controlled_tool('sleep')
        e=self.engine(backend_config(*tools,timeout_ms=800));destination=self.outdir/'timeout.mp4'
        rc,submitted=e.submit(trim_request(self.files['cfr'],destination));self.assertEqual(rc,0)
        pid=self.wait_marker(marker);done=e.terminal(submitted['job_id'],5)
        self.assertEqual(done['state'],'failed',done)
        self.assertEqual(done['error']['code'],'TIMEOUT')
        self.assertIsNone(done['result']);self.assertFalse(destination.exists());self.assert_pid_gone(pid)
        self.observations['controlled_timeout']={'pid':pid,'confirmed_started':True,'confirmed_gone_after_terminal':True,'terminal':done,'temporary_residue':list(map(str,self.outdir.glob('.mediastudio*')))}
        self.assertFalse(list(self.outdir.glob('.mediastudio*')))

    def test_output_limit_failure_does_not_wait_for_full_timeout(self):
        tools,marker=self.controlled_tool('flood')
        e=self.engine(backend_config(*tools,timeout_ms=10000,max_output_bytes=1024))
        start=time.monotonic();rc,submitted=e.submit(probe_request(self.files['cfr']));self.assertEqual(rc,0)
        pid=self.wait_marker(marker);done=e.terminal(submitted['job_id'],5)
        self.assertEqual(done['state'],'failed',done)
        self.assertEqual(done['error']['code'],'RESOURCE_LIMIT')
        elapsed=time.monotonic()-start
        self.assertLess(elapsed,5);self.assert_pid_gone(pid)
        self.observations['controlled_output_limit']={'pid':pid,'confirmed_started':True,'confirmed_gone_after_terminal':True,'elapsed_seconds':elapsed,'terminal':done}



    def test_naive_decodable_bframe_boundary_is_rejected_by_content_rule(self):
        source=self.files['cfr'];destination=self.outdir/'known-incomplete.mp4'
        self.commands.run([shutil.which('ffmpeg'),'-hide_banner','-nostdin','-v','error','-n',
                           '-ss','1.1','-i',source,'-t','1.5','-map','0','-c','copy',
                           '-avoid_negative_ts','make_zero','-map_metadata','-1',destination])
        source_hashes=self.hashes(source);output_hashes=self.hashes(destination)
        indices=[source_hashes.index(h) for h in output_hashes]
        missing=sorted(set(range(indices[0],indices[-1]+1))-set(indices))
        self.assertTrue(missing,'Negative boundary reference changed; investigate tool/input instead of accepting')
        self.observations['naive_boundary_negative']={'decoded_source_indices':indices,
              'missing_source_indices':missing,'boundary_validation':'FAIL_EXPECTED',
              'output_sha256':digest(destination),'not_a_core_success':True}
        destination.unlink()

    def test_unsupported_codec_and_subtitle_are_rejected_without_dropping_tracks(self):
        codec=self.outdir/'unsupported-codec.mp4'
        self.commands.run([shutil.which('ffmpeg'),'-nostdin','-hide_banner','-v','error','-n',
               '-f','lavfi','-i','testsrc2=size=160x96:rate=30:duration=2',
               '-c:v','mpeg4','-threads','1',codec])
        subtitle_text=self.outdir/'owned.srt'
        subtitle_text.write_text('1\n00:00:00,000 --> 00:00:02,000\nCORE-002 synthetic subtitle\n')
        subtitle=self.outdir/'unsupported-subtitle.mp4'
        self.commands.run([shutil.which('ffmpeg'),'-nostdin','-hide_banner','-v','error','-n',
              '-i',self.files['cfr'],'-i',subtitle_text,'-map','0','-map','1','-c','copy','-c:s','mov_text',subtitle])
        for source in [codec,subtitle]:
            destination=self.outdir/(source.stem+'-output.mp4');before=digest(source)
            self.assert_not_success(self.engine(),trim_request(source,destination,0,1000000),'UNSUPPORTED_FORMAT')
            self.assertFalse(destination.exists());self.assertEqual(digest(source),before)
            self.observations.setdefault('unsupported_sources',[]).append({'sha256':before,'metadata':self.metadata(source),'expected_error':'UNSUPPORTED_FORMAT'})

    def test_explicit_color_and_rotation_probe_but_trim_rejects_pending_acceptance(self):
        colored=self.outdir/'color-bt709.mp4'
        self.commands.run([shutil.which('ffmpeg'),'-nostdin','-hide_banner','-v','error','-n',
              '-f','lavfi','-i','testsrc2=size=160x96:rate=30:duration=2',
              '-c:v','libx264','-threads','1','-pix_fmt','yuv420p',
              '-color_primaries','bt709','-colorspace','bt709','-color_trc','bt709','-color_range','tv',colored])
        rotated=self.outdir/'rotate90.mp4'
        self.commands.run([shutil.which('ffmpeg'),'-nostdin','-hide_banner','-v','error','-n',
              '-display_rotation:v:0','90','-i',self.files['cfr'],'-map','0','-c','copy',rotated])
        for source in [colored,rotated]:
            before=digest(source)
            metadata=self.metadata(source)
            if source==colored:
                self.assertEqual(metadata['streams'][0]['color_space'],'bt709')
                self.assertEqual(metadata['streams'][0]['color_range'],'tv')
            else:self.assertTrue(metadata['streams'][0]['side_data_list'])
            e=self.engine();probe=self.run_job(e,probe_request(source))
            self.assertFalse(probe['result']['media_info']['trim_display_metadata_supported'])
            destination=self.outdir/(source.stem+'-output.mp4')
            self.assert_not_success(e,trim_request(source,destination,0,1000000),'UNSUPPORTED_FORMAT')
            self.assertFalse(destination.exists());self.assertEqual(digest(source),before)
            self.assertFalse(list(self.outdir.glob('.mediastudio*')))
            self.observations.setdefault('pending_display_metadata',[]).append({'sha256':before,'independent_metadata':metadata,'probe':probe,'expected_trim_error':'UNSUPPORTED_FORMAT'})

    def test_cancel_and_timeout_cleanup_an_actually_created_partial_export(self):
        real_ffmpeg=shutil.which('ffmpeg')
        for operation in ['cancel','timeout']:
            with self.subTest(operation=operation):
                shimdir=self.outdir/operation;shimdir.mkdir();marker=shimdir/'export.pid'
                tool=shimdir/'ffmpeg-export-injection'
                script="""#!PYTHON
import os,sys,time
if '-version' in sys.argv:
 print('ffmpeg version 7.1.1 CORE-002 QA controlled export failure');sys.exit(0)
if '-c' in sys.argv and sys.argv[sys.argv.index('-c')+1]=='copy':
 with open(sys.argv[-1],'wb') as stream:stream.write(b'QA partial export, never successful media')
 with open(MARKER,'w') as stream:stream.write(str(os.getpid()))
 time.sleep(30);sys.exit(23)
os.execv(REAL,[REAL]+sys.argv[1:])
""".replace('PYTHON',sys.executable).replace('MARKER',repr(str(marker))).replace('REAL',repr(real_ffmpeg))
                tool.write_text(script);tool.chmod(0o700)
                e=self.engine(backend_config(ffmpeg=tool,timeout_ms=2500 if operation=='timeout' else 10000))
                destination=shimdir/'output.mp4';rc,submitted=e.submit(trim_request(self.files['cfr'],destination));self.assertEqual(rc,0)
                pid=self.wait_marker(marker)
                staging=list(shimdir.glob('.mediastudio*/output.mp4'))
                self.assertEqual(len(staging),1,'Failure injection must enter an actual staged export')
                self.assertGreater(staging[0].stat().st_size,0)
                if operation=='cancel':self.assertEqual(e.cancel(submitted['job_id']),0)
                done=e.terminal(submitted['job_id'],5)
                self.assertEqual(done['state'],'cancelled' if operation=='cancel' else 'failed',done)
                self.assertEqual(done['error']['code'],'CANCELLED' if operation=='cancel' else 'TIMEOUT')
                self.assertFalse(destination.exists());self.assert_pid_gone(pid)
                self.assertFalse(list(shimdir.glob('.mediastudio*')))
                self.observations['staged_'+operation]={'owned_pid':pid,'confirmed_child_started':True,
                      'actual_partial_staging_file_observed':True,'confirmed_gone_after_terminal':True,
                      'staging_residue_after_terminal':[],'final_exists':False,'terminal':done}

    def test_general_keyframe_boundaries_include_start_exact_boundary_and_eof(self):
        for start,end,indices in [(0,900000,list(range(0,30))),
                                  (1000000,2000000,list(range(30,60))),
                                  (2700000,4000000,list(range(60,120)))]:
            with self.subTest(start=start,end=end):
                destination=self.outdir/(str(start)+'.mp4')
                done=self.run_job(self.engine(),trim_request(self.files['cfr'],destination,start,end))
                self.verify_output(self.files['cfr'],destination,done['result'],indices,
                                   {'start_us':start,'end_us':end})

    def test_cli_real_probe_trim_failure_and_cancel_exit_codes(self):
        cli=OPTIONS.library.parent/'media-cli'
        capabilities=self.commands.run([cli,'capabilities','--media'])
        self.assertFalse(json.loads(capabilities.stdout)['mock_only'])
        probe=self.commands.run([cli,'probe',self.files['unicode']])
        self.assertEqual(json.loads(probe.stdout)['final']['state'],'succeeded')
        destination=self.outdir/"CLI $() 空格.mp4"
        trim=self.commands.run([cli,'trim',self.files['unicode'],destination,'1100000','2600000'])
        self.verify_output(self.files['unicode'],destination,json.loads(trim.stdout)['final']['result'])
        failure=self.commands.run([cli,'probe',self.files['bad']],expected=None)
        self.assertNotEqual(failure.returncode,0)
        self.assertEqual(json.loads(failure.stdout)['final']['error']['code'],'INVALID_MEDIA')
        cancelled_destination=self.outdir/'cli-cancel.mp4'
        cancel=self.commands.run([cli,'trim',self.files['cfr'],cancelled_destination,
                                  '1100000','2600000','--cancel-after-ms','0'],expected=None)
        done=json.loads(cancel.stdout)
        self.assertEqual(done['final']['state'],'cancelled',done)
        self.assertFalse(cancelled_destination.exists())
        self.assertNotEqual(cancel.returncode,0,'Real media cancelled terminal must have nonzero CLI exit')

    def test_missing_empty_source_dependency_and_permission_errors(self):
        empty=self.outdir/'empty.mp4';empty.touch()
        for source in [empty,self.outdir/'missing.mp4']:
            self.assert_not_success(self.engine(),probe_request(source),'INVALID_INPUT')
        missing=self.outdir/'missing-ffmpeg'
        self.assert_not_success(self.engine(backend_config(ffmpeg=missing)),probe_request(self.files['cfr']),'DEPENDENCY_MISSING')
        denied=self.outdir/'denied-ffmpeg';denied.write_text('owned not executable');denied.chmod(0o600)
        self.assert_not_success(self.engine(backend_config(ffmpeg=denied)),probe_request(self.files['cfr']),'PERMISSION_DENIED')
        directory=self.outdir/'denied-directory';directory.mkdir();directory.chmod(0o500)
        self.addCleanup(directory.chmod,0o700)
        destination=directory/'output.mp4'
        self.assert_not_success(self.engine(),trim_request(self.files['cfr'],destination),'PERMISSION_DENIED')
        self.assertFalse(destination.exists())

    def test_queued_cancel_never_starts_a_second_owned_process(self):
        tools,marker=self.controlled_tool('sleep')
        e=self.engine(backend_config(*tools,timeout_ms=10000))
        rc,first=e.submit(probe_request(self.files['cfr']));self.assertEqual(rc,0)
        pid=self.wait_marker(marker)
        rc,queued=e.submit(probe_request(self.files['cfr']));self.assertEqual(rc,0)
        self.assertEqual(e.cancel(queued['job_id']),0)
        self.assertEqual(e.snapshot(queued['job_id'])['state'],'cancelled')
        self.assertEqual(e.cancel(first['job_id']),0)
        self.assertEqual(e.terminal(first['job_id'],5)['state'],'cancelled')
        self.assert_pid_gone(pid)
        self.assertEqual(int(marker.read_text()),pid)
        self.assertEqual(e.destroy(),0)
        self.observations['queued_cancel']={'owned_active_pid':pid,'queued_never_executed':True,
                                           'confirmed_gone_after_terminal':True,'trace':list(e.trace)}


def main():
    global OPTIONS
    parser=argparse.ArgumentParser();parser.add_argument('--library',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=HERE/'evidence/media-run.json')
    OPTIONS,remaining=parser.parse_known_args();OPTIONS.library=OPTIONS.library.resolve(strict=True)
    unittest.main(argv=[sys.argv[0],*remaining])


if __name__=='__main__':main()
