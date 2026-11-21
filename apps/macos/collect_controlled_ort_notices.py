#!/usr/bin/env python3
"""Package actual ORT CPU licenses and exact MPL covered source; never edits an app."""
import gzip
import io
import json
from pathlib import Path
import re
import shutil
import tarfile
import audit_qa002_ort as audit
import build_qa002_ort as r

OUT = r.BASE / 'static-notices'

def source_root(name):
    if name == 'onnxruntime':
        return r.SOURCE
    aliases = [name] + [alias for alias, target in r.ALIASES.items() if target == name]
    return next(r.BUILD / '_deps' / (alias + '-src') for alias in aliases
                if (r.BUILD / '_deps' / (alias + '-src')).exists())

def notice(path):
    result, current, block = [], [], False
    relevant = re.compile(r'copyright|licen[cs]e|permission|public domain|redistribution', re.I)
    for line in path.read_text(errors='replace').splitlines():
        if block or line.strip().startswith(('//', '/*')):
            current.append(line)
            if '/*' in line and '*/' not in line:
                block = True
            if '*/' in line:
                block = False
        elif not line.strip() and current:
            current.append(line)
        else:
            if relevant.search('\n'.join(current)):
                result.append('\n'.join(current).rstrip())
            current = []
    if relevant.search('\n'.join(current)):
        result.append('\n'.join(current).rstrip())
    text = '\n\n'.join(result)
    if re.search(r'copyright', path.read_text(errors='replace'), re.I) and not text:
        raise RuntimeError('copyright notice extraction failed: ' + str(path))
    return text

