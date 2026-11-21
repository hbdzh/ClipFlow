#!/usr/bin/env python3
"""Observe the completed controlled ORT artifact; never compiles or installs it."""
import json
from pathlib import Path
import re
import shlex
import shutil
import hashlib
import zipfile
from urllib.parse import urlparse

import build_qa002_ort as recipe

BUILD, SOURCE, EVIDENCE = recipe.BUILD, recipe.SOURCE, recipe.EVIDENCE
LIBRARY = BUILD / "libonnxruntime.1.22.1.dylib"
LICENSES = {
    "abseil_cpp": "Apache-2.0", "re2": "BSD-3-Clause", "protobuf": "BSD-3-Clause",
    "date": "MIT", "json": "MIT", "pytorch_cpuinfo": "BSD-2-Clause",
    "microsoft_gsl": "MIT", "safeint": "MIT", "flatbuffers": "Apache-2.0",
    "onnx": "Apache-2.0", "eigen": "MPL-2.0 AND Apache-2.0 AND BSD-3-Clause AND LicenseRef-Eigen-Embedded-Notices",
    "mp11": "BSL-1.0",
    "protoc_mac_universal": "BSD-3-Clause",
}


def normal(path):
    path = Path(path)
    if path.is_absolute():
        return str(path.relative_to(BUILD)) if path.is_relative_to(BUILD) else str(path)
    return str(path)


def origin(path):
    absolute = Path(path)
    if not absolute.is_absolute():
        absolute = BUILD / absolute
    absolute = absolute.resolve()
    if absolute.is_relative_to(BUILD / "_deps"):
        folder = absolute.relative_to(BUILD / "_deps").parts[0]
        match = re.fullmatch(r"(.+)-(src|build)", folder)
        if match:
            name = recipe.ALIASES.get(match[1].lower(), match[1].lower())
            return name, match[2]
    if absolute.is_relative_to(SOURCE):
        return "onnxruntime", "src"
    if absolute.is_relative_to(BUILD):
        return "onnxruntime", "generated"
    if str(absolute).startswith(("/Applications/Xcode.app/", "/Library/Developer/", "/usr/")):
        return "apple-system-toolchain", "system"
    return "UNMAPPED", str(absolute)


