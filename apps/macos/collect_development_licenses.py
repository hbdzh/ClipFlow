#!/usr/bin/env python3
"""Collect license texts for a local development bundle; not a release audit."""
import argparse
import hashlib
import json
import shutil
import tomllib
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    ort_input = parser.add_mutually_exclusive_group(required=True)
    ort_input.add_argument('--ort-wheel', type=Path)
    ort_input.add_argument('--ort-notices', type=Path,
                           help='Verified ORT notice directory containing LICENSE and ThirdPartyNotices.txt')
    parser.add_argument('--model-license', type=Path, required=True)
    parser.add_argument('--ffmpeg-source', type=Path, required=True)
    parser.add_argument('--x264-source', type=Path, required=True)
    parser.add_argument('--x264-build-manifest', type=Path,
                        help='Controlled patched build manifest; verifies actual encoder source and includes patch disclosure')
    parser.add_argument('--ort-static-notices', type=Path,
                        help='Reviewed exact controlled-build component notices, including their inventory')
    parser.add_argument('--cargo-registry', type=Path, required=True,
                        help='Cargo registry src directory containing extracted crate directories')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.output
    root.mkdir(parents=True, exist_ok=False)
    ort = root / 'onnxruntime-1.22.1'
    ort.mkdir()
    if args.ort_notices is not None:
        for name in ('LICENSE', 'ThirdPartyNotices.txt'):
            shutil.copy2(args.ort_notices / name, ort / name)
    else:
        with zipfile.ZipFile(args.ort_wheel) as wheel:
            for name in ('LICENSE', 'ThirdPartyNotices.txt'):
                (ort / name).write_bytes(wheel.read('onnxruntime/' + name))
    if args.ort_static_notices:
        if not (args.ort_static_notices / 'inventory.json').is_file():
            raise ValueError('Controlled ORT notices require inventory.json')
        if any(p.is_symlink() for p in args.ort_static_notices.rglob('*')):
            raise ValueError('ORT notice input must not contain symbolic links')
        notice_root = args.ort_static_notices.resolve()
        notice_inventory = json.loads((notice_root / 'inventory.json').read_text())
        declared = set()
        for entry in notice_inventory['files']:
            path = (notice_root / entry['file']).resolve()
            if not path.is_relative_to(notice_root) or entry['file'] in declared:
                raise ValueError('Invalid or duplicate ORT notice inventory path')
            declared.add(entry['file'])
            if path.stat().st_size != entry['bytes'] or hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
                raise ValueError('ORT notice inventory content mismatch: ' + entry['file'])
            if path.name == 'ACTUAL-SOURCE-NOTICES.txt' and len(path.read_text().strip()) < 32:
                raise ValueError('Truncated ORT actual source notice: ' + entry['file'])
        actual = {str(p.relative_to(notice_root)) for p in notice_root.rglob('*')
                  if p.is_file() and p != notice_root / 'inventory.json'}
        if declared != actual:
            raise ValueError('ORT notice inventory does not match all supplied materials')
        shutil.copytree(args.ort_static_notices, ort / 'static-dependencies')
    if args.x264_build_manifest:
        manifest = json.loads(args.x264_build_manifest.read_text())
        patch = Path(__file__).resolve().parent / 'patches/x264-31e19f92-param-cleanup.patch'
        actual_encoder_sha = hashlib.sha256((args.x264_source / 'encoder/encoder.c').read_bytes()).hexdigest()
        actual_patch_sha = hashlib.sha256(patch.read_bytes()).hexdigest()
        final_sources = manifest.get('x264_final_source_files', {})
        expected_encoder_sha = final_sources.get('encoder/encoder.c', manifest['x264_local_patch']['patched_encoder_sha256'])
        if expected_encoder_sha != actual_encoder_sha:
            raise ValueError('x264 disclosure source differs from controlled build')
        if manifest['x264_local_patch']['sha256'] != actual_patch_sha:
            raise ValueError('x264 disclosure patch differs from controlled build')
        shutil.copy2(patch, root / 'x264-local-param-cleanup.patch')
        additional = manifest.get('x264_additional_patches', [])
        if additional:
            known = [
                ('x264-31e19f92-constructor-cleanup-combined.patch', 'x264-local-constructor-cleanup.patch'),
                ('x264-31e19f92-late-abort.patch', 'x264-local-late-abort.patch'),
                ('x264-31e19f92-optional-cleanup.patch', 'x264-local-optional-cleanup.patch'),
                ('x264-31e19f92-mbaff-boundary.patch', 'x264-local-mbaff-boundary.patch'),
            ]
            if len(additional) != len(known):
                raise ValueError('Unexpected x264 additional patch count/order')
            mapped_final = {}
            for entry, (source_name, notice_name) in zip(additional, known):
                extra = Path(__file__).resolve().parent / 'patches' / source_name
                if Path(entry['path']).resolve() != extra:
                    raise ValueError('Unexpected x264 additional patch input/order')
                if hashlib.sha256(extra.read_bytes()).hexdigest() != entry['sha256']:
                    raise ValueError('Additional x264 patch checksum mismatch')
                mapped_final.update(entry['postimages'])
                shutil.copy2(extra, root / notice_name)
            if set(final_sources) != {'encoder/encoder.c', 'common/frame.c', 'common/set.c',
                                      'encoder/lookahead.c', 'common/threadpool.c',
                                      'common/macroblock.c', 'encoder/ratecontrol.c', 'encoder/ratecontrol.h',
                                      'common/opencl.c', 'common/opencl.h', 'common/pixel.c'}:
                raise ValueError('Missing final x264 source mapping')
            for relative, expected in final_sources.items():
                if hashlib.sha256((args.x264_source / relative).read_bytes()).hexdigest() != expected:
                    raise ValueError('Final x264 source mismatch: ' + relative)
            if mapped_final != final_sources:
                raise ValueError('Ordered x264 patch/source mapping mismatch')
        (root / 'x264-local-build.json').write_text(json.dumps({
            'base_revision': manifest['x264_revision'], 'derivative': manifest['x264_derivative'],
            'source_archive_sha256': manifest['x264_source_sha256'],
            'patch': manifest['x264_local_patch'],
            'additional_patches': additional,
            'final_source_files': final_sources,
            'build_manifest_sha256': hashlib.sha256(args.x264_build_manifest.read_bytes()).hexdigest(),
        }, indent=2) + '\n')
    for name, src in {
        'model-umx-MIT.txt': args.model_license,
        'ffmpeg-COPYING.GPLv2': args.ffmpeg_source / 'COPYING.GPLv2',
        'ffmpeg-COPYING.LGPLv2.1': args.ffmpeg_source / 'COPYING.LGPLv2.1',
        'ffmpeg-LICENSE.md': args.ffmpeg_source / 'LICENSE.md',
        'x264-COPYING': args.x264_source / 'COPYING',
    }.items():
        shutil.copy2(src, root / name)
    # FFmpeg's component license document specifically requires the IJG
    # attribution when distributing executables. Preserve the corresponding
    # source headers as well; the aggregate GPL text is not a replacement.
    ijg = ['This software is based in part on the work of the Independent JPEG Group.\n',
           'The following headers are copied unchanged from the supplied FFmpeg source.\n']
    for relative in ('libavcodec/jfdctfst.c', 'libavcodec/jfdctint_template.c',
                     'libavcodec/jrevdct.c'):
        data = (args.ffmpeg_source / relative).read_text()
        if not data.startswith('/*') or '*/' not in data:
            raise ValueError(f'Expected IJG copyright header: {relative}')
        ijg.append('\n--- ' + relative + ' ---\n' + data[:data.index('*/') + 2] + '\n')
    (root / 'ffmpeg-IJG-NOTICE.txt').write_text(''.join(ijg))
    repo = Path(__file__).resolve().parents[2]
    packages = {}
    for lock in (repo / 'core/Cargo.lock', repo / 'core/native-ai-worker/Cargo.lock'):
        for entry in tomllib.loads(lock.read_text())['package']:
            if not entry.get('source', '').startswith('registry+'):
                continue
            key = f"{entry['name']}-{entry['version']}"
            if key in packages:
                continue
            source = args.cargo_registry / key
            manifest = tomllib.loads((source / 'Cargo.toml').read_text())['package']
            texts = [p for p in source.iterdir() if p.is_file()
                     and p.name.upper().startswith(('LICENSE', 'COPYING', 'NOTICE'))]
            if not texts:
                raise ValueError(f'No standalone license text found for {key}')
            destination = root / 'rust-crates' / key
            destination.mkdir(parents=True)
            for text in texts:
                shutil.copy2(text, destination / text.name)
            packages[key] = {'name': entry['name'], 'version': entry['version'],
                             'source': entry['source'], 'checksum': entry.get('checksum'),
                             'declared_license': manifest.get('license'),
                             'license_files': sorted(p.name for p in texts)}
    (root / 'rust-lock-inventory.json').write_text(
        json.dumps(list(packages.values()), ensure_ascii=False, indent=2) + '\n')
    (root / 'license-text-sha256.json').write_text(json.dumps({
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob('*')) if path.is_file()
    }, indent=2) + '\n')
    print(f'Collected notices for {len(packages)} locked Rust packages in {root}')


if __name__ == '__main__':
    main()
