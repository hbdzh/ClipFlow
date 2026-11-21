#!/usr/bin/env python3
"""Build isolated macOS prototype or native development bundles."""
import argparse
import hashlib
import json
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMPONENTS = ROOT / 'apps/macos/build/MAC-005/MediaStudio-QA002-Native-Closure-v6.app'
OUTPUT_ROOT = ROOT / 'apps/macos/build/unified'


def run(*args):
    print('+', ' '.join(map(str, args)), flush=True)
    subprocess.run([str(a) for a in args], cwd=ROOT, check=True)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def components(bundle):
    contents = bundle / 'Contents'
    result = {
        'runtime': contents / 'Frameworks/libonnxruntime.dylib',
        'model': contents / 'Resources/ai/model.onnx',
        'graphs': contents / 'Resources/ai/graphs',
        'ffmpeg': contents / 'MacOS/ffmpeg',
        'ffprobe': contents / 'MacOS/ffprobe',
        'licenses': contents / 'Resources/licenses',
    }
    missing = [str(p) for p in result.values() if not p.exists()]
    if missing:
        raise ValueError('缺少组件：' + ', '.join(missing))
    for name in ('pre', 'post', *(f'lstm-{i}-{d}' for i in range(3) for d in ('forward', 'reverse'))):
        if not (result['graphs'] / f'{name}.onnx').is_file():
            raise ValueError(f'缺少模型子图：{name}.onnx')
    source = (ROOT / 'core/native-ai-worker/src/main.rs').read_text()
    model = re.search(r'MODEL_SHA: &str = "([0-9a-f]{64})"', source)
    graphs = dict(re.findall(r'"([^"/]+\.onnx)",\s*"([0-9a-f]{64})"', source))
    if model is None or len(graphs) != 8:
        raise ValueError('无法读取 worker 的模型校验信息')
    for path, expected in [(result['model'], model.group(1)),
                           *((result['graphs'] / name, value) for name, value in graphs.items())]:
        if digest(path) != expected:
            raise ValueError(f'模型或子图摘要不匹配：{path}')
    return result


def main():
    parser = argparse.ArgumentParser(description='构建并运行 ClipFlow macOS 视觉样板或完整开发版')
    parser.add_argument('mode', nargs='?', choices=('prototype', 'full'), default='prototype')
    parser.add_argument('--components-app', type=Path, default=DEFAULT_COMPONENTS,
                        help='full 模式复用组件的开发 App（不会修改）')
    parser.add_argument('--build-only', action='store_true', help='只构建，不启动')
    parser.add_argument('--verify', action='store_true', help='启动后确认新进程存在')
    parser.add_argument('--keep-intermediates', action='store_true', help='保留本次成功构建的编译中间文件，便于调试')
    parser.add_argument('--self-tests', action='store_true', help='在完整开发版中包含原生自测入口')
    args = parser.parse_args()
    if args.build_only and args.verify:
        parser.error('--build-only 和 --verify 不能同时使用')
    if args.mode == 'prototype' and args.self_tests:
        parser.error('--self-tests 仅适用于 full 模式')
    parts = components(args.components_app.resolve()) if args.mode == 'full' else None
    if parts:
        print('复用组件包:', args.components_app.resolve(), flush=True)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    session = Path(tempfile.mkdtemp(prefix=args.mode + '-', dir=OUTPUT_ROOT))
    print('构建目录:', session, flush=True)
    if args.mode == 'prototype':
        app = session / 'ClipFlow-VisualPrototype.app'
        binary = app / 'Contents/MacOS/Prototype'
        binary.parent.mkdir(parents=True)
        sources = sorted((ROOT / 'apps/macos/VisualPrototype').glob('*.swift'))
        sources.append(ROOT / 'apps/macos/Sources/StudioSharedComponents.swift')
        run('xcrun', 'swiftc', '-parse-as-library', '-module-cache-path', session / 'swift-cache',
            *sources, '-o', binary)
        info = {'CFBundleIdentifier': 'local.mediastudio.visualprototype',
                'CFBundleName': 'ClipFlow 视觉样板', 'CFBundleExecutable': 'Prototype',
                'CFBundleIconFile': 'ClipFlow.icns',
                'CFBundlePackageType': 'APPL', 'NSHighResolutionCapable': True,
                'NSPrincipalClass': 'NSApplication'}
        resources = app / 'Contents/Resources'
        resources.mkdir()
        shutil.copy2(ROOT / 'apps/macos/Resources/ClipFlow.icns', resources / 'ClipFlow.icns')
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
        run('codesign', '--force', '--sign', '-', '--timestamp=none', app)
        run('codesign', '--verify', '--strict', app)
    else:
        base = session / 'Base.app'
        app = session / 'ClipFlow-Development.app'
        worker_target = session / 'worker-target'
        run('cargo', 'build', '--release', '--offline', '--locked', '--manifest-path',
            ROOT / 'core/native-ai-worker/Cargo.toml', '--target-dir', worker_target)
        base_command = [sys.executable, '-B', ROOT / 'apps/macos/build_mac005_base.py', '--output', base,
                        '--work-dir', session / 'base-build']
        if args.self_tests:
            base_command.append('--self-tests')
        run(*base_command)
        worker = worker_target / 'release/mediastudio-native-ai-worker'
        command = [sys.executable, '-B', ROOT / 'apps/macos/assemble_native_ai.py',
                   '--base-app', base, '--output', app, '--worker', worker]
        for name, path in parts.items():
            command.extend(['--' + name, path])
        run(*command, '--sandbox')
        files = [parts['runtime'], parts['model'], parts['ffmpeg'], parts['ffprobe'], worker]
        files += sorted(p for p in parts['graphs'].iterdir() if p.is_file())
        manifest = {'components_app': str(args.components_app.resolve()),
                    'inputs_sha256': {str(p): digest(p) for p in files},
                    'licenses_source': str(parts['licenses']), 'sandbox': True}
        (session / 'components.json').write_text(json.dumps(manifest, indent=2) + '\n')
    if not args.keep_intermediates:
        for name in ('swift-cache', 'base-build', 'worker-target', 'Base.app'):
            path = session / name
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
    print('应用:', app, flush=True)
    if not args.build_only:
        run('/usr/bin/open', '-n', app)
        if args.verify:
            import time
            time.sleep(2)
            run('/usr/bin/pgrep', '-f', str(binary if args.mode == 'prototype' else app / 'Contents/MacOS/MediaStudio'))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print('构建失败:', exc, file=sys.stderr)
        sys.exit(1)
