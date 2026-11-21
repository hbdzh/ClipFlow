#!/usr/bin/env python3
"""Observe actual GUI two-stem copies on a dedicated small QA volume only."""
import argparse, errno, json, os, select, stat, time
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--parent', required=True, type=Path)
p.add_argument('--report', required=True, type=Path)
p.add_argument('--stop', required=True, type=Path)
p.add_argument('--permission-fault', action='store_true')
a = p.parse_args(); parent = a.parent.resolve()
mount = Path('/private/tmp/mediastudio-qa002-ai-export-mount')
assert parent.parent == mount and parent.name in {'export-parent', 'export-fault'}
assert os.path.ismount(mount) and parent.stat().st_uid == os.geteuid()
v = os.statvfs(mount)
assert 200*1024**2 <= v.f_blocks*v.f_frsize <= 400*1024**2
assert parent.stat().st_dev != Path('/private/tmp').stat().st_dev
assert not a.report.exists() and not a.stop.exists() and not list(parent.iterdir())
original_mode = stat.S_IMODE(parent.stat().st_mode)
probe = parent/'.qa002-space-witness'
fd = os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
os.write(fd, bytes(4096)); os.fsync(fd); owned = os.fstat(fd)
directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
queue = select.kqueue()
queue.control([select.kevent(directory, filter=select.KQ_FILTER_VNODE,
    flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR, fflags=select.KQ_NOTE_WRITE)], 0)
started = time.monotonic(); events = []; last = None
fault_applied = False; enospc = False
try:
    while not a.stop.exists() and time.monotonic()-started < 180:
        stages = list(parent.glob('.mediastudio-export-*'))
        if a.permission_fault and stages and not fault_applied:
            parent.chmod(0o500); fault_applied = True
            events.append({'kind': 'owned-parent-permission-denial', 'mode': '0500',
                           'stage': str(stages[0]), 'elapsed_seconds': time.monotonic()-started})
        sizes = {}
        for stage in stages:
            try: sizes[stage.name] = {f.name: f.stat().st_size for f in stage.iterdir() if f.is_file()}
            except FileNotFoundError: pass
        free = os.statvfs(mount).f_bavail*v.f_frsize
        if free <= 4096 and not enospc:
            try: os.write(fd, bytes(4096)); os.fsync(fd)
            except OSError as error:
                if error.errno != errno.ENOSPC: raise
                enospc = True
                events.append({'kind': 'actual-ENOSPC', 'errno': error.errno,
                    'free_bytes': free, 'observed_stem_sizes': sizes,
                    'elapsed_seconds': time.monotonic()-started})
        state = (tuple((k, tuple(sorted((n,s//(16*1024**2)) for n,s in x.items())))
                       for k,x in sorted(sizes.items())), free == 0, enospc)
        if state != last:
            events.append({'kind': 'filesystem-observation', 'stems': sizes, 'free_bytes': free,
                           'elapsed_seconds': time.monotonic()-started}); last = state
            a.report.write_text(json.dumps({'scope': 'Filesystem observation; GUI states are independent',
                'permission_fault_requested': a.permission_fault, 'permission_fault_applied': fault_applied,
                'observed_real_enospc': enospc, 'events': events}, indent=2)+'\n')
        queue.control(None, 8, .001)
finally:
    queue.close(); os.close(directory); os.close(fd); parent.chmod(original_mode)
    if probe.exists() and (probe.stat().st_dev, probe.stat().st_ino) == (owned.st_dev, owned.st_ino): probe.unlink()
    value = json.loads(a.report.read_text()) if a.report.exists() else {'events': events}
    value['qa_owned_probe_removed'] = not probe.exists()
    value['qa_parent_permissions_restored'] = stat.S_IMODE(parent.stat().st_mode) == original_mode
    value['remaining_staging'] = [str(f) for f in parent.glob('.mediastudio-export-*')]
    value['published_directories'] = [f.name for f in parent.iterdir() if f.is_dir() and not f.name.startswith('.mediastudio-export-')]
    a.report.write_text(json.dumps(value, indent=2)+'\n')
