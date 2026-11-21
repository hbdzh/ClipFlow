#!/usr/bin/env python3
"""Reviewed ORT 1.22.1 derivative: remove an adapted documentation example.

Default action verifies/applies the one-source patch, retains every upstream
compiler command unchanged, builds, and compares actual half-conversion functions with the
preserved unpatched baseline. Never installs into an app or runs a model.
"""
import argparse
import gzip
import json
from pathlib import Path
import shutil

import build_qa002_ort as base

BASE_RECIPE = "7f6019214f9e9f7127f2393cf447d0ef4b69d57d3275e0d50df784eed77b0db7"
PATCH = Path(__file__).resolve().parent / "patches/onnxruntime-1.22.1-std-bit-cast.patch"
TEST = PATCH.with_name("onnxruntime-1.22.1-bitcast-semantics.cpp")
PATCH_SHA = "c9d12e3fc57abaac30bdf2c668da7e74f3c0758b0eea6d16101f097905672517"
TEST_SHA = "9d780a4874ddada36055a15385fdb0b01eb735762cd9c01e4e36794483ceef8d"
PRE = "d906c1f9d36de8b06b0b7adf242473213e1fb90049c69e5d3b8727226d3ef840"
POST = "e7266190b1a5207310491c8595beeea4169bfbdd57bad824998fda6c9c5bd65c"
MATH = base.SOURCE / "onnxruntime/core/util/math_cpu.cc"
BASELINE_OUTPUT = "f0dfbd2a31e049156c71b2440ca8b17f3223042c105df3fdaffc60a3db82e2bc"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reviewed-recipe-sha256", required=True)
    args = parser.parse_args()
    if args.reviewed_recipe_sha256 != base.digest(__file__):
        raise RuntimeError("Integration-reviewed derivative recipe SHA is required")
    for path, expected in ((base.__file__, BASE_RECIPE), (PATCH, PATCH_SHA), (TEST, TEST_SHA)):
        if base.digest(path) != expected:
            raise RuntimeError("controlled input hash mismatch: " + str(path))
    history = base.EVIDENCE / "historical-ort-unpatched/manifest.json"
    historical = json.loads(history.read_text())
    if historical["artifact_sha256"] != "4d3d5cf5cf09bfc2ed465273b445b0607a3c5be6261996236f4a04966561ef5c":
        raise RuntimeError("unpatched historical artifact receipt mismatch")
    if base.digest(historical["artifact"]) != historical["artifact_sha256"]:
        raise RuntimeError("unpatched historical artifact changed")
    if shutil.disk_usage(base.BASE).free < 20 * 1024 ** 3:
        raise RuntimeError("controlled build requires at least 20 GiB free")
    if base.digest(MATH) == PRE:
        base.run(["patch", "--dry-run", "-p1", "-i", PATCH],
                 "ort-controlled-local-patch-check.log", cwd=base.SOURCE)
        base.run(["patch", "--forward", "-p1", "-i", PATCH],
                 "ort-controlled-local-patch.log", cwd=base.SOURCE)
    if base.digest(MATH) != POST:
        raise RuntimeError("unexpected derivative source postimage")
    command = json.loads((base.EVIDENCE / "ort-controlled-configure-command.json").read_text())["argv"]
    command = [arg for arg in command if not arg.startswith("-DCMAKE_PROJECT_onnxruntime_INCLUDE=")]
    # ORT 1.22.1 already uses GNU C++20 upstream. Remove the exploratory hook
    # from the cache; no compiler-standard adjustment is necessary or allowed.
    command += ["-U", "CMAKE_PROJECT_onnxruntime_INCLUDE"]
    base.save("ort-controlled-configure-command.json", {"argv": command,
              "recipe_sha256": base.digest(__file__), "base_recipe_sha256": BASE_RECIPE,
              "patch_sha256": PATCH_SHA, "compiler_options": "unchanged from upstream baseline"})
    base.run(command, "ort-controlled-configure.log")
    commands = json.loads((base.BUILD / "compile_commands.json").read_text())
    if len([entry for entry in commands if Path(entry["file"]) == MATH]) != 1:
        raise RuntimeError("expected one patched translation unit")
    prior = next(row for row in historical["files"] if row["original"] == "ort-controlled-compile_commands.json")
    prior_path = Path(prior["archive"])
    if base.digest(prior_path) != prior["archive_sha256"]:
        raise RuntimeError("historical compiler-command archive changed")
    baseline_commands = json.loads(gzip.decompress(prior_path.read_bytes()))
    canonical = lambda entries: sorted(json.dumps(entry, sort_keys=True) for entry in entries)
    if canonical(commands) != canonical(baseline_commands):
        raise RuntimeError("compiler commands differ from the reviewed unpatched upstream baseline")
    base.run([base.TOOLS / "cmake", "--build", base.BUILD, "--target", "onnxruntime", "--parallel", "4"],
             "ort-controlled-patched-build.log")
    old = json.loads((base.EVIDENCE / "ort-bitcast-semantics-baseline.json").read_text())
    if old["source_sha256"] != TEST_SHA or old["output_sha256"] != BASELINE_OUTPUT:
        raise RuntimeError("unpatched numerical baseline mismatch")
    closure = json.loads((base.EVIDENCE / "ort-controlled-observed-closure.json").read_text())
    directory = base.BASE / "semantics"
    executable, output = directory / "patched", directory / "patched.bin"
    argv = ["xcrun", "clang++", "-std=c++20", "-O2", "-arch", "arm64", "-mmacosx-version-min=27.0",
            TEST, *[base.BUILD / row["path"] for row in closure["static_archives"]],
            "-Wl,-dead_strip", "-framework", "Foundation", "-framework", "CoreFoundation", "-o", executable]
    base.run(argv, "ort-bitcast-semantics-patched-link.log")
    base.run([executable, output], "ort-bitcast-semantics-patched-run.log")
    if base.digest(output) != BASELINE_OUTPUT or output.stat().st_size != old["output_bytes"]:
        raise RuntimeError("patched actual ORT conversion functions differ from unpatched baseline")
    base.save("ort-bitcast-semantics-comparison.json", {"status": "PASS", "source_sha256": TEST_SHA,
              "unpatched_executable_sha256": old["executable_sha256"],
              "patched_executable_sha256": base.digest(executable),
              "baseline_and_patched_output_sha256": BASELINE_OUTPUT, "output_bytes": output.stat().st_size,
              "all_half_representations": 65536, "float_boundary_neighborhoods": 120,
              "deterministic_float_bit_patterns": 1048576, "deterministic_double_bit_patterns": 1048576,
              "double_boundary_neighborhoods": 27,
              "scope": "Actual ORT floatToHalf/doubleToHalf/halfToFloat statically linked; no model, session, GUI, playback"})
    library = base.BUILD / "libonnxruntime.1.22.1.dylib"
    base.save("ort-controlled-derivative-receipt.json", {"version": "1.22.1", "commit": base.COMMIT,
              "artifact": str(library), "sha256": base.digest(library),
              "recipe_sha256": base.digest(__file__), "base_recipe_sha256": BASE_RECIPE,
              "source_archive_sha256": base.SOURCE_HASH,
              "source_modifications": [{"file": str(MATH), "preimage_sha256": PRE,
                   "postimage_sha256": POST, "patch": str(PATCH), "patch_sha256": PATCH_SHA}],
              "all_compiler_commands_identical_to_unpatched_baseline": True,
              "CMakeCache_sha256": base.digest(base.BUILD / "CMakeCache.txt"),
              "compile_commands_sha256": base.digest(base.BUILD / "compile_commands.json"),
              "note": "Only math_cpu.cc source changes; two call sites use std::bit_cast; all compiler commands/options/API/model/deployment unchanged."})
    print("PASS: patched ORT built; actual full-half/boundary/2x1048576 semantics match baseline")


if __name__ == "__main__":
    main()