def main():
    closure_path = r.EVIDENCE / 'ort-controlled-observed-closure.json'
    closure = json.loads(closure_path.read_text())
    assert not (closure['unmapped'] or closure['unresolved'] or closure['missing_depfiles'])
    assert closure['sha256'] == r.digest(audit.LIBRARY)
    derivative = closure['derivative_receipt']
    assert derivative['all_compiler_commands_identical_to_unpatched_baseline']
    if OUT.exists():
        assert (OUT / 'inventory.json').is_file(), 'refusing an unrecognized directory'
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    material = json.loads((r.EVIDENCE / 'ort-controlled-actual-file-provenance.json').read_text())
    embedded = json.loads((r.EVIDENCE / 'ort-controlled-embedded-review-candidates.json').read_text())
    components = []
    for item in closure['components']:
        name = item['name']
        directory, dest = source_root(name), OUT / name
        dest.mkdir()
        for path in sorted(directory.iterdir()):
            if path.is_file() and re.fullmatch(r'(LICENSE|COPYING|NOTICE|COPYRIGHT)([._-].*)?', path.name, re.I):
                if name == 'eigen' and path.name == 'COPYING.MINPACK':
                    continue
                shutil.copyfile(path, dest / path.name)
        selected = [row for row in material if row['component'] == name]
        texts = []
        for row in selected:
            path = Path(row['path'])
            label = str(path.relative_to(directory)) if path.is_relative_to(directory) else '<build>/' + str(path.relative_to(r.BUILD))
            if text := notice(path):
                texts.append('===== ' + label + ' =====\n' + text)
        (dest / 'ACTUAL-SOURCE-NOTICES.txt').write_text('\n\n'.join(texts) + '\n')
        usage = item['observed_usage']
        components.append({'name': name, 'source_url': item['url'],
            'source_archive_sha256': item['sha256'], 'source_archive_sha1': item.get('sha1'),
            'license_expression': item['license_expression'],
            'classification': 'target-compiled-and-headers' if usage.get('compiled_source_files') else 'target-header-input',
            'material_file_count': len(selected)})
    shutil.copyfile(r.SOURCE / 'ThirdPartyNotices.txt', OUT / 'onnxruntime/ThirdPartyNotices.txt')
    umbrella = (r.SOURCE / 'ThirdPartyNotices.txt').read_text()
    boost = umbrella[umbrella.index('Boost Software License - Version 1.0 - August 17th, 2003'):].split('_____', 1)[0].strip()
    assert boost.endswith('DEALINGS IN THE SOFTWARE.')
    (OUT / 'mp11/LICENSE_1_0.txt').write_text(boost + '\n')
    (OUT / 'mp11/LICENSE-SOURCE.json').write_text(json.dumps({
        'upstream': 'ORT 1.22.1 ThirdPartyNotices.txt Boost section', 'ort_commit': r.COMMIT,
        'source_sha256': r.digest(r.SOURCE / 'ThirdPartyNotices.txt'),
        'license_sha256': r.digest(OUT / 'mp11/LICENSE_1_0.txt')}, indent=2) + '\n')
    llvm = r.BASE / 'LLVM-LICENSE-a85b37d0.txt'
    assert r.digest(llvm) == '8d85c1057d742e597985c7d4e6320b015a9139385cff4cbae06ffc0ebe89afee'
    shutil.copyfile(llvm, OUT / 'onnxruntime/LLVM-LICENSE-a85b37d0.txt')
    header = r.SOURCE / 'include/onnxruntime/core/common/inlined_containers_fwd.h'
    (OUT / 'onnxruntime/LLVM-SMALLVECTOR-ATTRIBUTION.txt').write_text(
        'Upstream LLVM commit a85b37d0ca819776c6034c2dbda2b21e54e3393a\n' +
        '\n'.join(header.read_text().splitlines()[45:59]) + '\n')
    covered = {Path(row['path']): 'eigen/' + str(Path(row['path']).relative_to(source_root('eigen')))
               for row in material if row['component'] == 'eigen'}
    for relative in ('include/onnxruntime/core/platform/Barrier.h', 'include/onnxruntime/core/platform/EigenNonBlockingThreadPool.h'):
        path = r.SOURCE / relative
        assert any(row['path'] == str(path) for row in material)
        covered[path] = 'onnxruntime/' + relative
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as archive:
        for path, name in sorted(covered.items(), key=lambda pair: pair[1]):
            data = path.read_bytes()
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), 0o644, 0
            archive.addfile(info, io.BytesIO(data))
    disclosure = OUT / 'MPL-COVERED-SOURCE.tar.gz'
    disclosure.write_bytes(gzip.compress(buffer.getvalue(), mtime=0))
    attributions = {'source_revision': 'ORT 1.22.1 ' + r.COMMIT,
        'license_groups': {'TensorFlow/Caffe2': 'Apache-2.0', 'ORT Eigen-derived threadpool': 'MPL-2.0',
            'LLVM SmallVector': 'Apache-2.0 WITH LLVM-exception', 'MLAS qdwconv Facebook/Google': 'BSD-3-Clause',
            'MLAS Intel/Arm': 'MIT', 'MurmurHash/scikit-learn': 'Public-domain original plus BSD-3-Clause adaptation',
            'Eigen Half/COLAMD/SuperLU': 'LicenseRef-Eigen-Embedded-Notices; exact notices and covered source retained'},
        'files': [{'path': str(Path(row['path']).relative_to(r.SOURCE)), 'sha256': row['sha256'],
                   'notices': row['notice_lines']} for row in embedded]}
    (OUT / 'EMBEDDED-ATTRIBUTIONS.json').write_text(json.dumps(attributions, indent=2) + '\n')
    (OUT / 'SOURCE-MODIFICATIONS.txt').write_text('ORT 1.22.1 ' + r.COMMIT + '\n'
        'MediaStudio local patch modifies only core/util/math_cpu.cc: removed the adapted documentation example helper;\n'
        'added <bit> and replaced two calls with std::bit_cast. All compiler commands unchanged.\n'
        'Patch SHA256: ' + derivative['source_modifications'][0]['patch_sha256'] + '\n'
        'Patched source SHA256: ' + derivative['source_modifications'][0]['postimage_sha256'] + '\n')
    upstream = closure['upstream_dependency_source_patches']
    (OUT / 'UPSTREAM-SOURCE-MODIFICATIONS.json').write_text(json.dumps(upstream, indent=2) + '\n')
    with (OUT / 'SOURCE-MODIFICATIONS.txt').open('a') as stream:
        stream.write('\nOfficial ORT CMake also applies these dependency build patches:\n')
        for row in upstream['official_cmake_patches']:
            stream.write(row['file'] + ' SHA256 ' + row['sha256'] + '\n')
        stream.write('Actual selected dependency source pre/postimages:\n')
        for row in upstream['changed_material_files']:
            stream.write(row['component'] + '/' + row['file'] + '\n  ' + row['preimage_sha256'] + ' -> ' + row['postimage_sha256'] + '\n')
    normalized = []
    for row in material:
        path, root = Path(row['path']), source_root(row['component'])
        relative = str(path.relative_to(root)) if path.is_relative_to(root) else '<build>/' + str(path.relative_to(r.BUILD))
        normalized.append({'component': row['component'], 'path': relative, 'kind': row['kind'],
                           'sha256': row['sha256'], 'bytes': row['bytes']})
    (OUT / 'ACTUAL-MATERIAL-FILES.json').write_text(json.dumps(normalized, indent=2) + '\n')
    files = [{'file': str(path.relative_to(OUT)), 'sha256': r.digest(path), 'bytes': path.stat().st_size}
             for path in sorted(OUT.rglob('*')) if path.is_file()]
    inventory = {'schema_version': 1, 'artifact_unsigned_sha256': closure['sha256'],
        'status': 'OBSERVED_MATERIAL_CLOSED_NOTICES_PREPARED_REQUIRES_INTEGRATION_REVIEW',
        'closure_sha256': r.digest(closure_path), 'collector_sha256': r.digest(__file__),
        'components': components, 'other_inputs': closure['other_inputs'],
        'source_disclosure': {'file': disclosure.name, 'sha256': r.digest(disclosure), 'exact_files': len(covered)},
        'license_refs': {'LicenseRef-Eigen-Embedded-Notices': 'See eigen/ACTUAL-SOURCE-NOTICES.txt and MPL-COVERED-SOURCE.tar.gz: exact permissive Fabian Giesen/COLAMD/SuperLU notices preserved verbatim.'},
        'files': files, 'limits': 'SDK/system dylibs are external. Headers are observed compiler inputs, not necessarily emitted code. Upstream aggregate notices are broader than this artifact. No patent/platform clearance.'}
    (OUT / 'inventory.json').write_text(json.dumps(inventory, indent=2) + '\n')
    r.save('ort-controlled-static-notices-inventory.json', inventory)
    print(json.dumps({'output': str(OUT), 'components': len(components), 'files': len(files)+1,
        'covered_source_files': len(covered), 'inventory_sha256': r.digest(OUT / 'inventory.json')}))

if __name__ == '__main__':
    main()
