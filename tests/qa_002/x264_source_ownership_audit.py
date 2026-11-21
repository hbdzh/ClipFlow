#!/usr/bin/env python3
"""Pinned-source ownership audit. No encoder execution or crafted media."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile

REV = "31e19f92f00c7003fa115047ce50978bc98c3a0d"
ARCHIVE_SHA = "d053c9d86988d6bc78237ca5205865c5ddf99c98ef4cd9927eec8f6d388f6dd9"
PATCH_SHA = "736368e6444002409a8cc88017c79776c107ee59a642db2b695479294a21977d"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--archive", type=Path, required=True)
    p.add_argument("--x264-source", type=Path, required=True)
    p.add_argument("--ffmpeg-source", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--expect", choices=("patched", "unpatched"), required=True)
    a = p.parse_args()
    patch = Path(__file__).with_name("x264_encoder_open_param_cleanup.patch")
    assert sha(a.archive.read_bytes()) == ARCHIVE_SHA, "unreviewed source archive"
    assert sha(patch.read_bytes()) == PATCH_SHA, "unreviewed patch"
    paths = ("encoder/encoder.c", "common/base.c", "common/base.h")
    original = {}
    with tarfile.open(a.archive) as t:
        for rel in paths:
            members = [m for m in t.getmembers() if m.name.endswith("/" + rel)]
            assert len(members) == 1, (rel, len(members))
            original[rel] = t.extractfile(members[0]).read()
    old = "fail:\n    x264_free( h );\n    return NULL;\n}"
    new = "fail:\n    if( h != NULL )\n        x264_param_cleanup( &h->param );\n    x264_free( h );\n    return NULL;\n}"
    baseline = original["encoder/encoder.c"].decode()
    assert baseline.count(old) == 1, "failure block drift"
    expected = baseline.replace(old, new) if a.expect == "patched" else baseline
    sources = {rel: (a.x264_source / rel).read_bytes() for rel in paths}
    assert sources["encoder/encoder.c"].decode() == expected, "source differs beyond selected patch"
    for rel in paths[1:]:
        assert sources[rel] == original[rel], "ownership support source drift: " + rel
    encoder = sources["encoder/encoder.c"].decode()
    base = sources["common/base.c"].decode()
    wrapper = (a.ffmpeg_source / "libavcodec/libx264.c").read_text()
    codec = (a.ffmpeg_source / "libavcodec/avcodec.c").read_text()
    facts = {
        "encoder_internal_param_separate_owner": "h->param.opaque = NULL;" in encoder,
        "default_stat_names_are_nonnull": 'param->rc.psz_stat_out = "x264_2pass.log";' in base
            and 'param->rc.psz_stat_in = "x264_2pass.log";' in base,
        "encoder_clones_stat_names_before_validate": encoder.index("CHECKED_PARAM_STRDUP( h->param.rc.psz_stat_out")
            < encoder.index("if( validate_parameters( h, 1 ) < 0 )"),
        "cleanup_frees_tracked_strings_and_owner": "free( buf->ptr[i] );" in base
            and "free( buf );\n        param->opaque = NULL;" in base,
        "wrapper_null_encoder_failure": "x4->enc = x264_encoder_open(&x4->params);\n    if (!x4->enc)\n        return AVERROR_EXTERNAL;" in wrapper,
        "wrapper_cleans_caller_params": "x264_param_cleanup(&x4->params);" in wrapper,
        "wrapper_closes_only_nonnull_encoder": "if (x4->enc) {\n        x264_encoder_close(x4->enc);" in wrapper,
        "codec_init_cleanup_enabled": "FF_CODEC_CAP_INIT_CLEANUP" in wrapper
            and "avci->needs_close = codec2->caps_internal & FF_CODEC_CAP_INIT_CLEANUP;" in codec
            and "ffcodec(avctx->codec)->close(avctx);" in codec,
        "failure_param_cleanup_guarded": new in encoder,
    }
    assert all(v for k, v in facts.items() if k != "failure_param_cleanup_guarded"), facts
    assert facts["failure_param_cleanup_guarded"] == (a.expect == "patched")
    result = {
        "scope": "defensive exact-source static ownership audit; no encoder execution, failure reproduction or malicious media",
        "x264_revision": REV, "archive_sha256": ARCHIVE_SHA, "patch_sha256": PATCH_SHA,
        "source_state": a.expect, "checks_passed": True, "facts": facts,
        "source_files": {str(a.x264_source / rel): sha(data) for rel, data in sources.items()},
        "ffmpeg_files": {str(a.ffmpeg_source / rel): sha((a.ffmpeg_source / rel).read_bytes())
                         for rel in ("libavcodec/libx264.c", "libavcodec/avcodec.c")},
        "limits": ["Syntax/ownership anchors support the accompanying manual review; they are not a formal proof.",
                   "Selected patch covers h->param tracked strings only, not all later initialization resources.",
                   "No claim of AAC decoding or arbitrary-code-execution impact is made.",
                   "Static checks do not validate a previously built binary or GUI."],
    }
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"output": str(a.output), "source_state": a.expect, "checks_passed": True}))


if __name__ == "__main__":
    main()
