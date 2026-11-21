#!/usr/bin/env python3
"""Build-only ORT 1.22.1 provenance recipe; never installs into an application.

prepare/configure do not compile. build requires an explicit reviewed-recipe SHA256.
audit emits observed linker objects and compiler headers, not a default-deps SBOM.
All downloaded source remains under the ignored build directory.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tarfile
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "apps/macos/build/ORT-1.22.1-controlled"
EVIDENCE = ROOT / "docs/qa-002/evidence/dependency-closure-20261115"
COMMIT = "89746dc19a0a1ae59ebf4b16df9acab8f99f3925"
SOURCE_HASH = "38f8ffa28a5ea9040431816c6204e6fc36d1b8eab43b33dbee92fceeb181d151"
SOURCE = BASE / "source" / ("onnxruntime-" + COMMIT)
BUILD = BASE / "Release"
TOOLS = BASE / "tools-venv/bin"
SELECTED = ("abseil_cpp", "re2", "protobuf", "date", "json", "pytorch_cpuinfo",
            "microsoft_gsl", "safeint", "flatbuffers", "onnx", "eigen", "mp11", "protoc_mac_universal")
ALIASES = {"gsl": "microsoft_gsl", "eigen": "eigen", "eigen3": "eigen",
           "nlohmann_json": "json", "protoc_binary": "protoc_mac_universal"}


def digest(path, algorithm="sha256"):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()


def save(name, value):
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def run(args, name=None, cwd=None):
    result = subprocess.run([str(x) for x in args], cwd=cwd, check=False,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if name:
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        (EVIDENCE / name).write_text(result.stdout)
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {args}; log={name}\n{result.stdout[-2500:]}")
    return result.stdout


def download(url, target, expected, algorithm="sha256"):
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        temporary = target.with_name(target.name + ".part")
        run(["curl", "--fail", "--location", "--max-time", "180", "--retry", "2",
             url, "--output", temporary])
        if digest(temporary, algorithm) != expected:
            temporary.unlink()
            raise RuntimeError("source digest mismatch: " + url)
        temporary.rename(target)
    if digest(target, algorithm) != expected:
        raise RuntimeError("cached source digest mismatch: " + str(target))


def deps():
    return {parts[0]: {"url": parts[1], "sha1": parts[2]}
            for line in (SOURCE / "cmake/deps.txt").read_text().splitlines()
            if line and not line.startswith("#") and len(parts := line.split(";")) == 3}


def prepare():
    archive = BASE / "source/onnxruntime-89746dc.tar.gz"
    download(f"https://codeload.github.com/microsoft/onnxruntime/tar.gz/{COMMIT}", archive, SOURCE_HASH)
    if not SOURCE.exists():
        with tarfile.open(archive) as stream:
            stream.extractall(archive.parent, filter="data")
    inventory = []
    for name in SELECTED:
        dep = deps()[name]
        parsed = urlparse(dep["url"])
        target = SOURCE / "mirror" / parsed.netloc / parsed.path.lstrip("/")
        # GitHub archive redirects lead here; ZIP contents/hash stay identical.
        match = re.fullmatch(r"/([^/]+)/([^/]+)/archive/(.+?)(?:/[^/]+)?\.zip", parsed.path)
        fetch_url = dep["url"]
        if match and parsed.netloc == "github.com":
            owner, repo, revision = match.groups()
            # Preserve refs/tags; eigen's archive URL includes a decorative basename.
            if name == "eigen":
                revision = parsed.path.split("/archive/", 1)[1].split("/", 1)[0]
            else:
                revision = parsed.path.split("/archive/", 1)[1][:-4]
            fetch_url = f"https://codeload.github.com/{owner}/{repo}/zip/{revision}"
        download(fetch_url, target, dep["sha1"], "sha1")
        inventory.append({"name": name, **dep, "download_url": fetch_url,
                          "sha256": digest(target), "bytes": target.stat().st_size,
                          "role": "prefetched input; NOT evidence of linked code"})
        save("ort-controlled-source-inputs.json", {"ort_commit": COMMIT,
             "ort_archive_sha256": SOURCE_HASH, "inputs": inventory})


def configure():
    BUILD.mkdir(parents=True, exist_ok=True)
    for name, version in (("cmake", "3.31.10"), ("ninja", "1.11.1")):
        actual = run([TOOLS / name, "--version"])
        if version not in actual.splitlines()[0]:
            raise RuntimeError("unexpected private build tool: " + actual)
    args = [TOOLS / "cmake", "-S", SOURCE / "cmake", "-B", BUILD, "-G", "Ninja",
            f"-DCMAKE_MAKE_PROGRAM={TOOLS / 'ninja'}", "-DCMAKE_BUILD_TYPE=Release",
            "-DCMAKE_OSX_ARCHITECTURES=arm64", "-DCMAKE_OSX_DEPLOYMENT_TARGET=27.0",
            "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON", "-DFETCHCONTENT_TRY_FIND_PACKAGE_MODE=NEVER",
            "-DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF", "-DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=OFF",
            "-DCMAKE_FIND_USE_CMAKE_ENVIRONMENT_PATH=OFF", "-Dprotobuf_WITH_ZLIB=OFF",
            # An archive inside MediaStudio must not inherit the host repo's Git identity.
            "-DCMAKE_DISABLE_FIND_PACKAGE_Git=ON",
            f"-DCMAKE_SHARED_LINKER_FLAGS=-Wl,-map,{BUILD / 'onnxruntime.linkmap'}",
            "-Donnxruntime_BUILD_SHARED_LIB=ON", "-Donnxruntime_BUILD_UNIT_TESTS=OFF",
            "-Donnxruntime_MINIMAL_BUILD=OFF", "-Donnxruntime_DISABLE_CONTRIB_OPS=OFF",
            "-Donnxruntime_ENABLE_PYTHON=OFF", "-Donnxruntime_USE_COREML=OFF",
            "-Donnxruntime_USE_CUDA=OFF", "-Donnxruntime_USE_DNNL=OFF",
            "-Donnxruntime_USE_XNNPACK=OFF", "-Donnxruntime_USE_WEBGPU=OFF",
            "-Donnxruntime_USE_VCPKG=OFF", "-Donnxruntime_USE_KLEIDIAI=OFF",
            "-Donnxruntime_ENABLE_TRAINING=OFF", "-Donnxruntime_ENABLE_TRAINING_APIS=OFF",
            "-Donnxruntime_ENABLE_LTO=OFF", "-Donnxruntime_USE_TELEMETRY=OFF"]
    save("ort-controlled-configure-command.json", {"argv": [str(x) for x in args],
         "recipe_sha256": digest(__file__), "purpose": "build-only CPU candidate; no application replacement"})
    run(args, "ort-controlled-configure.log")
    shutil.copyfile(BUILD / "CMakeCache.txt", EVIDENCE / "ort-controlled-CMakeCache.txt")


def build(approved):
    if approved != digest(__file__):
        raise RuntimeError("build requires --reviewed-recipe-sha256 matching the Integration-reviewed script")
    if not (BUILD / "build.ninja").exists():
        raise RuntimeError("configure must complete before build")
    if shutil.disk_usage(BASE).free < 20 * 1024 ** 3:
        raise RuntimeError("controlled ORT build requires at least 20 GiB free")
    run([TOOLS / "cmake", "--build", BUILD, "--target", "onnxruntime", "--parallel", "4"],
        "ort-controlled-build.log")


def audit():
    """Save raw evidence; identify actual static link and actual included-header components."""
    library = BUILD / "libonnxruntime.1.22.1.dylib"
    linkmap = BUILD / "onnxruntime.linkmap"
    if not library.exists() or not linkmap.exists():
        raise RuntimeError("no completed candidate dylib/linkmap; cannot claim closure")
    ninja_headers = run([TOOLS / "ninja", "-C", BUILD, "-t", "deps"], "ort-controlled-ninja-deps.txt")
    run([TOOLS / "ninja", "-C", BUILD, "-t", "commands", "onnxruntime"],
        "ort-controlled-link-commands.txt")
    shutil.copyfile(linkmap, EVIDENCE / "ort-controlled-linkmap.txt")
    shutil.copyfile(BUILD / "CMakeCache.txt", EVIDENCE / "ort-controlled-CMakeCache.txt")
    shutil.copyfile(BUILD / "compile_commands.json", EVIDENCE / "ort-controlled-compile_commands.json")
    objects = linkmap.read_text().split("# Object files:", 1)[1].split("# Sections:", 1)[0]
    # Restrict header provenance to object files selected by the final linker.
    # A full Ninja deps dump also includes protoc/flatc: those must not be
    # accidentally promoted to shipped dependencies.
    compiled = {}
    for entry in json.loads((BUILD / "compile_commands.json").read_text()):
        argv = entry.get("arguments") or shlex.split(entry["command"])
        output = entry.get("output") or argv[argv.index("-o") + 1]
        path = Path(output)
        if path.is_absolute():
            path = path.relative_to(BUILD)
        compiled[str(path)] = entry
    dependencies, current = {}, None
    for line in ninja_headers.splitlines():
        if line and not line[0].isspace() and ": #deps " in line:
            current = line.split(": #deps ", 1)[0]
            dependencies[current] = []
        elif current and line.startswith("    "):
            dependencies[current].append(line.strip())
    archive_queries, linked, unresolved = {}, set(), []
    for line in objects.splitlines():
        match = re.fullmatch(r"\[\s*\d+\]\s+(.+)", line)
        if not match or match[1] == "linker synthesized":
            continue
        value = match[1]
        archive = re.fullmatch(r"(.+\.a)\((.+)\)", value)
        if archive:
            path = Path(archive[1])
            if path.is_absolute():
                path = path.relative_to(BUILD)
            name = str(path)
            if name not in archive_queries:
                archive_queries[name] = run([TOOLS / "ninja", "-C", BUILD, "-t", "query", name])
            members = [candidate.strip() for candidate in archive_queries[name].splitlines()
                       if candidate.strip() in compiled]
            selected = [member for member in members if Path(member).name == archive[2]]
        else:
            path = Path(value)
            if path.is_absolute():
                path = path.relative_to(BUILD)
            selected = [str(path)] if str(path) in compiled else []
        if len(selected) != 1:
            unresolved.append(value)
        else:
            linked.add(selected[0])
    missing_depfiles = sorted(linked - dependencies.keys())
    selected_headers = sorted({header for obj in linked for header in dependencies.get(obj, [])})
    save("ort-controlled-linked-compile-inputs.json", {"linked_objects": sorted(linked),
         "included_headers": selected_headers, "unresolved_link_objects": unresolved,
         "missing_depfiles": missing_depfiles, "archive_queries": archive_queries,
         "build_only_objects": sorted(compiled.keys() - linked)})
    observed = {}
    for kind, raw in (("linked_objects", objects), ("compiler_headers", "\n".join(selected_headers))):
        for name in set(re.findall(r"/_deps/([^/]+?)-(?:src|build)/", raw)):
            key = ALIASES.get(name.lower(), name.lower())
            observed.setdefault(key, {})[kind] = True
    inputs = {entry["name"]: entry for entry in json.loads(
        (EVIDENCE / "ort-controlled-source-inputs.json").read_text())["inputs"]}
    components, unmapped = [], list(unresolved) + ["no_depfile:" + obj for obj in missing_depfiles]
    for name, usage in sorted(observed.items()):
        if name not in inputs:
            unmapped.append(name)
            continue
        source_names = [name] + [alias for alias, canonical in ALIASES.items() if canonical == name]
        dirs = [BUILD / "_deps" / (alias + "-src") for alias in source_names]
        license_files = []
        for directory in dirs:
            if directory.exists():
                for path in sorted(directory.rglob("*")):
                    if path.is_file() and re.fullmatch(r"(?:LICENSE|COPYING|NOTICE|COPYRIGHT)(?:[._-].*)?",
                                                       path.name, re.I):
                        license_files.append({"path": str(path), "sha256": digest(path)})
        if not license_files:
            unmapped.append(name + ":no_license_file")
        components.append({**inputs[name], "observed_usage": usage, "license_files": license_files})
    save("ort-controlled-observed-closure.json", {"ort_commit": COMMIT,
         "artifact": str(library), "sha256": digest(library), "recipe_sha256": digest(__file__),
         "components": components, "unmapped": unmapped,
         "status": "REQUIRES_REVIEW" if not unmapped else "INCOMPLETE",
         "limits": "Only final linker-selected objects contribute headers. Duplicate archive member names or missing depfiles fail closure. License expressions and embedded third-party notices still require review; no automatic SBOM COMPLETE claim."})
    for executable, arguments, name in (("otool", ["-L", library], "dynamic-links"),
                                        ("nm", ["-gU", library], "exports"),
                                        ("file", [library], "file")):
        run([executable, *arguments], f"ort-controlled-{name}.txt")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "configure", "build", "audit"))
    parser.add_argument("--reviewed-recipe-sha256")
    args = parser.parse_args()
    {"prepare": prepare, "configure": configure, "build": lambda: build(args.reviewed_recipe_sha256),
     "audit": audit}[args.phase]()


if __name__ == "__main__":
    main()
