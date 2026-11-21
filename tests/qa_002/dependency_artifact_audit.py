#!/usr/bin/env python3
"""Bind reviewed source-built dependencies to a fresh signed local candidate.

Static inspection and a CPU C API lookup only; no media, player or device access.
"""
import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import struct
import subprocess


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def sections(path):
    data = Path(path).read_bytes()
    assert struct.unpack_from('<I', data)[0] == 0xfeedfacf, 'Expected thin arm64 Mach-O'
    assert struct.unpack_from('<I', data, 4)[0] == 0x100000c, 'Expected arm64'
    count = struct.unpack_from('<I', data, 16)[0]
    offset, result = 32, {}
    for _ in range(count):
        command, size = struct.unpack_from('<II', data, offset)
        assert size >= 8 and offset + size <= len(data)
        if command == 0x19:
            n = struct.unpack_from('<I', data, offset + 64)[0]
            assert size >= 72 + n * 80
            for j in range(n):
                start = offset + 72 + j * 80
                name = data[start:start + 16].split(b'\0')[0].decode()
                segment = data[start + 16:start + 32].split(b'\0')[0].decode()
                length = struct.unpack_from('<Q', data, start + 40)[0]
                position = struct.unpack_from('<I', data, start + 48)[0]
                flags = struct.unpack_from('<I', data, start + 64)[0]
                if position and flags & 0xff not in (1, 0xc, 0x12):
                    assert position + length <= len(data)
                    result[segment + ',' + name] = {'bytes': length, 'sha256': hashlib.sha256(data[position:position + length]).hexdigest()}
        offset += size
    assert '__TEXT,__text' in result
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ['app', 'previous', 'ffmpeg-build', 'runtime-closure', 'report']:
        p.add_argument('--' + name, type=Path, required=True)
    a = p.parse_args()
    assert not a.report.exists()
    app, previous = a.app.resolve(), a.previous.resolve()
    closure = json.loads(a.runtime_closure.read_text())
    build = json.loads((a.ffmpeg_build / 'build-manifest.json').read_text())
    unsigned_runtime = Path(closure['artifact'])
    assert sha(unsigned_runtime) == closure['sha256']
    commands = []

    def run(argv):
        r = subprocess.run(list(map(str, argv)), capture_output=True, text=True)
        commands.append({'argv': list(map(str, argv)), 'exit': r.returncode, 'stdout': r.stdout, 'stderr': r.stderr})
        assert r.returncode == 0, commands[-1]
        return r.stdout

    pairs = {'libonnxruntime.dylib': (unsigned_runtime, app / 'Contents/Frameworks/libonnxruntime.dylib')}
    for tool in ['ffmpeg', 'ffprobe']:
        original = Path(build['artifact_paths'][tool])
        assert sha(original) == build['artifacts'][tool]
        pairs[tool] = (original, app / 'Contents/MacOS' / tool)
    bindings = {}
    for name, (unsigned, signed) in pairs.items():
        before, after = sections(unsigned), sections(signed)
        assert before == after, 'Signing changed code/data: ' + name
        links = run(['otool', '-L', signed])
        dependencies = [line.strip().split(' (', 1)[0] for line in links.splitlines()[1:]]
        if name == 'libonnxruntime.dylib':
            dependencies = dependencies[1:]
        assert all(d.startswith(('/usr/lib/', '/System/Library/')) for d in dependencies), dependencies
        run(['codesign', '--verify', '--strict', signed])
        bindings[name] = {'unsigned_sha256': sha(unsigned), 'signed_sha256': sha(signed),
                          'all_file_backed_sections_equal': True, 'sections': before,
                          'dynamic_dependencies': dependencies}
    runtime = ctypes.CDLL(str(pairs['libonnxruntime.dylib'][1]))
    get_api_type = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_uint32)
    get_version_type = ctypes.CFUNCTYPE(ctypes.c_char_p)

    class ApiBase(ctypes.Structure):
        _fields_ = [('GetApi', get_api_type), ('GetVersionString', get_version_type)]

    runtime.OrtGetApiBase.restype = ctypes.POINTER(ApiBase)
    base = runtime.OrtGetApiBase().contents
    assert base.GetApi(21), 'Worker requires ORT C API21'
    version = base.GetVersionString().decode()
    assert version == '1.22.1', version
    exports = run(['nm', '-gU', pairs['libonnxruntime.dylib'][1]])
    assert '_OrtGetApiBase' in exports and '_OrtSessionOptionsAppendExecutionProvider_CPU' in exports
    assert 'CoreML' not in exports and 'CUDA' not in exports
    main_sections = sections(app / 'Contents/MacOS/MediaStudio')
    assert main_sections == sections(previous / 'Contents/MacOS/MediaStudio'), 'Main code/data changed'
    unchanged = []
    for relative in ['Contents/Frameworks/libmedia_ffi.dylib', 'Contents/MacOS/media-ai-worker',
                     'Contents/Resources/ai/model.onnx',
                     *[str(f.relative_to(previous)) for f in sorted((previous / 'Contents/Resources/ai/graphs').glob('*.onnx'))]]:
        assert sha(app / relative) == sha(previous / relative), relative
        unchanged.append({'path': relative, 'sha256': sha(app / relative)})
    run(['codesign', '--verify', '--deep', '--strict', app])
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps({'result': 'PASS', 'scope': 'Exact code/data binding, system links and CPU C API lookup; not execution or complete vulnerability acceptance',
        'app': str(app), 'ort_version': version, 'worker_api21_available': True,
        'main_all_file_backed_sections_unchanged': True,
        'bindings': bindings, 'unchanged': unchanged, 'x264_derivative': build['x264_derivative'],
        'closure_sha256': sha(a.runtime_closure), 'build_manifest_sha256': sha(a.ffmpeg_build / 'build-manifest.json'),
        'script_sha256': sha(__file__), 'commands': commands}, indent=2) + '\n')
    print('Dependency artifact binding PASS')


if __name__ == '__main__':
    main()
