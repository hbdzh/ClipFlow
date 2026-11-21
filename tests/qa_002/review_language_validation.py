#!/usr/bin/env python3
"""Real encodes plus explicit metadata fault injection for language validation."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'tests/core_004'))
from support import API, Commands, configuration


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--library', type=Path, required=True)
    p.add_argument('--ffmpeg', type=Path, required=True)
    p.add_argument('--ffprobe', type=Path, required=True)
    p.add_argument('--work', type=Path, required=True)
    p.add_argument('--report', type=Path, required=True)
    a = p.parse_args(); a.work.mkdir(parents=True, exist_ok=False)
    commands = Commands(); rows = []
    src = a.work / 'known-language.mp4'
    commands.run([a.ffmpeg, '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=30:duration=1',
                  '-f', 'lavfi', '-i', 'sine=frequency=570:sample_rate=48000:duration=1',
                  '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-bf', '2', '-g', '30',
                  '-c:a', 'aac', '-metadata:s:a:0', 'language=eng', '-movflags', '+faststart', src])
    src_hash = digest(src); core_hash = digest(a.library); api = API(a.library)
    for strategy in ['stream_copy', 'accurate_reencode']:
        for fault in ['none', 'wrong-known', 'missing-known']:
            own = a.work / f'{strategy}-{fault}'; own.mkdir()
            probe = own / 'ffprobe'; event = own / 'metadata-fault-events.jsonl'
            script = f'''#!{sys.executable}
import json,pathlib,subprocess,sys
args=sys.argv[1:]
p=subprocess.run([{str(a.ffprobe)!r},*args],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
data=p.stdout
if p.returncode==0 and '-show_streams' in args and args[-1].endswith('/output.mp4') and {fault!r} != 'none':
 value=json.loads(data)
 for stream in value.get('streams',[]):
  if stream.get('codec_type')=='audio':
   tags=stream.setdefault('tags',{{}})
   before=tags.get('language')
   if {fault!r}=='wrong-known':tags['language']='fra'
   else:tags.pop('language',None)
   with open({str(event)!r},'a') as f:f.write(json.dumps({{'source_language':before,'replacement':tags.get('language'),'path':args[-1]}})+'\\n')
 data=json.dumps(value).encode()
sys.stdout.buffer.write(data);sys.stderr.buffer.write(p.stderr);sys.exit(p.returncode)
'''
            probe.write_text(script); probe.chmod(0o700)
            engine = api.create(configuration(core004=False, ffmpeg=a.ffmpeg, ffprobe=probe))
            out = own / 'result.mp4'
            try:
                request = {'schema_version': 0, 'operation': 'trim', 'source': {'uri': src.as_uri()},
                           'destination': {'uri': out.as_uri()}, 'range': {'start_us': 0, 'end_us': 1000000},
                           'strategy': strategy, 'profile': 'mp4_h264_copy_v1' if strategy == 'stream_copy' else 'mp4_h264_standard_v1',
                           'stream_selection': 'all_supported', 'allow_reencode_fallback': False}
                rc, receipt = engine.submit(request); assert rc == 0 and receipt.get('job_id'), (rc, receipt)
                result = engine.wait(receipt['job_id'])
                if fault == 'none':
                    assert result['state'] == 'succeeded' and out.exists(), result
                    meta = json.loads(commands.run([a.ffprobe, '-v', 'error', '-show_streams', '-of', 'json', out]).stdout)
                    audio = [s for s in meta['streams'] if s['codec_type'] == 'audio']
                    assert len(audio) == 1 and audio[0]['tags']['language'] == 'eng'
                    commands.run([a.ffmpeg, '-nostdin', '-v', 'error', '-xerror', '-i', out, '-map', '0', '-f', 'null', '-'])
                else:
                    assert event.exists() and event.read_text().strip(), 'Metadata fault did not execute'
                    assert result['state'] == 'failed' and result['error']['code'] == 'OUTPUT_VALIDATION_FAILED', result
                    assert not out.exists() and not list(own.glob('.mediastudio*')), list(own.iterdir())
                assert digest(src) == src_hash
                rows.append({'name': f'{strategy}-{fault}', 'result': 'PASS', 'snapshot': result,
                             'fault_events': event.read_text().splitlines() if event.exists() else [],
                             'source_sha256': src_hash, 'output_sha256': digest(out) if out.exists() else None,
                             'probe_stub_sha256': digest(probe)})
            finally:
                assert engine.destroy() == 0
    assert api.outputs == api.frees and digest(a.library) == core_hash
    report = {'result': 'PASS', 'scope': 'Independent Core C ABI real FFmpeg copy/reencode controls plus explicit output ffprobe metadata faults; faults test Core validation, not fabricated media corruption or GUI',
              'core_sha256': core_hash, 'core_file_unchanged': True, 'checks': rows, 'commands': commands.records,
              'ffi_outputs': api.outputs, 'ffi_frees': api.frees, 'qa_script_sha256': digest(Path(__file__)), 'argv': sys.argv}
    a.report.parent.mkdir(parents=True, exist_ok=True); a.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'result': 'PASS', 'cases': len(rows), 'ffi_outputs': api.outputs, 'ffi_frees': api.frees}))


if __name__ == '__main__':
    main()
