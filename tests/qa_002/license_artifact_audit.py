#!/usr/bin/env python3
"""Read-only AC25 inventory of a frozen local app; not legal/release approval.

Lists all locked crates conservatively, including optional/target-only entries.
Does not claim static linkage is proven by a lockfile or otool output. Never
opens media, an audio device, a GUI or a model runtime.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import tarfile
import tomllib
from pathlib import Path


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run(argv):
    result = subprocess.run(list(map(str, argv)), stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=30)
    return {'argv': list(map(str, argv)), 'exit': result.returncode,
            'stdout': result.stdout, 'stderr': result.stderr}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--app', required=True, type=Path)
    p.add_argument('--evidence', required=True, type=Path)
    p.add_argument('--tag', default='v6')
    p.add_argument('--build-manifest', type=Path,
                   help='Query the directly hashed unsigned build inputs instead of inherit-entitlement helpers')
    p.add_argument('--direct-binding', type=Path,
                   help='Independent direct build-input to signed-artifact verification receipt')
    a = p.parse_args()
    root = Path(__file__).resolve().parents[2]
    app = a.app.resolve(strict=True)
    ev = a.evidence
    ev.mkdir(parents=True, exist_ok=True)
    texts = app / 'Contents/Resources/licenses'
    before = {str(x.relative_to(app)): sha(x) for x in app.rglob('*') if x.is_file()}
    locks = [root / 'core/Cargo.lock', root / 'core/native-ai-worker/Cargo.lock']
    registry = Path(os.environ['CARGO_HOME']) / 'registry'
    registry_src = registry / 'src/index.crates.io-1949cf8c6b5b557f'
    cache = registry / 'cache/index.crates.io-1949cf8c6b5b557f'
    inventory = {x['name'] + '-' + x['version']: x
                 for x in json.loads((texts / 'rust-lock-inventory.json').read_text())}
    entries = {}
    for lock in locks:
        for item in tomllib.loads(lock.read_text())['package']:
            if not item.get('source', '').startswith('registry+'):
                continue
            key = item['name'] + '-' + item['version']
            if key not in entries:
                entries[key] = {**item, 'lockfiles': []}
            entries[key]['lockfiles'].append(str(lock.relative_to(root)))
    components = []
    crate_checks = []
    for key, item in sorted(entries.items()):
        src = registry_src / key
        archive = cache / (key + '.crate')
        manifest = tomllib.loads((src / 'Cargo.toml').read_text())['package']
        expected = inventory[key]
        notice_checks = {name: sha(texts / 'rust-crates' / key / name) == sha(src / name)
                         for name in expected['license_files']}
        checksum_ok = sha(archive) == item['checksum'] == expected['checksum']
        assert expected['declared_license'] == manifest.get('license')
        assert checksum_ok and notice_checks and all(notice_checks.values()), key
        row = {'name': item['name'], 'version': item['version'],
               'lockfiles': item['lockfiles'], 'registry_source': item['source'],
               'registry_archive_sha256': sha(archive), 'lock_checksum_matches': checksum_ok,
               'declared_license': manifest.get('license'), 'bundled_notice_matches_registry': notice_checks}
        crate_checks.append(row)
        components.append({'type': 'library', 'bom-ref': 'cargo:' + key,
                           'name': item['name'], 'version': item['version'],
                           'purl': 'pkg:cargo/' + item['name'] + '@' + item['version'],
                           'hashes': [{'alg': 'SHA-256', 'content': item['checksum']}],
                           'licenses': [{'expression': manifest['license']}],
                           'scope': 'optional',
                           'properties': [{'name': 'mediastudio:inventory-scope',
                                           'value': 'lockfile-conservative; not proof this target links the crate'}]})
    assert set(entries) == set(inventory), 'Bundled inventory/lockfile package set differs'
    commands = []
    build_binding = None
    if a.build_manifest:
        build_binding = json.loads(a.build_manifest.read_text())
        for source, digest in build_binding['source_archives'].items():
            assert sha(Path(source)) == digest, source
        assert sha(root / 'apps/macos/build_mac005_ffmpeg.py') == build_binding['build_script_sha256']
        for name in ['ffmpeg', 'ffprobe']:
            tool = Path(build_binding['artifact_paths'][name])
            assert sha(tool) == build_binding['artifacts'][name], name
            for option in ['-version', '-buildconf', '-L']:
                commands.append(run([tool, option]))
        assert all(x['exit'] == 0 for x in commands)
    version_text = ''.join(x['stdout'] + x['stderr'] for x in commands[:1])
    match = re.search(r'ffmpeg version (\S+)', version_text)
    ffmpeg_version = match.group(1) if match else None
    source_root = root / 'apps/macos/build/MAC-005/sources'
    archives = []
    for path in sorted(source_root.glob('*.tar.*')):
        archives.append({'file': str(path.relative_to(root)), 'sha256': sha(path)})
    upstream_checks = {}
    for local_name, upstream_name in [
            ('model-umx-MIT.txt', 'license-upstream-model-license.txt'),
            ('onnxruntime-1.22.1/LICENSE', 'license-upstream-ort-license.txt'),
            ('onnxruntime-1.22.1/ThirdPartyNotices.txt', 'license-upstream-ort-notices.txt')]:
        upstream_checks[local_name] = sha(texts / local_name) == sha(ev / upstream_name)
    assert all(upstream_checks.values())
    missing = [name for name in ['ffmpeg-LICENSE.md', 'ffmpeg-IJG-NOTICE.txt']
               if not (texts / name).is_file()]
    components.extend([
        {'type': 'application', 'bom-ref': 'runtime:ffmpeg', 'name': 'FFmpeg/ffprobe',
         **({'version': ffmpeg_version} if ffmpeg_version else {}),
         'licenses': [{'expression': 'GPL-2.0-or-later'}],
         'externalReferences': [{'type': 'website', 'url': 'https://ffmpeg.org/'}],
         'properties': [{'name': 'mediastudio:linkage', 'value': 'Separate CLI; statically linked FFmpeg libraries and libx264; GPL CLI is not a license exemption'}]},
        {'type': 'library', 'bom-ref': 'runtime:x264', 'name': 'x264',
         'version': '31e19f92f00c7003fa115047ce50978bc98c3a0d',
         'licenses': [{'expression': 'GPL-2.0-or-later'}],
         'properties': [{'name': 'mediastudio:provenance-limit', 'value':
                         'Fixed source archive and current direct signed-tool build-input receipt verified separately'
                         if a.direct_binding else
                         'Supplied source revision; direct historical signed-binary build provenance not reconstructed'}]},
        {'type': 'library', 'bom-ref': 'runtime:onnxruntime', 'name': 'ONNX Runtime',
         'version': '1.22.1', 'licenses': [{'expression': 'MIT'}],
         'externalReferences': [{'type': 'website', 'url': 'https://github.com/microsoft/onnxruntime/tree/v1.22.1'}],
         'properties': [{'name': 'mediastudio:notice-limit', 'value': 'Full upstream ThirdPartyNotices retained; build-specific static component/version closure not independently proven'}]},
        {'type': 'machine-learning-model', 'bom-ref': 'model:umxhq', 'name': 'UMX-HQ vocals ONNX',
         'version': 'e06097d3193a4c3168b5ddd2a479a680b1176c16',
         'hashes': [{'alg': 'SHA-256', 'content': sha(app / 'Contents/Resources/ai/model.onnx')}],
         'licenses': [{'expression': 'MIT'}],
         'externalReferences': [{'type': 'website', 'url': 'https://zenodo.org/records/3370489'}],
         'properties': [{'name': 'mediastudio:scope', 'value': 'Original weights independently declared MIT; fixed derivative card and complete MIT retained; training data and patents not thereby cleared'}]}
    ])
    graph_root = app / 'Contents/Resources/ai/graphs'
    graphs = json.loads((graph_root / 'manifest.json').read_text())
    graph_checks = {name: sha(graph_root / name) == value for name, value in graphs['graphs'].items()}
    assert len(graph_checks) == 8 and all(graph_checks.values())
    components.extend({'type': 'data', 'bom-ref': 'graph:' + name, 'name': name,
                       'version': 'UMX-HQ-fixed-subgraph-v1',
                       'hashes': [{'alg': 'SHA-256', 'content': digest}],
                       'licenses': [{'expression': 'MIT'}],
                       'properties': [{'name': 'mediastudio:derivation-script-sha256',
                                       'value': sha(root / 'core/native-ai-worker/tools/prepare_graphs.py')}]}
                      for name, digest in sorted(graphs['graphs'].items()))
    files = {str(x.relative_to(app)): sha(x) for x in app.rglob('*') if x.is_file()}
    assert files == before, 'Audit mutated frozen app'
    direct_binding = None
    if a.direct_binding:
        assert a.build_manifest
        direct_binding = json.loads(a.direct_binding.read_text())
        assert direct_binding['result'] == 'PASS_DIRECT_LOCAL_BUILD_TO_SIGNED_ARTIFACT_BINDING'
        assert direct_binding['app'] == str(app)
        assert direct_binding['build_manifest_sha256'] == sha(a.build_manifest)
        for name in ['ffmpeg', 'ffprobe']:
            row = direct_binding['tools'][name]
            assert row['exact_match'] and row['delivered_sha256'] == files['Contents/MacOS/' + name]
            assert row['direct_input_sha256'] == build_binding['artifacts'][name]
        assert all(direct_binding['all_94_license_files_match_exact_final_materials_and_assembly'].values())
    completeness = ('INCOMPLETE: conservative lock inventory and delivered files; '
                    'ORT build-specific static component/version closure and source-delivery compliance unresolved')
    unresolved = ['ORT complete build-specific static dependency/version closure not independently proven',
                  'Public GPL source delivery, distribution channel, patent/legal and final signing/notarization approval not completed']
    if not direct_binding:
        completeness += '; direct signed-tool build-input chain not proven'
        unresolved.insert(0, 'Historical signed FFmpeg/x264 exact build-input chain not proven by a different rebuild')
    sbom = {'bomFormat': 'CycloneDX', 'specVersion': '1.6', 'version': 1,
            'metadata': {'component': {'type': 'application', 'name': app.name,
                                     'version': sha(app / 'Contents/MacOS/MediaStudio')},
                         'properties': [{'name': 'mediastudio:completeness',
                                         'value': completeness}]},
            'components': components}
    result = {'result': 'NO_GO_PUBLIC_DISTRIBUTION', 'scope': 'AC25 artifact inventory; dependency advisories assessed separately',
              'app': str(app), 'app_unchanged': True, 'files': files,
              'lockfiles': {str(x.relative_to(root)): sha(x) for x in locks},
              'locked_registry_count': len(entries), 'crate_checks': crate_checks,
              'source_archives': archives, 'upstream_notice_checks': upstream_checks,
              'all_eight_graphs_match': graph_checks, 'missing_bundled_notices': missing,
              'commands': commands, 'ffmpeg_version': ffmpeg_version,
              'tool_query_scope': 'Direct SHA-bound unsigned build inputs; final signed helper copy/sign binding is separate'
                  if a.build_manifest else 'NOT_RUN: inherit-entitlement helper requires app parent; earlier standalone abort evidence retained separately',
              'build_manifest': str(a.build_manifest) if a.build_manifest else None,
              'build_manifest_sha256': sha(a.build_manifest) if a.build_manifest else None,
              'direct_signed_tool_build_input_binding': 'PASS' if direct_binding else 'NOT_PROVEN',
              'direct_binding_receipt': str(a.direct_binding) if a.direct_binding else None,
              'unresolved': unresolved,
              'local_controlled_development_only': True}
    (ev / ('license-' + a.tag + '-sbom.cdx.json')).write_text(json.dumps(sbom, indent=2) + '\n')
    (ev / ('license-' + a.tag + '-artifact-audit.json')).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ['result', 'app_unchanged', 'locked_registry_count',
                                           'ffmpeg_version', 'missing_bundled_notices']}))


if __name__ == '__main__':
    main()
