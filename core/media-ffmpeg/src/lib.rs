#![forbid(unsafe_code)]

//! Bounded local FFmpeg adapter. No shell, reencoding, networking or native UI.

mod files;
mod media;
mod process;
mod time;
mod trim;

use media_domain::{BackendConfig, MAX_MEDIA_RESULT_BYTES, MediaError, MediaRequest};
use serde_json::{Value, json};
use std::sync::atomic::AtomicBool;

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

fn tools(context: &process::Context<'_>) -> Result<(String, String), MediaError> {
    let mut versions = Vec::new();
    for (path, prefix) in [
        (&context.config.ffmpeg, "ffmpeg version "),
        (&context.config.ffprobe, "ffprobe version "),
    ] {
        let bytes = context.run(path, &process::args(&["-version"]))?;
        let text = std::str::from_utf8(&bytes).map_err(|_| {
            MediaError::new("DEPENDENCY_MISSING", "Media tool version is unreadable.")
        })?;
        let line = text.lines().next().ok_or_else(|| {
            MediaError::new(
                "DEPENDENCY_MISSING",
                "Media tool did not report its version.",
            )
        })?;
        let version = line
            .strip_prefix(prefix)
            .and_then(|rest| rest.split_whitespace().next())
            .ok_or_else(|| {
                MediaError::new(
                    "DEPENDENCY_MISSING",
                    "Media tool identity is not recognized.",
                )
            })?;
        if version.len() > 128 || version.split('.').next() != Some("7") {
            return Err(MediaError::new(
                "UNSUPPORTED_BACKEND",
                "This bounded adapter has only accepted FFmpeg/ffprobe major version 7.",
            ));
        }
        versions.push(version.to_owned());
    }
    Ok((versions.remove(0), versions.remove(0)))
}

/// Capabilities of an explicitly configured local backend. The default Core
/// remains mock-only, and no media flags are inferred merely from the host OS.
pub fn backend_capabilities(config: &BackendConfig) -> Value {
    let cancel = AtomicBool::new(false);
    let context = process::Context::new(config, &cancel);
    let checked = tools(&context);
    let available = checked.is_ok() && cfg!(target_os = "macos");
    let reason = checked.as_ref().err().map_or_else(
        || {
            if available {
                "available"
            } else {
                "platform_not_accepted"
            }
        },
        |error| error.code,
    );
    let versions = checked.ok();
    json!({
        "schema_version":0,"core_version":env!("CARGO_PKG_VERSION"),"platform":format!("{}-{}",std::env::consts::OS,std::env::consts::ARCH),"mock_only":false,
        "features":{
            "probe":{"available":available,"reason":reason},"trim_stream_copy":{"available":available,"reason":reason},
            "accurate_reencode":{"available":false,"reason":"not_implemented"},"ai_separation":{"available":false,"reason":"not_implemented"},"mock_job":{"available":true}
        },
        "media_backend":{"name":"ffmpeg_cli","ffmpeg_version":versions.as_ref().map(|v|&v.0),"ffprobe_version":versions.as_ref().map(|v|&v.1)},
        "limits":{"accepted_platform":"macOS","demuxer":"mov","container":"mp4","video":"h264","audio":"aac","maximum_streams":16,"maximum_frames":100000,
            "maximum_result_bytes":MAX_MEDIA_RESULT_BYTES,"configured_process_output_bytes":config.max_output_bytes,"configured_timeout_ms":config.timeout_ms,
            "probe_video_codec":"demuxer_supported_single_video","trim_video_codec":"h264","trim_audio_codecs":["aac"],
            "trim_rotation_and_explicit_colour_metadata":"unsupported_pending_acceptance",
            "trim_alignment":"enclose_keyframes","full_frame_and_packet_validation":true,"perceptual_av_sync_verified":false}
    })
}

/// Execute one real media request; callers publish terminal state only after this
/// function returns, including child reaping and explicit output cleanup.
pub fn execute(
    config: &BackendConfig,
    request: &MediaRequest,
    cancel: &AtomicBool,
) -> Result<Value, MediaError> {
    let context = process::Context::new(config, cancel);
    context.check()?;
    let path = match request {
        MediaRequest::Probe { source } | MediaRequest::Trim { source, .. } => source,
    };
    let source = files::Source::open(path)?;
    if !cfg!(target_os = "macos") {
        return Err(MediaError::new(
            "UNSUPPORTED_BACKEND",
            "This local file-identity backend has only been accepted on macOS.",
        ));
    }
    tools(&context)?;
    let media = media::inspect(&context, &source.path)?;
    match request {
        MediaRequest::Probe { .. } => {
            source.unchanged()?;
            context.check()?;
            let result = json!({"schema_version":0,"mock_only":false,"file_created":false,"media_info":media.info});
            let size = serde_json::to_vec(&result)
                .map_err(|_| MediaError::new("INTERNAL", "Probe result cannot be serialized."))?
                .len();
            if size > MAX_MEDIA_RESULT_BYTES {
                return Err(MediaError::new(
                    "RESOURCE_LIMIT",
                    "Probe result exceeds its retained-result bound.",
                ));
            }
            Ok(result)
        }
        MediaRequest::Trim {
            destination,
            start_us,
            end_us,
            ..
        } => trim::execute(&context, &source, destination, media, *start_us, *end_us),
    }
}
