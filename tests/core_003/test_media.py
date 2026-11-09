#!/usr/bin/env python3
"""Independent CORE-003 C ABI/CLI media and physical cleanup acceptance."""
import argparse
import ast
from array import array
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
sys.dont_write_bytecode=True
from support import (API,Commands,HERE,ROOT,accurate_request,copy_v1_request,backend_config,digest,
                     probe_request,report,trim_request)
from verify import binary,metadata,frames,selected_indices,frame_sequence,timing,psnr_and_patches,audio_mapping,FFMPEG,FFPROBE

OPTIONS=None


def config(**kwargs):
    value=backend_config(**kwargs);value['media_backend']['core003_enabled']=True
    return value


class Media(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api=API(OPTIONS.library);cls.commands=Commands();cls.observations={}
        cls.manifest=json.loads((HERE/'build/fixture-manifest.json').read_text())
        cls.files={n:Path(v['path']) for n,v in cls.manifest.items()}
        for n,p in cls.files.items():assert digest(p)==cls.manifest[n]['sha256'],n
        cls.temp=tempfile.TemporaryDirectory(prefix='media-run-',dir=HERE/'build');cls.directory=Path(cls.temp.name)

    @classmethod
    def tearDownClass(cls):
        after={n:digest(p) for n,p in cls.files.items()}
        preserved=all(after[n]==cls.manifest[n]['sha256'] for n in after)
        report(OPTIONS.output,{'scope':'CORE-003 independent real macOS host C ABI/CLI media; not native UI/other platform/perceptual quality',
              'source_hashes_preserved':preserved,'fixture_manifest':cls.manifest,'observations':cls.observations,
              'commands':cls.commands.records,'only_fixture_filter':OPTIONS.only_fixture})
        cls.temp.cleanup();assert preserved,'Core modified QA source'

    def setUp(self):
        self.case=tempfile.TemporaryDirectory(prefix='case-',dir=self.directory);self.outdir=Path(self.case.name)
        self.addCleanup(self.case.cleanup)

    def tearDown(self):
        files=[]
        for path in self.outdir.rglob('*.mp4'):
            if path.is_file():
                value={'name':str(path.relative_to(self.outdir)),'sha256':digest(path),'bytes':path.stat().st_size}
                try:value['metadata']=metadata(self.commands,path)
                except AssertionError:value['metadata']='invalid or incomplete container'
                files.append(value)
        self.observations.setdefault('per_test_artifacts',[]).append({'test':self.id(),'files':files})

    def engine(self,cfg=None):
        e=self.api.create(cfg or config());self.addCleanup(e.cleanup);return e

    def run_job(self,e,request,expected='succeeded',error=None):
        rc,submitted=e.submit(request);self.assertEqual(rc,0,(rc,request))
        final=e.terminal(submitted['job_id'],seconds=120)
        self.observations.setdefault('jobs',[]).append({'request':request,'trace':list(e.trace)})
        self.assertEqual(final['state'],expected,final)
        if error:self.assertEqual(final['error']['code'],error,final)
        return final

    def physical(self,source,out,done,start=1100000,end=2600000,quality=False):
        result=done['result'];self.assertTrue(result['reencoded']);self.assertFalse(result['lossless'])
        self.assertEqual(result['strategy'],'accurate_reencode');self.assertEqual(result['profile'],'mp4_h264_standard_v1')
        self.assertFalse(result['mock_only']);self.assertTrue(result['file_created'])
        expected,src,fs,origin,tb=selected_indices(self.commands,source,start,end)
        dst=metadata(self.commands,out)
        slayout=[(s['codec_type'],s['codec_name'],s.get('channels'),s.get('sample_rate'),s.get('tags',{}).get('language')) for s in src['streams']]
        dlayout=[(s['codec_type'],s['codec_name'],s.get('channels'),s.get('sample_rate'),s.get('tags',{}).get('language')) for s in dst['streams']]
        self.assertEqual(slayout,dlayout)
        sv=next(s for s in src['streams'] if s['codec_type']=='video');dv=next(s for s in dst['streams'] if s['codec_type']=='video')
        for key in ['width','height','pix_fmt','sample_aspect_ratio','color_range','color_space','color_transfer','color_primaries']:
            self.assertEqual(sv.get(key),dv.get(key),(key,sv.get(key),dv.get(key)))
        self.assertEqual(Fraction(sv['avg_frame_rate']),Fraction(dv['avg_frame_rate']))
        self.commands.run([FFMPEG,'-nostdin','-hide_banner','-v','error','-xerror','-i',out,'-map','0','-f','null','-'],timeout=120)
        sequence=frame_sequence(self.commands,source,out,expected)
        first=Fraction(fs[expected[0]]['best_effort_timestamp'])*tb
        clock=timing(self.commands,out,source,first,origin+Fraction(end,1000000))
        last=fs[expected[-1]];duration=Fraction(last.get('duration',last.get('pkt_duration')))*tb
        actual_end=Fraction(last['best_effort_timestamp'])*tb+duration
        for key,expected_value in [('start_us',(first-origin)*1000000),('end_us',(actual_end-origin)*1000000)]:
            self.assertLessEqual(abs(Fraction(result['actual_range_us'][key])-expected_value),1)
        output={'source_sha256':digest(source),'output_sha256':digest(out),'expected_source_frame_indices':expected,
                'frame_sequence':sequence,'timing':clock,'output_probe':dst,'decoded_audio_source_mapping':audio_mapping(self.commands,source,out,first)}
        if quality:output['quality']=psnr_and_patches(self.commands,source,out,expected,self.outdir)
        self.observations.setdefault('physical_outputs',[]).append(output)
        return expected

    def test_exact_basic_cfr(self):
        source=self.files['cfr'];out=self.outdir/'basic.mp4'
        done=self.run_job(self.engine(),accurate_request(source,out));self.physical(source,out,done)

    def test_exact_cfr_b_frames_fractional_rates_offset_and_track_layouts(self):
        for name in ['stereo','audio44100','audio_first','multi','noaudio','offset','short_audio','delayed_audio','fps24','fps25','fps60','fps23976','fps2997','unicode','unlabelled']:
            if OPTIONS.only_fixture and name!=OPTIONS.only_fixture:continue
            with self.subTest(fixture=name):
                out=self.outdir/(name+'.mp4');e=self.engine()
                done=self.run_job(e,accurate_request(self.files[name],out))
                self.physical(self.files[name],out,done)
                self.assertEqual(e.destroy(),0)

    def test_exact_head_tail_and_empty_frame_selection(self):
        source=self.files['cfr'];e=self.engine()
        for i,(start,end) in enumerate([(0,500000),(333333,1000000),(3100000,4000000)]):
            out=self.outdir/f'edge-{i}.mp4';done=self.run_job(e,accurate_request(source,out,start,end))
            self.physical(source,out,done,start,end)
        out=self.outdir/'empty.mp4'
        self.run_job(e,accurate_request(source,out,1100001,1100002),'failed','INVALID_INPUT')
        self.assertFalse(out.exists())

    def test_1080p_709_quality_planes_patches_and_full_content(self):
        source=self.files['quality1080'];out=self.outdir/'quality.mp4'
        done=self.run_job(self.engine(),accurate_request(source,out));self.physical(source,out,done,quality=True)

    def test_copy_v1_709_preserves_packets_and_legacy_rejects_it(self):
        source=self.files['cfr'];out=self.outdir/'copy709.mp4';e=self.engine()
        done=self.run_job(e,copy_v1_request(source,out));self.assertFalse(done['result']['reencoded'])
        sm=metadata(self.commands,source);dm=metadata(self.commands,out)
        for key in ['color_range','color_space','color_transfer','color_primaries']:
            self.assertEqual(sm['streams'][0].get(key),dm['streams'][0].get(key))
        def hashes(path):
            p=self.commands.run([FFMPEG,'-nostdin','-v','error','-i',path,'-map','0:v:0','-fps_mode','passthrough','-f','framehash','-hash','sha256','-'])
            return [line.split(',')[-1].strip() for line in p.stdout.splitlines() if line and not line.startswith('#')]
        self.assertEqual(hashes(out),hashes(source)[30:90])
        def packets(path):
            return json.loads(self.commands.run([FFPROBE,'-v','error','-show_packets','-show_data_hash','sha256',
              '-show_entries','packet=stream_index,data_hash','-of','json',path]).stdout)['packets']
        original=packets(source);exported=packets(out)
        for stream in dm['streams']:
            index=stream['index'];before=[p['data_hash'] for p in original if p['stream_index']==index];after=[p['data_hash'] for p in exported if p['stream_index']==index]
            self.assertTrue(any(before[i:i+len(after)]==after for i in range(len(before)-len(after)+1)))
        legacy=self.outdir/'legacy.mp4'
        self.run_job(e,trim_request(source,legacy),'failed','UNSUPPORTED_FORMAT');self.assertFalse(legacy.exists())
        self.observations['copy_v1']={'output_sha256':digest(out),'result':done['result']}

    def test_unsupported_and_damaged_inputs_never_publish(self):
        e=self.engine()
        for name in ['vfr','sar','hdr','rotation','subtitle','hevc','alpha','multi_video','surround','bad']:
            with self.subTest(fixture=name):
                out=self.outdir/(name+'.mp4')
                done=self.run_job(e,accurate_request(self.files[name],out),'failed')
                self.assertIn(done['error']['code'],['UNSUPPORTED_FORMAT','INVALID_MEDIA','PROCESS_FAILED'])
                self.assertFalse(out.exists());self.assertEqual(list(self.outdir.glob('.mediastudio-*')),[])
        out=self.outdir/'range.mp4';self.run_job(e,accurate_request(self.files['cfr'],out,0,5000000),'failed','INVALID_INPUT');self.assertFalse(out.exists())

    def test_source_and_existing_outputs_are_never_overwritten(self):
        source=self.files['cfr'];e=self.engine()
        self.run_job(e,accurate_request(source,source),'failed','OUTPUT_EXISTS')
        target=self.outdir/'existing.mp4';target.write_bytes(b'known existing content')
        self.run_job(e,accurate_request(source,target),'failed','OUTPUT_EXISTS');self.assertEqual(target.read_bytes(),b'known existing content')
        link=self.outdir/'linked.mp4';link.symlink_to(target)
        self.run_job(e,accurate_request(source,link),'failed','OUTPUT_EXISTS');self.assertEqual(target.read_bytes(),b'known existing content')
        directory=self.outdir/'directory.mp4';directory.mkdir()
        self.run_job(e,accurate_request(source,directory),'failed','OUTPUT_EXISTS')

    def shim(self,mode):
        path=self.outdir/'ffmpeg-fixture.py';marker=self.outdir/'process-marker.json'
        # Self-owned failure injection only. Successful data is always real FFmpeg.
        path.write_text('#!'+sys.executable+'\nimport json,os,pathlib,subprocess,sys,time\n'
            +'args=sys.argv[1:]\nreal='+repr(FFMPEG)+'\nmode='+repr(mode)+'\nmarker=pathlib.Path('+repr(str(marker))+')\n'
            +'if "-encoders" in args and mode=="missing_encoder":\n'
            +' p=subprocess.run([real,*args],capture_output=True);sys.stdout.buffer.write(b"\\n".join(x for x in p.stdout.splitlines() if b"libx264" not in x));sys.stderr.buffer.write(p.stderr);sys.exit(p.returncode)\n'
            +'if "libx264" in args and "-i" in args and mode in ("hang","fail"):\n'
            +' staging=pathlib.Path(args[-1]);staging.write_bytes(b"physically observed partial staging");marker.write_text(json.dumps({"pid":os.getpid(),"staging":str(staging)}))\n'
            +' if mode=="fail":sys.exit(1)\n'
            +' while True:time.sleep(0.05)\n'
            +'os.execv(real,[real,*args])\n')
        path.chmod(0o700);return path,marker

    def test_missing_encoder_does_not_fallback(self):
        shim,_=self.shim('missing_encoder');e=self.engine(config(ffmpeg=shim))
        cap=e.query('ms_get_capabilities');self.assertFalse(cap['features']['accurate_reencode']['available'])
        out=self.outdir/'missing.mp4';self.run_job(e,accurate_request(self.files['cfr'],out),'failed','DEPENDENCY_MISSING');self.assertFalse(out.exists())

    def observe_marker(self,e,job,marker):
        until=time.monotonic()+10
        while time.monotonic()<until and not marker.exists():
            snap=e.snapshot(job)
            if snap['state'] in ['succeeded','failed','cancelled']:raise AssertionError(('terminated before encode marker',snap))
            time.sleep(.01)
        self.assertTrue(marker.exists());value=json.loads(marker.read_text());staging=Path(value['staging']);self.assertTrue(staging.is_file());self.assertGreater(staging.stat().st_size,0)
        value['observed_nonempty_sha256']=digest(staging);value['observed_nonempty_size']=staging.stat().st_size
        return value

    def assert_reaped(self,value):
        try:os.kill(value['pid'],0)
        except ProcessLookupError:pass
        else:self.fail('owned failure-process PID still alive')
        self.assertFalse(Path(value['staging']).exists())

    def test_cancel_timeout_and_failure_clean_real_observed_partial(self):
        for action in ['cancel','timeout','close','failure']:
            with self.subTest(action=action):
                sub=self.outdir/action;sub.mkdir();old=self.outdir;self.outdir=sub
                try:
                    shim,marker=self.shim('fail' if action=='failure' else 'hang')
                    cfg=config(ffmpeg=shim);cfg['media_backend']['accurate_reencode_timeout_ms']=3000 if action=='timeout' else 10000
                    e=self.engine(cfg);out=sub/'result.mp4';rc,submitted=e.submit(accurate_request(self.files['cfr'],out));self.assertEqual(rc,0)
                    if action=='failure':
                        final=e.terminal(submitted['job_id']);self.assertEqual(final['state'],'failed');self.assertFalse(out.exists())
                        self.assertTrue(marker.exists());value=json.loads(marker.read_text());self.assert_reaped(value);self.assertEqual(e.destroy(),0)
                        self.observations.setdefault('physical_partial_cleanup',[]).append({'action':action,'observation':value,'final':final,'staging_removed':True,'owned_pid_reaped':True});continue
                    value=self.observe_marker(e,submitted['job_id'],marker);start=time.monotonic()
                    if action=='close':self.assertEqual(e.destroy(),-7,'active C ABI destroy must retain BUSY handle')
                    if action in ['cancel','close']:self.assertEqual(e.cancel(submitted['job_id']),0)
                    final=e.terminal(submitted['job_id'],seconds=10);elapsed=time.monotonic()-start
                    self.assertEqual(final['state'],'failed' if action=='timeout' else 'cancelled',final)
                    if action=='timeout':self.assertEqual(final['error']['code'],'TIMEOUT')
                    else:self.assertLessEqual(elapsed,5)
                    self.assertEqual(e.destroy(),0);self.assert_reaped(value);self.assertFalse(out.exists())
                    self.observations.setdefault('physical_partial_cleanup',[]).append({'action':action,'observation':value,'elapsed_after_marker_seconds':elapsed,'final':final,'staging_removed':True,'owned_pid_reaped':True})
                finally:self.outdir=old

    def test_validation_rejects_successfully_encoded_silent_audio_and_wrong_chroma(self):
        for mode in ['mute','swapuv']:
            with self.subTest(mode=mode):
                directory=self.outdir/mode;directory.mkdir();marker=directory/'mutation.json';shim=directory/'mutating-ffmpeg.py'
                source = ('#!'+sys.executable+'\nimport json,os,pathlib,subprocess,sys\n'
                    +'args=sys.argv[1:];real='+repr(FFMPEG)+';mode='+repr(mode)+';marker=pathlib.Path('+repr(str(marker))+')\n'
                    +'if "libx264" in args and "-i" in args and args[-1].endswith(".mp4"):\n'
                    +' p=subprocess.run([real,*args]);\n if p.returncode:sys.exit(p.returncode)\n'
                    +' stage=pathlib.Path(args[-1]);changed=stage.parent/"mutated.mp4"\n'
                    +' common=[real,"-nostdin","-v","error","-y","-i",str(stage),"-map","0"]\n'
                    +' extra=["-c:v","copy","-af","volume=0","-c:a","aac","-b:a","96k"] if mode=="mute" else ["-vf","swapuv","-c:v","libx264","-crf","23","-threads","1","-pix_fmt","yuv420p","-color_range","tv","-colorspace","bt709","-color_primaries","bt709","-color_trc","bt709","-c:a","copy"]\n'
                    +' p=subprocess.run([*common,*extra,str(changed)]);marker.write_text(json.dumps({"mode":mode,"mutator_returncode":p.returncode,"argv":[*common,*extra,str(changed)]}));\n if p.returncode:sys.exit(p.returncode)\n os.replace(changed,stage);sys.exit(0)\n'
                    +'os.execv(real,[real,*args])\n')
                ast.parse(source);shim.write_text(source);shim.chmod(0o700)
                e=self.engine(config(ffmpeg=shim));out=directory/'out.mp4'
                done=self.run_job(e,accurate_request(self.files['cfr'],out),'failed','OUTPUT_VALIDATION_FAILED')
                self.assertTrue(marker.exists());mutation=json.loads(marker.read_text());self.assertEqual(mutation['mutator_returncode'],0)
                self.assertFalse(out.exists());self.assertEqual(list(directory.glob('.mediastudio-*')),[])
                self.observations.setdefault('actual_encoded_mutation_rejections',[]).append({'mutation':mutation,'final':done})

    def test_cli_accurate_and_copy_profiles_use_core(self):
        cli=Path(OPTIONS.library).parent/'media-cli'
        for mode in ['trim-accurate','trim-copy-v1']:
            out=self.outdir/(mode+'.mp4');p=self.commands.run([cli,mode,self.files['cfr'],out,'1100000','2600000'],timeout=120)
            value=json.loads(p.stdout);self.assertTrue(out.is_file());self.assertEqual(value['final']['state'],'succeeded')
            if mode=='trim-accurate':self.physical(self.files['cfr'],out,value['final'])
            else:self.assertFalse(value['final']['result']['reencoded'])
            self.observations.setdefault('cli',[]).append({'command':mode,'stdout':value,'output_sha256':digest(out)})
        out=self.outdir/'badcli.mp4'
        p=self.commands.run([cli,'trim-accurate',self.files['bad'],out,'0','1000000'],expected=None)
        self.assertNotEqual(p.returncode,0);self.assertFalse(out.exists())


def main():
    global OPTIONS
    parser=argparse.ArgumentParser();parser.add_argument('--library',type=Path,required=True);parser.add_argument('--output',type=Path,default=HERE/'evidence/media-run.json');parser.add_argument('--only-fixture')
    OPTIONS,rest=parser.parse_known_args();OPTIONS.library=OPTIONS.library.resolve(strict=True)
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(Media)
    if rest:suite=unittest.defaultTestLoader.loadTestsFromNames(rest,module=sys.modules[__name__])
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    summary={'result':'PASS' if result.wasSuccessful() else 'FAIL','tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors)}
    report(OPTIONS.output.with_name(OPTIONS.output.stem+'-summary.json'),summary);raise SystemExit(not result.wasSuccessful())


if __name__=='__main__':main()
