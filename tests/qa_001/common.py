"""Shared command recording only; generation and validation keep separate semantics."""
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import subprocess
import stat
import tempfile
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BUILD = HERE / 'build'
EVIDENCE = HERE / 'evidence'


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path = Path(path)
    if HERE.resolve() not in path.resolve().parents:
        raise ValueError('Evidence output escaped the owned QA directory')
    current = HERE
    for part in path.relative_to(HERE).parts:
        if part in ('..', '.'):
            raise ValueError('Refusing ambiguous evidence output path')
        current = current / part
        if current.is_symlink():
            raise ValueError('Refusing symlink evidence path')
        if current.exists():
            info = current.stat()
            if current != path and not stat.S_ISDIR(info.st_mode):
                raise ValueError('Evidence ancestor is not a directory')
            if current == path and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
                raise ValueError('Refusing hardlinked or special evidence output')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', prefix='.qa-evidence-', suffix='.tmp', dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def timestamp():
    return datetime.now(timezone(timedelta(hours=8))).isoformat()


class Commands:
    def __init__(self):
        self.records = []

    def run(self, argv, expected=0, timeout=30):
        argv = [str(arg) for arg in argv]
        start = time.monotonic()
        try:
            p = subprocess.run(argv, cwd=ROOT, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, timeout=timeout)
        except subprocess.TimeoutExpired as error:
            self.records.append({'argv': argv, 'result': 'TIMEOUT', 'timeout_seconds': timeout})
            raise AssertionError('Command timed out: ' + argv[0]) from error
        record = {'argv': argv, 'returncode': p.returncode,
                  'elapsed_seconds': round(time.monotonic() - start, 6),
                  'stdout': p.stdout, 'stderr': p.stderr}
        self.records.append(record)
        if expected == 'nonzero':
            assert p.returncode != 0, (argv, p.stdout, p.stderr)
        else:
            assert p.returncode == expected, (argv, p.returncode, p.stdout, p.stderr)
        return p


def relative(path):
    return str(Path(path).relative_to(ROOT))


def require_owned_build(path):
    """Only our ignored build directory may be created/removed by fixture scripts."""
    path = Path(path)
    require_build_root()
    resolved = path.resolve()
    if resolved == BUILD.resolve() or BUILD.resolve() not in resolved.parents:
        raise ValueError('Output escaped the owned build directory: ' + str(path))
    # No directory or leaf symlink is accepted, even if it currently points
    # inside build; do not turn a mutable redirection into ownership authority.
    current = BUILD
    for part in path.relative_to(BUILD).parts:
        if part in ('..', '.'):
            raise ValueError('Refusing ambiguous output path')
        current = current / part
        if current.is_symlink():
            raise ValueError('Refusing symlink output path: ' + str(current))
        if current.exists():
            info = current.stat()
            if current != path and not stat.S_ISDIR(info.st_mode):
                raise ValueError('Output ancestor is not a directory')
            if current == path:
                if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                    raise ValueError('Refusing special output node')
                if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                    raise ValueError('Refusing hardlinked output file')
    return path


def require_build_root():
    """Anchor ownership to the real test directory, never a redirected build root."""
    if BUILD.is_symlink():
        raise ValueError('Refusing symlink build root')
    if BUILD.resolve().parent != HERE.resolve():
        raise ValueError('Build root escaped the fixture directory')
    if BUILD.exists() and not BUILD.is_dir():
        raise ValueError('Build root is not a directory')


def owned_input_paths(rules, generation):
    """Bind untrusted generation sidecar paths to exact predeclared input names."""
    if set(generation['files']) != set(rules['fixtures']):
        raise ValueError('Generation fixture IDs differ from predeclared rules')
    inputs = {}
    for identifier, rule in rules['fixtures'].items():
        supplied = Path(generation['files'][identifier]['path'])
        filename = Path(rule['filename'])
        if supplied.is_absolute() or '..' in supplied.parts or filename.is_absolute() or '..' in filename.parts:
            raise ValueError('Refusing redirected input path')
        expected = BUILD / 'inputs' / filename
        if ROOT / supplied != expected:
            raise ValueError('Generation path does not match the fixed fixture name')
        inputs[identifier] = require_owned_build(expected)
    return inputs


@contextmanager
def temporary_output(destination, tag):
    """Keep final bytes untouched until caller validates and atomically replaces."""
    destination = require_owned_build(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = require_owned_build(destination.with_name(destination.stem + '.' + tag + '.partial' + destination.suffix))
    try:
        yield temporary
    finally:
        if temporary.exists():
            temporary.unlink()
