#!/usr/bin/env python3
"""Build the fixed FFmpeg/x264 local-development pair from supplied archives.

This uses GPL components and does not make a distribution-compliance decision.
Archives are downloaded separately from upstream and are not added to Git.
"""
import argparse
import hashlib
import json
import os
import subprocess
import tarfile
from pathlib import Path

FFMPEG_VERSION = '7.1.5'
FFMPEG_SHA = 'de668509caf9e35e3cd162473441fdb29538c6d96ed080292b3cf9e6fc5d558f'
X264_SHA = 'd053c9d86988d6bc78237ca5205865c5ddf99c98ef4cd9927eec8f6d388f6dd9'
X264_REV = '31e19f92f00c7003fa115047ce50978bc98c3a0d'
X264_PATCH = Path(__file__).resolve().parent / 'patches/x264-31e19f92-param-cleanup.patch'
X264_PATCH_SHA = '736368e6444002409a8cc88017c79776c107ee59a642db2b695479294a21977d'
X264_STAGE_PATCH = Path(__file__).resolve().parent / 'patches/x264-31e19f92-constructor-cleanup-combined.patch'
X264_STAGE_PATCH_SHA = '7afd0b624a5b1ccec94372eceef282767e0a278e0b01e9085ed58e2ac59b2016'
X264_LATE_PATCH = Path(__file__).resolve().parent / 'patches/x264-31e19f92-late-abort.patch'
X264_LATE_PATCH_SHA = 'ae9fd64932467d5c12442c1bc49f387239859385d3a2b6f3a543e3ef9ea28e69'
X264_OPTIONAL_PATCH = Path(__file__).resolve().parent / 'patches/x264-31e19f92-optional-cleanup.patch'
X264_OPTIONAL_PATCH_SHA = 'd751b38801d23afbfa060872063fac4a1c8ad152450b090719f8e7c9e090b747'
X264_MBAFF_PATCH = Path(__file__).resolve().parent / 'patches/x264-31e19f92-mbaff-boundary.patch'
X264_MBAFF_PATCH_SHA = '45320a71744ab9e01444cf4e91c6f7e85aa15930c2e1225386d365f265c4789d'
X264_DERIVATIVE = X264_REV + '+mediastudio-open-cleanup-5'


