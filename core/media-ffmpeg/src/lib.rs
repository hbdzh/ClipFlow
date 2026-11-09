#![forbid(unsafe_code)]

//! CORE-001 placeholder adapter: no FFmpeg discovery, process execution or I/O.

use serde_json::{Value, json};

pub fn capabilities() -> Value {
    json!({
        "schema_version": 0,
        "core_version": env!("CARGO_PKG_VERSION"),
        "platform": format!("{}-{}", std::env::consts::OS, std::env::consts::ARCH),
        "mock_only": true,
        "features": {
            "probe": {"available": false, "reason": "not_implemented"},
            "trim_stream_copy": {"available": false, "reason": "not_implemented"},
            "accurate_reencode": {"available": false, "reason": "not_implemented"},
            "ai_separation": {"available": false, "reason": "not_implemented"},
            "mock_job": {"available": true}
        },
        "media_backend": {"name": "mock", "version": "core-001"}
    })
}