def main():
    if not LIBRARY.exists():
        raise RuntimeError("no completed candidate library")
    rawmap = (BUILD / "onnxruntime.linkmap").read_text()
    recipe.run([recipe.TOOLS / "ninja", "-C", BUILD, "-t", "commands", "onnxruntime"],
               "ort-controlled-link-commands.txt")
    deps_raw = recipe.run([recipe.TOOLS / "ninja", "-C", BUILD, "-t", "deps"],
                          "ort-controlled-ninja-deps.txt")
    compiled = {}
    for entry in json.loads((BUILD / "compile_commands.json").read_text()):
        argv = entry.get("arguments") or shlex.split(entry["command"])
        key = normal(entry.get("output") or argv[argv.index("-o") + 1])
        compiled[key] = entry
    dependencies, current = {}, None
    for line in deps_raw.splitlines():
        if line and not line[0].isspace() and ": #deps " in line:
            current = normal(line.split(": #deps ", 1)[0])
            dependencies[current] = []
        elif current and line.startswith("    "):
            dependencies[current].append(line.strip())
    map_symbols = {}
    for line in rawmap.splitlines():
        match = re.fullmatch(r"0x[0-9A-Fa-f]+\s+0x[0-9A-Fa-f]+\s+\[\s*(\d+)\]\s+(.+)", line)
        if match:
            map_symbols.setdefault(int(match[1]), set()).add(match[2])
    object_lines = rawmap.split("# Object files:", 1)[1].split("# Sections:", 1)[0]
    queries, linked, mappings, system, unresolved, archives = {}, set(), [], [], [], {}
    for line in object_lines.splitlines():
        match = re.fullmatch(r"\[\s*(\d+)\]\s+(.+)", line)
        if not match or match[2] == "linker synthesized":
            continue
        object_id, value = int(match[1]), match[2]
        if value.endswith(".tbd") and origin(value)[0] == "apple-system-toolchain":
            system.append({"map_object_id": object_id, "path": value})
            continue
        archive = re.fullmatch(r"(.+\.a)\((.+)\)", value)
        scores = None
        if archive:
            name = normal(archive[1])
            if name not in queries:
                queries[name] = recipe.run([recipe.TOOLS / "ninja", "-C", BUILD, "-t", "query", name])
                archives[name] = {"path": name, "sha256": recipe.digest(BUILD / name),
                                  "component": origin(name)[0]}
            members = [part.strip() for part in queries[name].splitlines() if part.strip() in compiled]
            candidates = [member for member in members if Path(member).name == archive[2]]
        else:
            name = normal(value)
            candidates = [name] if name in compiled else []
        if len(candidates) > 1:
            # Apple ld permits duplicate archive member names. Match this map
            # object ID's actual defined symbols to each original compile object.
            scores = {}
            for candidate in candidates:
                defined = set(recipe.run(["nm", "-Uj", BUILD / candidate]).splitlines())
                scores[candidate] = sorted(defined & map_symbols.get(object_id, set()))
            highest = max(map(len, scores.values()))
            candidates = [candidate for candidate, symbols in scores.items()
                          if len(symbols) == highest and highest > 0]
        if len(candidates) != 1:
            unresolved.append({"map_object_id": object_id, "entry": value,
                               "candidates": candidates, "matching_defined_symbols": scores})
            continue
        selected = candidates[0]
        linked.add(selected)
        mappings.append({"map_object_id": object_id, "entry": value, "object": selected,
                         "object_sha256": recipe.digest(BUILD / selected),
                         "source": compiled[selected]["file"], "symbol_disambiguation": scores})
    missing = sorted(linked - dependencies.keys())
    headers = sorted({value for key in linked for value in dependencies.get(key, [])})
    actual_files = sorted({entry["source"] for entry in mappings} | set(headers))
    material, embedded = [], []
    notice_pattern = re.compile(
        r"copyright|SPDX-License-Identifier|Licensed under|[Ll]icense, [Vv]ersion|"
        r"[Dd]erived from|[Aa]dapted from|public domain|Mozilla|Public License", re.I)
    for filename in actual_files:
        component, kind = origin(filename)
        if component == "apple-system-toolchain":
            continue
        path = Path(filename)
        lines = [{"line": number, "text": line.strip()}
                 for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1)
                 if notice_pattern.search(line)]
        record = {"path": filename, "component": component, "kind": kind,
                  "sha256": recipe.digest(path), "bytes": path.stat().st_size, "notice_lines": lines}
        material.append(record)
        if component == "onnxruntime" and any(re.search(
                r"TensorFlow|Mozilla|Apache|LLVM|GNU|Intel|Google|Arm Limited|ARM Limited|BSD|"
                r"Hewlett|University|Marat|Netlib|Julia|Public Domain|David|Folly|Facebook|SciPy|LAPACK", line["text"], re.I)
                for line in lines):
            embedded.append(record)
    recipe.save("ort-controlled-actual-file-provenance.json", material)
    recipe.save("ort-controlled-embedded-review-candidates.json", embedded)
    observed, unmapped = {}, []
    for item in mappings:
        component, kind = origin(item["source"])
        if component == "UNMAPPED":
            unmapped.append(item["source"])
        observed.setdefault(component, {}).setdefault("compiled_source_files", []).append(item["source"])
    for header in headers:
        component, kind = origin(header)
        if component == "UNMAPPED":
            unmapped.append(header)
        if component == "apple-system-toolchain":
            continue
        field = "generated_headers" if kind in ("build", "generated") else "included_headers"
        observed.setdefault(component, {}).setdefault(field, []).append(header)
    inputs = {row["name"]: row for row in json.loads(
        (EVIDENCE / "ort-controlled-source-inputs.json").read_text())["inputs"]}
    # Official ORT CMake applies pinned upstream dependency patches before build.
    # Reconcile actual used source against the original downloaded ZIP contents.
    dependency_changes = []
    for name, source_input in inputs.items():
        selected = [row for row in material if row['component'] == name and row['kind'] == 'src']
        if not selected:
            continue
        parsed = urlparse(source_input['url'])
        archive_path = SOURCE / 'mirror' / parsed.netloc / parsed.path.lstrip('/')
        with zipfile.ZipFile(archive_path) as archive:
            prefix = archive.namelist()[0].split('/')[0] + '/'
            root = next(parent for parent in Path(selected[0]['path']).parents if parent.name.endswith('-src'))
            for row in selected:
                relative = str(Path(row['path']).relative_to(root))
                preimage = hashlib.sha256(archive.read(prefix + relative)).hexdigest()
                if preimage != row['sha256']:
                    dependency_changes.append({'component': name, 'file': relative,
                        'source_archive_sha256': source_input['sha256'],
                        'preimage_sha256': preimage, 'postimage_sha256': row['sha256']})
    upstream_patches = []
    for stamp in sorted((BUILD / '_deps').glob('*-subbuild/*-populate-prefix/src/*-populate-stamp/*-patch-info.txt')):
        command = next((line[8:] for line in stamp.read_text().splitlines() if line.startswith('command=')), '')
        for patch in re.findall(r'[^;]+\.patch', command):
            path = Path(patch)
            upstream_patches.append({'file': str(path.relative_to(SOURCE)), 'sha256': recipe.digest(path),
                                     'command': command, 'stamp_sha256': recipe.digest(stamp)})
    patch_receipt = {'ort_commit': recipe.COMMIT, 'official_cmake_patches': upstream_patches,
                     'changed_material_files': dependency_changes,
                     'scope': 'Upstream ORT dependency patches, distinct from the one MediaStudio math_cpu.cc local patch.'}
    recipe.save('ort-controlled-upstream-source-patches.json', patch_receipt)
    components = []
    for component, usage in sorted(observed.items()):
        provenance = inputs.get(component, {})
        if component == "onnxruntime":
            provenance = {"name": component, "version": "1.22.1", "commit": recipe.COMMIT,
                          "url": f"https://codeload.github.com/microsoft/onnxruntime/tar.gz/{recipe.COMMIT}",
                          "sha256": recipe.SOURCE_HASH}
        elif not provenance:
            unmapped.append("component:" + component)
        components.append({**provenance, "name": component,
                           "license_expression": LICENSES.get(component,
                               "MIT AND Apache-2.0 AND MPL-2.0 AND BSD-3-Clause AND (Apache-2.0 WITH LLVM-exception)"),
                           "observed_usage": {key: sorted(set(value)) for key, value in usage.items()}})
    inactive = [{**entry, "classification": "host-tool-only" if key == "protoc_mac_universal" else "prefetched-not-observed-in-final-artifact"}
                for key, entry in sorted(inputs.items()) if key not in observed]
    recipe.save("ort-controlled-linked-compile-inputs.json", {"linked_objects": sorted(linked),
                "mappings": mappings, "included_headers": headers, "actual_files": actual_files,
                "unresolved_link_objects": unresolved, "missing_depfiles": missing,
                "archive_queries": queries, "static_archives": list(archives.values()),
                "system_link_map_objects": system,
                "build_only_objects": sorted(compiled.keys() - linked)})
    derivative_path = EVIDENCE / "ort-controlled-derivative-receipt.json"
    derivative = json.loads(derivative_path.read_text()) if derivative_path.exists() else None
    if derivative and derivative["sha256"] != recipe.digest(LIBRARY):
        raise RuntimeError("derivative receipt does not bind the actual library")
    recipe.save("ort-controlled-observed-closure.json", {"artifact": str(LIBRARY),
                "sha256": recipe.digest(LIBRARY), "ort_commit": recipe.COMMIT,
                "build_recipe_sha256": recipe.digest(recipe.__file__),
                "audit_recipe_sha256": recipe.digest(__file__), "components": components,
                "derivative_receipt": derivative,
                "upstream_dependency_source_patches": patch_receipt,
                "other_inputs": inactive, "static_archives": list(archives.values()),
                "unmapped": unmapped, "unresolved": unresolved, "missing_depfiles": missing,
                "status": "INCOMPLETE" if unresolved or missing or unmapped else "REQUIRES_LICENSE_AND_EMBEDDED_REVIEW",
                "limits": "Compiler depfiles and selected link objects establish actual material inputs. Embedded source attributions, generated code, and license notices require separate reviewed evidence."})
    for source, name in ((BUILD / "onnxruntime.linkmap", "linkmap.txt"),
                         (BUILD / "CMakeCache.txt", "CMakeCache.txt"),
                         (BUILD / "compile_commands.json", "compile_commands.json")):
        shutil.copyfile(source, EVIDENCE / ("ort-controlled-" + name))
    for executable, args, name in (("otool", ["-L", LIBRARY], "dynamic-links"),
                                   ("nm", ["-gU", LIBRARY], "exports"),
                                   ("file", [LIBRARY], "file")):
        recipe.run([executable, *args], f"ort-controlled-{name}.txt")
    print(json.dumps({"linked_objects": len(linked), "actual_headers": len(headers),
                      "components": [row["name"] for row in components], "unresolved": len(unresolved),
                      "missing_depfiles": len(missing), "unmapped": len(unmapped)}))


if __name__ == "__main__":
    main()
