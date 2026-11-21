#!/usr/bin/env python3
"""Isolate the reviewed wait barrier from all optional RC/OpenCL changes.

Offline locked v4 three-patch source vs same source plus wait patch, raw-only
x264 CLI. Ordinary valid synthetic inputs only; no failure or exploit tests.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REV = '31e19f92f00c7003fa115047ce50978bc98c3a0d'
ARCHIVE = ROOT / 'apps/macos/build/MAC-005/sources/x264-31e19f92.tar.gz'
ARCHIVE_SHA = 'd053c9d86988d6bc78237ca5205865c5ddf99c98ef4cd9927eec8f6d388f6dd9'
PATCHES = [
    ('apps/macos/patches/x264-31e19f92-param-cleanup.patch', '736368e6444002409a8cc88017c79776c107ee59a642db2b695479294a21977d'),
    ('apps/macos/patches/x264-31e19f92-constructor-cleanup-combined.patch', '7afd0b624a5b1ccec94372eceef282767e0a278e0b01e9085ed58e2ac59b2016'),
    ('apps/macos/patches/x264-31e19f92-late-abort.patch', 'ae9fd64932467d5c12442c1bc49f387239859385d3a2b6f3a543e3ef9ea28e69'),
]
WAIT = ROOT / 'docs/qa-002/evidence/x264-optional-abort-20261116/repeat10-wait-order-proposal.patch'
WAIT_SHA = '11be84d919dbb646e11c2c2c1fdf30d76b92cba336e57fa118c7a8e76e1d6aad'
V4_SOURCES = {
    'common/frame.c': '026329cc5214eeaaaa8246b5e498218dfb4fef06efa0dd11c60ed4d3207818b5',
    'common/set.c': '3f1794d375cbe943b36099978b3083865b1b0136f76a3f97dcccc151d1783a54',
    'common/threadpool.c': '8cb10a97978cb3992f07f85f12be55c94bbab821d09d3815eeaaa78678c983a4',
    'encoder/lookahead.c': '12c19bf4f485429f5ca6d5aa8e9c138ce60e7160174ec9bb2055750bb48bc2e4',
    'encoder/encoder.c': '4e450e97cfe96bd2906c0496d2d51da4b4add50cbddd80a3500a9e811d229027',
    'common/macroblock.c': '3eebb4c53c5f8be0da970955d8d24bce6b2fbd22dea10f94021e25b52ef5dcda',
    'encoder/ratecontrol.c': '0d88ea0267ae7cbda5602f0ff6da0fd2af63394bffc33a8cfd4b38933535990a',
    'encoder/ratecontrol.h': '30a6124875fed08e65943852458fb9f2a18d8d341f138da97c41a1c42846ca3d',
}
WAIT_POST = '4d89fec3f710ccaa96297a593a3106f87bed88af3da3aaf094044112421a8e1d'
INPUTS = {
    '42010': ('/private/tmp/mediastudio-x264-late-abort/slice10-fixed-input/fixed.yuv', 'facc7f5532e05cc5f391a3c5fa26a47874c6d8507993d81298a21f5eabbf938e'),
    '44410': ('/private/tmp/mediastudio-x264-repeat10/fixed44410.yuv', '29554835cb96e2ea2774c6cf04c77303a528e8172ae41f6231f373bc67f2c766'),
    '4208': ('/private/tmp/mediastudio-x264-repeat10/fixed8.yuv', 'b3baf4fe28a4cf5638d3a8ef967b22dc9a8f5c85950592990fcc5e9211cc0699'),
}
CASES = [('42010', '42010', 'i420', 10, False, False),
         ('44410', '44410', 'i444', 10, False, False),
         ('interlaced10', '42010', 'i420', 10, True, False),
         ('cpu-none8', '4208', 'i420', 8, False, True)]
OPTIONS = ['--enable-static', '--disable-opencl', '--disable-lavf', '--disable-swscale', '--disable-ffms', '--disable-avs']


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work', required=True, type=Path)
    p.add_argument('--report', required=True, type=Path)
    p.add_argument('--admission', required=True, type=Path)
    p.add_argument('--jobs', type=int, default=2)
    a = p.parse_args()
    assert 1 <= a.jobs <= 4
    admission = json.loads(a.admission.read_text())
    assert admission['decision'] == 'APPROVE_ISOLATED_RAW_CLI_NORMAL_COMPARISON'
    assert admission['script_sha256'] == sha(__file__)
    assert sha(ARCHIVE) == ARCHIVE_SHA and sha(WAIT) == WAIT_SHA
    for relative, expected in PATCHES:
        assert sha(ROOT / relative) == expected
    for name, expected in INPUTS.values():
        assert sha(name) == expected
    assert not a.report.exists()
    a.work.mkdir(parents=True, exist_ok=False)
    a.report.parent.mkdir(parents=True, exist_ok=True)
    report = {'scope': __doc__, 'script_sha256': sha(__file__), 'admission_sha256': sha(a.admission),
              'archive_sha256': ARCHIVE_SHA, 'patches': PATCHES, 'wait_patch_sha256': WAIT_SHA,
              'inputs': {}, 'commands': [], 'builds': {}, 'results': {}}
    def save():
        a.report.write_text(json.dumps(report, indent=2) + '\n')
    def run(argv, cwd=None, env=None, binary=False, timeout=180):
        started = time.monotonic()
        r = subprocess.run(list(map(str, argv)), cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=timeout)
        index = len(report['commands'])
        stderr = r.stderr.decode(errors='replace')
        item = {'argv': list(map(str, argv)), 'cwd': str(cwd) if cwd else None,
                'exit': r.returncode, 'elapsed_seconds': time.monotonic() - started, 'stderr': stderr}
        if binary:
            item['stdout_bytes'] = len(r.stdout)
            item['stdout_sha256'] = hashlib.sha256(r.stdout).hexdigest()
        else:
            log = a.work / f'command-{index:03d}.stdout.log'
            log.write_bytes(r.stdout)
            item['stdout_log'] = str(log)
            item['stdout_sha256'] = sha(log)
        report['commands'].append(item)
        save()
        assert r.returncode == 0, item
        return r.stdout if binary else r.stdout.decode(errors='replace')
    compiler = run(['xcrun', '--find', 'clang']).strip()
    sdkroot = run(['xcrun', '--sdk', 'macosx', '--show-sdk-path']).strip()
    report['compiler'] = {'path': compiler, 'version': run([compiler, '--version']), 'sdkroot': sdkroot}
    decoder = Path('/opt/homebrew/bin/ffmpeg')
    probe = Path('/opt/homebrew/bin/ffprobe')
    report['decoder_sha256'] = sha(decoder)
    report['probe_sha256'] = sha(probe)
    frozen_inputs = a.work / 'inputs'
    frozen_inputs.mkdir()
    for key, (name, expected) in INPUTS.items():
        target = frozen_inputs / f'{key}.yuv'
        shutil.copyfile(name, target)
        assert sha(target) == expected
        report['inputs'][key] = {'path': str(target), 'sha256': expected}
    env = dict(os.environ, CC=compiler, SDKROOT=sdkroot)
    for variant in ['v4', 'wait-only']:
        folder = a.work / variant
        folder.mkdir()
        with tarfile.open(ARCHIVE) as archive:
            archive.extractall(folder, filter='data')
        source = folder / ('x264-' + REV)
        for relative, _ in PATCHES:
            run(['patch', '--batch', '--forward', '-p1', '-i', ROOT / relative], cwd=source)
        assert {n: sha(source/n) for n in V4_SOURCES} == V4_SOURCES
        if variant == 'wait-only':
            run(['patch', '--batch', '--forward', '-p1', '-i', WAIT], cwd=source)
        expected = dict(V4_SOURCES)
        if variant == 'wait-only': expected['encoder/encoder.c'] = WAIT_POST
        assert {n: sha(source/n) for n in expected} == expected
        prefix = folder / 'install'
        configure = ['./configure', f'--prefix={prefix}', *OPTIONS]
        run(configure, cwd=source, env=env)
        config = (source/'config.h').read_text()
        for key in ['HAVE_OPENCL', 'HAVE_LAVF', 'HAVE_SWSCALE', 'HAVE_FFMS', 'HAVE_AVS']:
            assert f'#define {key} 0' in config
        run(['make', f'-j{a.jobs}'], cwd=source, env=env, timeout=300)
        run(['make', 'install'], cwd=source, env=env)
        cli = source / 'x264'
        links = run(['otool', '-L', cli])
        dependencies = [x.strip().split(' (',1)[0] for x in links.splitlines()[1:]]
        assert all(x.startswith(('/usr/lib/', '/System/Library/')) for x in dependencies)
        report['builds'][variant] = {'source': str(source), 'source_files': expected,
            'configure': configure, 'config_h_sha256': sha(source/'config.h'), 'cli': str(cli),
            'cli_sha256': sha(cli), 'libx264_sha256': sha(source/'libx264.a'), 'dependencies': dependencies}
        results = report['results'][variant] = {}
        for case, input_key, csp, depth, interlaced, noasm in CASES:
            pix = f'yuv{csp[1:]}p' + ('10le' if depth == 10 else '')
            rows = results[case] = []
            for repeat in range(3):
                output = folder / f'{case}-{repeat}.h264'
                cmd = [cli, '--no-progress', '--demuxer', 'raw', '--input-res', '320x256', '--fps', '30',
                    '--frames', '30', '--input-depth', str(depth), '--output-depth', str(depth),
                    '--input-csp', csp, '--output-csp', csp, '--threads', '4', '--sliced-threads',
                    '--sync-lookahead', '0', '--lookahead-threads', '2', '--preset', 'medium', '--crf', '23']
                if interlaced: cmd += ['--tff']
                if noasm: cmd += ['--no-asm']
                cmd += ['-o', output, report['inputs'][input_key]['path']]
                run(cmd, cwd=folder)
                options = output.read_bytes().split(b' options: ',1)[1].split(b'\x00',1)[0].decode('ascii')
                actual = dict(re.findall(r'(\w+)=([^ ]+)', options))
                assert actual['threads'] == '4' and actual['sliced_threads'] == '1'
                assert actual['mbtree'] == '1' and actual['rc'] == 'crf'
                assert actual['interlaced'] == ('tff' if interlaced else '0')
                meta = json.loads(run([probe, '-v', 'error', '-count_frames', '-show_streams', '-of', 'json', output]))['streams']
                assert len(meta) == 1 and meta[0]['pix_fmt'] == pix and meta[0]['nb_read_frames'] == '30'
                assert meta[0]['field_order'] in ('tt','tb') if interlaced else meta[0]['field_order'] == 'progressive'
                data = run([decoder, '-nostdin', '-v', 'error', '-xerror', '-threads', '1', '-i', output,
                    '-map', '0:v:0', '-pix_fmt', pix, '-f', 'rawvideo', '-'], binary=True)
                frame_bytes = 320*256*3*(2 if depth==10 else 1)//(1 if csp=='i444' else 2)
                assert len(data) == 30*frame_bytes
                rows.append({'repeat': repeat, 'path': str(output), 'encoded_sha256': sha(output),
                    'decoded_sha256': hashlib.sha256(data).hexdigest(), 'actual_encoder_options': actual,
                    'stream': meta[0], 'frame_sha256': [hashlib.sha256(data[k:k+frame_bytes]).hexdigest()
                        for k in range(0,len(data),frame_bytes)]})
                save()
    report['comparisons'] = {variant: {case: {'encoded_unique': len({r['encoded_sha256'] for r in rows}),
        'decoded_unique': len({r['decoded_sha256'] for r in rows})} for case,rows in results.items()}
        for variant,results in report['results'].items()}
    assert all(x['encoded_unique'] == x['decoded_unique'] == 1 for x in report['comparisons']['wait-only'].values())
    assert report['builds']['v4']['dependencies'] == report['builds']['wait-only']['dependencies']
    for key, (name, expected) in INPUTS.items():
        assert sha(name) == expected and sha(report['inputs'][key]['path']) == expected
    report['result'] = 'PASS_ISOLATED_WAIT_ONLY_NORMAL_REPEATABILITY'
    report['limits'] = 'Only normal repeated inputs; old v4 variation retained; no universal determinism or CVE resolution claim; production still OpenCL disabled'
    save()
    print(json.dumps(report['comparisons']))

if __name__ == '__main__':
    main()
