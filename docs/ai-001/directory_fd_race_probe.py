#!/usr/bin/env python3
"""Reproduce two macOS same-UID name/content races in a disposable directory.

Research only; this does not touch a user destination or exercise Core.
"""
import ctypes
import json
import os
import pathlib
import platform
import tempfile

if platform.system() != 'Darwin':
    raise SystemExit('macOS required')
lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
rename = lib.renameatx_np
rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
rename.restype = ctypes.c_int
clone = lib.fclonefileat
clone.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
clone.restype = ctypes.c_int

with tempfile.TemporaryDirectory(prefix='ms-ai-fd-race-') as tmp:
    root = pathlib.Path(tmp)
    outer = root / 'outer'
    outer.mkdir(mode=0o700)
    ready = outer / 'ready'
    ready.mkdir(mode=0o700)
    (ready / 'stem').write_bytes(b'validated')
    rootfd = os.open(root, os.O_RDONLY)
    outerfd = os.open(outer, os.O_RDONLY)
    readyfd = os.open(ready, os.O_RDONLY)
    try:
        # The validation saw this exact directory. The same UID replaces its
        # name, then rename resolves the replacement at the linearization point.
        verified_inode = os.fstat(readyfd).st_ino
        os.rename(ready, outer / 'original')
        ready.mkdir(mode=0o700)
        (ready / 'stem').write_bytes(b'replacement')
        rc = rename(outerfd, b'ready', rootfd, b'published-by-name', 4)
        if rc != 0:
            raise OSError(ctypes.get_errno(), 'renameatx_np failed')
        published = root / 'published-by-name'
        name_result = {
            'verified_inode': verified_inode,
            'published_inode': published.stat().st_ino,
            'published_content': (published / 'stem').read_text(),
        }
        # FD cloning addresses that source-name substitution, but a same-UID
        # process with access to the real directory can change content after
        # validation and before cloning. No undocumented race scheduling is
        # required to demonstrate that this is a different unresolved issue.
        (outer / 'original' / 'stem').write_bytes(b'changed-after-validation')
        rc = clone(readyfd, rootfd, b'published-by-fd', 0)
        clone_errno = ctypes.get_errno() if rc else 0
        cloned = root / 'published-by-fd'
        clone_result = {
            'rc': rc,
            'errno': clone_errno,
            'published_content': (cloned / 'stem').read_text() if rc == 0 else None,
        }
        result = {'platform': platform.platform(), 'name_rename': name_result,
                  'directory_fd_clone': clone_result}
        print(json.dumps(result, sort_keys=True, indent=2))
        if name_result['verified_inode'] == name_result['published_inode'] or name_result['published_content'] != 'replacement':
            raise SystemExit('substitution observation unexpected')
        if rc == 0 and clone_result['published_content'] != 'changed-after-validation':
            raise SystemExit('content mutation observation unexpected')
    finally:
        os.close(readyfd)
        os.close(outerfd)
        os.close(rootfd)