def sha256(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def run(args, *, cwd, env=None):
    subprocess.run([str(a) for a in args], cwd=cwd, env=env, check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ffmpeg-archive', type=Path, required=True)
    parser.add_argument('--x264-archive', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path, required=True, help='new, empty build directory')
    parser.add_argument('--jobs', type=int, default=4)
    args = parser.parse_args()
    if not 1 <= args.jobs <= 32:
        parser.error('--jobs must be between 1 and 32')
    for archive, expected in ((args.ffmpeg_archive, FFMPEG_SHA), (args.x264_archive, X264_SHA)):
        if sha256(archive) != expected:
            parser.error(f'archive checksum mismatch: {archive.name}')
    if sha256(X264_PATCH) != X264_PATCH_SHA:
        parser.error('x264 defensive patch checksum mismatch')
    if sha256(X264_STAGE_PATCH) != X264_STAGE_PATCH_SHA:
        parser.error('x264 staged cleanup patch checksum mismatch')
    if sha256(X264_LATE_PATCH) != X264_LATE_PATCH_SHA:
        parser.error('x264 late abort patch checksum mismatch')
    if sha256(X264_OPTIONAL_PATCH) != X264_OPTIONAL_PATCH_SHA:
        parser.error('x264 optional cleanup patch checksum mismatch')
    if sha256(X264_MBAFF_PATCH) != X264_MBAFF_PATCH_SHA:
        parser.error('x264 MBAFF boundary patch checksum mismatch')
    work = args.work_dir.absolute()
    work.mkdir(parents=True, exist_ok=False)
    for archive in (args.ffmpeg_archive, args.x264_archive):
        with tarfile.open(archive) as source:
            source.extractall(work, filter='data')
    x264 = work / ('x264-' + X264_REV)
    ffmpeg = work / ('ffmpeg-' + FFMPEG_VERSION)
    original_encoder_sha = sha256(x264 / 'encoder/encoder.c')
    run(['patch', '--batch', '--forward', '-p1', '-i', X264_PATCH], cwd=x264)
    patched_encoder_sha = sha256(x264 / 'encoder/encoder.c')
    expected_preimages = {'common/frame.c': 'daca9eb48564ad960249ba079c980ecd206febe0ae9edb891b5d5dadef802262', 'common/threadpool.c': '63f9ac9d5b7d3855985f33bc1b58f5ded39d152c4a3a0e6d331a60f71666661f', 'encoder/lookahead.c': 'df8323cef7a7a17af4169c7896ccb991c71f9e5a7256bdf0869bd0f2e0b11e66', 'encoder/encoder.c': 'b7eae7ce451a2aef66162b974e520643283bc2b8cc6aac157786f295ced30f35', 'common/set.c': '77b5cc6ed05b87296ef999ecf69185b2dd4530fac58e8e7990682db9c279d998'}
    staged_preimages = {name: sha256(x264 / name) for name in expected_preimages}
    if staged_preimages != expected_preimages:
        parser.error('x264 constructor source preimages differ from independent review')
    run(['patch', '--batch', '--forward', '-p1', '-i', X264_STAGE_PATCH], cwd=x264)
    staged_postimages = {name: sha256(x264 / name) for name in staged_preimages}
    expected_postimages = {'common/frame.c': 'f8f073f2e9834eef70ff820d97ab268f77a984345c94ff3423f2082b4d040296', 'common/threadpool.c': '8cb10a97978cb3992f07f85f12be55c94bbab821d09d3815eeaaa78678c983a4', 'encoder/lookahead.c': '12c19bf4f485429f5ca6d5aa8e9c138ce60e7160174ec9bb2055750bb48bc2e4', 'encoder/encoder.c': '6ce18adeb8c7cd79de0faff084077adc83785120f4079b88269365f727406427', 'common/set.c': '3f1794d375cbe943b36099978b3083865b1b0136f76a3f97dcccc151d1783a54'}
    if staged_postimages != expected_postimages:
        parser.error('x264 constructor source postimages differ from independent review')
    expected_late_preimages = {'common/frame.c': 'f8f073f2e9834eef70ff820d97ab268f77a984345c94ff3423f2082b4d040296', 'encoder/encoder.c': '6ce18adeb8c7cd79de0faff084077adc83785120f4079b88269365f727406427', 'common/macroblock.c': 'f2ad587f7c4d32900c98118a3bfb8cbe40fdb4420fb66666d2bc141753cdd80f', 'encoder/ratecontrol.c': 'c03cb0025d983ceee142644ba4328da5c59f53f2bd0f83bd6225dbaa6a31f87c', 'encoder/ratecontrol.h': '98b59316641bd3367e8c79082ef0b61b3c359fad185234a80d823417809429b4'}
    late_preimages = {name: sha256(x264 / name) for name in expected_late_preimages}
    if late_preimages != expected_late_preimages:
        parser.error('x264 late abort preimages differ from independent review')
    run(['patch', '--batch', '--forward', '-p1', '-i', X264_LATE_PATCH], cwd=x264)
    late_postimages = {name: sha256(x264 / name) for name in late_preimages}
    expected_late_postimages = {'common/frame.c': '026329cc5214eeaaaa8246b5e498218dfb4fef06efa0dd11c60ed4d3207818b5', 'encoder/encoder.c': '4e450e97cfe96bd2906c0496d2d51da4b4add50cbddd80a3500a9e811d229027', 'common/macroblock.c': '3eebb4c53c5f8be0da970955d8d24bce6b2fbd22dea10f94021e25b52ef5dcda', 'encoder/ratecontrol.c': '0d88ea0267ae7cbda5602f0ff6da0fd2af63394bffc33a8cfd4b38933535990a', 'encoder/ratecontrol.h': '30a6124875fed08e65943852458fb9f2a18d8d341f138da97c41a1c42846ca3d'}
    if late_postimages != expected_late_postimages:
        parser.error('x264 late abort postimages differ from independent review')
    expected_optional_preimages = {'encoder/ratecontrol.c': '0d88ea0267ae7cbda5602f0ff6da0fd2af63394bffc33a8cfd4b38933535990a', 'encoder/ratecontrol.h': '30a6124875fed08e65943852458fb9f2a18d8d341f138da97c41a1c42846ca3d', 'encoder/encoder.c': '4e450e97cfe96bd2906c0496d2d51da4b4add50cbddd80a3500a9e811d229027', 'common/opencl.c': '8e3280cb03e021cb178244f0be17a0049f011056dcc3507295eaae1bcafd84cd', 'common/opencl.h': '8b34d77000cb2a1e334b5292449ef4d9697638974e5d0b0a5d5c3d8210d0340e'}
    optional_preimages = {name: sha256(x264 / name) for name in expected_optional_preimages}
    if optional_preimages != expected_optional_preimages:
        parser.error('x264 optional preimages differ from independent review')
    run(['patch', '--batch', '--forward', '-p1', '-i', X264_OPTIONAL_PATCH], cwd=x264)
    optional_postimages = {name: sha256(x264 / name) for name in optional_preimages}
    expected_optional_postimages = {'encoder/ratecontrol.c': '6f83927bd38641cbcebb31034f6fdf08754d7afc3cdaf17c5a503f89258aaac6', 'encoder/ratecontrol.h': 'db06b14756bbb01a94b4dd1097b1af9249bb9c3dbe650ad5b1855022d3c28002', 'encoder/encoder.c': '66a75649ac4a08a277993f278e61016190baddf89dcbae1361be996169e53776', 'common/opencl.c': '2722ad611815d92ed08d1527fd7cca629ac338cd7c4d5255d3e6a8e569f2e191', 'common/opencl.h': '0add993fe38874c78dc93bf2b8d90b34db488a49083176db75f135e33acf76c2'}
    if optional_postimages != expected_optional_postimages:
        parser.error('x264 optional postimages differ from independent review')
    expected_mbaff_preimages = {'common/pixel.c': '1e648892bc1843e91158c327abad61156bfe6cf780175844a15e0f2d59274366', 'common/macroblock.c': '3eebb4c53c5f8be0da970955d8d24bce6b2fbd22dea10f94021e25b52ef5dcda'}
    mbaff_preimages = {name: sha256(x264 / name) for name in expected_mbaff_preimages}
    if mbaff_preimages != expected_mbaff_preimages:
        parser.error('x264 MBAFF source preimage differs from independent review')
    run(['patch', '--batch', '--forward', '-p1', '-i', X264_MBAFF_PATCH], cwd=x264)
    mbaff_postimages = {name: sha256(x264 / name) for name in mbaff_preimages}
    expected_mbaff_postimages = {'common/pixel.c': 'c3e04b09f46e6fed9df87ecb918b0433bbf7f7a02b646215ae0c925cb2c5b44a', 'common/macroblock.c': 'dbe8850b033c04648a4fd9545f796b00225e3234332f1190ebd7e9bd16ca3225'}
    if mbaff_postimages != expected_mbaff_postimages:
        parser.error('x264 MBAFF source postimage differs from independent review')
    final_source_files = {**staged_postimages, **late_postimages, **optional_postimages, **mbaff_postimages}
    x264_install = work / 'x264-install'
    install = work / 'ffmpeg-install'
    run(['./configure', f'--prefix={x264_install}', '--enable-static', '--disable-cli', '--disable-opencl'], cwd=x264)
    run(['make', f'-j{args.jobs}'], cwd=x264)
    run(['make', 'install'], cwd=x264)
    env = os.environ.copy()
    env['PKG_CONFIG_PATH'] = str(x264_install / 'lib/pkgconfig')
    options = [f'--prefix={install}', '--disable-autodetect', '--disable-doc', '--disable-debug',
               '--disable-ffplay', '--disable-network', '--enable-gpl', '--enable-libx264',
               '--disable-shared', '--enable-static', '--pkg-config-flags=--static']
    run(['./configure', *options], cwd=ffmpeg, env=env)
    run(['make', f'-j{args.jobs}'], cwd=ffmpeg)
    run(['make', 'install'], cwd=ffmpeg)
    files = [install / 'bin/ffmpeg', install / 'bin/ffprobe', x264_install / 'lib/libx264.a']
    info = {'ffmpeg_version': FFMPEG_VERSION,
            'ffmpeg_source_url': f'https://ffmpeg.org/releases/ffmpeg-{FFMPEG_VERSION}.tar.xz',
            'ffmpeg_source_sha256': FFMPEG_SHA, 'x264_source_sha256': X264_SHA,
            'x264_revision': X264_REV, 'ffmpeg_options': options,
            'x264_derivative': X264_DERIVATIVE,
            'x264_additional_patches': [{'path': str(X264_STAGE_PATCH), 'sha256': X264_STAGE_PATCH_SHA,
                                        'source': 'MediaStudio reviewed local defensive resource rollback; ADR-0024',
                                        'scope': 'Stage2 transactional sync-list/pool/lookahead construction, exclusive encoder prefix and idle pools; later patch scope is declared separately',
                                        'preimages': staged_preimages, 'postimages': staged_postimages},
                                       {'path': str(X264_LATE_PATCH), 'sha256': X264_LATE_PATCH_SHA,
                                        'source': 'MediaStudio reviewed staged parent/frame/macroblock/no-stats RC abort; ADR-0025',
                                        'scope': 'Stage3 parent/frame/macroblock and bounded RC abort; subsequent optional patch scope is declared separately; full CVE unresolved',
                                        'preimages': late_preimages, 'postimages': late_postimages},
                                       {'path': str(X264_OPTIONAL_PATCH), 'sha256': X264_OPTIONAL_PATCH_SHA,
                                        'source': 'MediaStudio reviewed optional RC/OpenCL and sliced wait order; ADR-0026',
                                        'scope': 'Library-owned optional RC no-rename/no-unlink disposal, OpenCL no-context rollback/normal unmap and sliced scratch ordering; active-context/caller-callback/temp-identity contracts remain',
                                        'preimages': optional_preimages, 'postimages': optional_postimages},
                                       {'path': str(X264_MBAFF_PATCH), 'sha256': X264_MBAFF_PATCH_SHA,
                                        'source': 'MediaStudio reviewed threadslice-local MBAFF top-neighbor availability; ADR-0027',
                                        'scope': 'Ignore top field-mode bias and dead top-position reads outside current threadslice; same threads, MBtree, encoder scheduling and non-sliced behavior; no full CVE resolution claim',
                                        'preimages': mbaff_preimages, 'postimages': mbaff_postimages}],
            'x264_final_source_files': final_source_files,
            'x264_local_patch': {'path': str(X264_PATCH), 'sha256': X264_PATCH_SHA,
                                 'source': 'https://code.videolan.org/videolan/x264/-/merge_requests/183',
                                 'scope': 'Internal parameter ownership cleanup on encoder-open failure only; upstream MR unmerged; no blanket CVE resolution claim',
                                 'original_encoder_sha256': original_encoder_sha,
                                 'patched_encoder_sha256': patched_encoder_sha},
            'build_script_sha256': sha256(Path(__file__)),
            'x264_options': [f'--prefix={x264_install}', '--enable-static', '--disable-cli', '--disable-opencl'],
            'source_archives': {str(p.absolute()): sha256(p) for p in
                                [args.ffmpeg_archive, args.x264_archive]},
            'artifact_paths': {p.name: str(p) for p in files},
            'artifacts': {p.name: sha256(p) for p in files}}
    (work / 'build-manifest.json').write_text(json.dumps(info, indent=2) + '\n')
    print('Development FFmpeg built:', install / 'bin')


if __name__ == '__main__':
    main()
