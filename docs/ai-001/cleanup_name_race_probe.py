#!/usr/bin/env python3
"""Demonstrate pathname unlink identity race in a disposable macOS directory.

This reproduces syscall semantics only; it does not run or change Core.
"""
import ctypes
import json
import os
from pathlib import Path
import platform
import tempfile

if platform.system() != "Darwin":
    raise SystemExit("macOS required")

lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
lib.unlinkat.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int)
lib.unlinkat.restype = ctypes.c_int

with tempfile.TemporaryDirectory(prefix="ms-ai-unlink-race-") as tmp:
    root = Path(tmp)
    ready = root / "ready"
    ready.mkdir(mode=0o700)
    observed = ready / "vocals.wav"
    observed.write_bytes(b"owned test output")
    directory_fd = os.open(ready, os.O_RDONLY)
    try:
        # Simulate the final pathname check in cleanup, then interleave a
        # same-UID replacement before unlinkat is reached.
        verified_inode = observed.lstat().st_ino
        displaced = ready / "displaced"
        observed.rename(displaced)
        observed.write_bytes(b"foreign test output")
        replacement_inode = observed.lstat().st_ino
        rc = lib.unlinkat(directory_fd, b"vocals.wav", 0)
        if rc:
            raise OSError(ctypes.get_errno(), "unlinkat failed")
        result = {"platform": platform.platform(),
                  "verified_inode": verified_inode,
                  "replacement_inode": replacement_inode,
                  "replacement_deleted": not observed.exists(),
                  "original_preserved": displaced.read_bytes() == b"owned test output"}
        print(json.dumps(result, sort_keys=True, indent=2))
        if (verified_inode == replacement_inode or not result["replacement_deleted"]
                or not result["original_preserved"]):
            raise SystemExit("unexpected filesystem semantics")
    finally:
        os.close(directory_fd)
