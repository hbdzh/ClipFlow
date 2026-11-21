#!/usr/bin/env python3
"""Compile inherit-only forwarding tools for controlled local queue GUI QA."""
import argparse
import hashlib
import json
import plistlib
import shutil
import subprocess
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--app', required=True, type=Path)
p.add_argument('--work', required=True, type=Path)
p.add_argument('--output-prefix', required=True, type=Path)
a = p.parse_args()
a.work.mkdir(parents=True, exist_ok=False)
source = Path(__file__).with_name('queue_gui_tool.c')
shutil.copy2(source, a.work/source.name)
entitlements = a.work/'inherit.plist'
entitlements.write_bytes(plistlib.dumps({'com.apple.security.app-sandbox': True,
                                        'com.apple.security.inherit': True}))
commands = []
def run(argv):
    r = subprocess.run(list(map(str, argv)), capture_output=True, text=True)
    commands.append({'argv': list(map(str, argv)), 'exit': r.returncode,
                     'stdout': r.stdout, 'stderr': r.stderr})
    r.check_returncode()
for name, probe in [('ffmpeg', 0), ('ffprobe', 1)]:
    config = ('#define REAL_TOOL '+json.dumps(str(a.app.resolve()/'Contents/MacOS'/name))+'\n'
              '#define OUTPUT_PREFIX '+json.dumps(str(a.output_prefix.resolve()))+'\n'
              '#define IS_PROBE '+str(probe)+'\n')
    (a.work/'queue_gui_config.h').write_text(config)
    binary = a.work/(name+'-controlled')
    run(['/usr/bin/xcrun', 'clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2',
         '-I', a.work, a.work/source.name, '-o', binary])
    run(['/usr/bin/codesign', '--force', '--sign', '-', '--timestamp=none',
         '--entitlements', entitlements, binary])
    run(['/usr/bin/codesign', '--verify', '--strict', binary])
manifest = {'scope': 'Real forwarding tools; selected encode throttled to 0.25x; slow-probe.mp4 delayed 22s',
            'commands': commands, 'hashes': {f.name: hashlib.sha256(f.read_bytes()).hexdigest()
                for f in a.work.iterdir() if f.is_file()}}
(a.work/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
