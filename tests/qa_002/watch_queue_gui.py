#!/usr/bin/env python3
"""Observe only the dedicated queue GUI fixture's real process and staging."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--root', required=True, type=Path)
p.add_argument('--report', required=True, type=Path)
p.add_argument('--stop', required=True, type=Path)
a = p.parse_args()
assert not a.report.exists() and not a.stop.exists()
root = a.root.resolve()
assert root == Path('/private/tmp/mediastudio-qa002-queue-gui/output')
seen = {}
events = []
started = time.monotonic()
while not a.stop.exists() and time.monotonic() - started < 1200:
    for marker in root.rglob('.qa002-encode-*'):
        if marker in seen:
            continue
        try:
            lines = marker.read_text().splitlines()
            pid = int(lines[0]); output = Path(lines[1])
            assert output.is_relative_to(root) and output.name == 'output.mp4'
            seen[marker] = {'pid': pid, 'output': output, 'nonempty': False, 'gone': False}
        except (ValueError, IndexError, OSError):
            continue
    for marker, state in seen.items():
        output = state['output']; stage = output.parent; pid = state['pid']
        if not state['nonempty'] and output.exists() and output.stat().st_size > 0:
            command = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True)
            state['nonempty'] = True
            events.append({'kind': 'real-encode-nonempty', 'monotonic_seconds': time.monotonic(),
                           'pid': pid, 'output': str(output), 'bytes': output.stat().st_size,
                           'ps_exit': command.returncode, 'command': command.stdout.strip()})
        if state['nonempty'] and not state['gone']:
            try:
                os.kill(pid, 0); alive = True
            except ProcessLookupError:
                alive = False
            if not alive and not stage.exists():
                state['gone'] = True
                events.append({'kind': 'pid-and-staging-gone', 'monotonic_seconds': time.monotonic(),
                               'pid': pid, 'staging': str(stage)})
    a.report.write_text(json.dumps({'scope': 'Filesystem/process observation, not GUI state',
        'events': events, 'elapsed_seconds': time.monotonic()-started}, indent=2)+'\n')
    time.sleep(0.02)
