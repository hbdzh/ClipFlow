#!/usr/bin/env python3
"""Run a deterministic fault against the real transaction in an owned fixture.

The interposer deliberately compromises the test process and forks a same-UID
child at the rename boundary. This tests that call sequence, not whether an
independent uninstrumented attacker can win a particular scheduling race.
Never use the interposer with application binaries or user directories.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
interposer = root / "docs/ai-001/core_commit_race_interposer.c"
build_command = ["cargo", "test", "--manifest-path", "core/Cargo.toml", "--offline",
                 "-p", "media-ai", "--test", "transaction", "--no-run", "--message-format=json"]


def execute(command, env):
    return subprocess.run(command, cwd=root, env=env, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)


if platform.system() != "Darwin":
    raise SystemExit("macOS required")
with tempfile.TemporaryDirectory(prefix="ai001-interposer-") as directory:
    env = os.environ.copy()
    env.pop("DYLD_INSERT_LIBRARIES", None)
    env["TMPDIR"] = directory
    built = subprocess.run(build_command, cwd=root, env=env, text=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120, check=True)
    messages = [json.loads(line) for line in built.stdout.splitlines()]
    executables = [x["executable"] for x in messages if x.get("reason") == "compiler-artifact"
                   and x.get("target", {}).get("name") == "transaction" and x.get("executable")]
    if len(executables) != 1:
        raise SystemExit("could not uniquely identify test executable")
    binary = Path(executables[0])
    binary_sha256 = hashlib.sha256(binary.read_bytes()).hexdigest()
    command = [str(binary), "publishes_two_files_once_without_overwriting", "--exact", "--nocapture"]
    baseline = execute(command, env)
    library = str(Path(directory) / "test-only.dylib")
    subprocess.run(["clang", "-Wall", "-Wextra", "-Werror", "-dynamiclib",
                    str(interposer), "-o", library], check=True, timeout=30)
    injected_env = env.copy()
    injected_env["DYLD_INSERT_LIBRARIES"] = library
    injected = execute(command, injected_env)

changed = ("AI001_TEST_INTERPOSITION_COMPLETED" in injected.stdout and
           "[115, 117, 98, 115, 116, 105, 116, 117, 116, 101, 100]" in injected.stdout)
result = {
    "platform": platform.platform(),
    "test": "transaction::publishes_two_files_once_without_overwriting",
    "interposer_sha256": hashlib.sha256(interposer.read_bytes()).hexdigest(),
    "test_binary_sha256": binary_sha256,
    "transaction_source_sha256": hashlib.sha256((root / "core/media-ai/src/lib.rs").read_bytes()).hexdigest(),
    "baseline_exit": baseline.returncode,
    "injected_exit": injected.returncode,
    "injected_child_ran": "AI001_TEST_INTERPOSITION_COMPLETED" in injected.stdout,
    "replacement_observed_in_assertion": changed,
    "interpretation": "Expected vulnerable-code reproduction; not a security regression pass",
}
print(json.dumps(result, indent=2, sort_keys=True))
if baseline.returncode != 0 or injected.returncode == 0 or not changed:
    raise SystemExit("reproduction conditions not met; investigate captured behavior")
