#![forbid(unsafe_code)]

//! Bounded local FFmpeg adapter. Explicit opt-in reencoding; no shell, network or UI.

mod accurate;
mod audio_mix;
mod audio_preview;
pub use audio_mix::publish_guard as mix_publish_guard;
mod audio_probe;
mod prepared;
mod transcode;
pub use prepared::{
    PreparedMedia, destinations_equivalent, execute_prepared, preflight, quarantined,
    resource_snapshot, storage_preview,
};
mod files;
mod media;
mod process;
mod quality;
mod references;
mod scope;
mod time;
mod trim;

use media_domain::{BackendConfig, MAX_MEDIA_RESULT_BYTES, MediaError, MediaRequest, TrimStrategy};
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
    // Old configurations never run a new encoder probe: their observable mock
    // and stream-copy capability behavior remains backward compatible.
    let encoder_check = if available && config.core003_enabled {
        Some(accurate::encoders(&context))
    } else {
        None
    };
    let mix_check = if available
        && config.mix_stems_enabled
        && config.core003_enabled
        && config.core004_enabled
    {
        Some(audio_mix::check_dependencies(&context))
    } else {
        None
    };
    let mix_available = mix_check.as_ref().is_some_and(Result::is_ok);
    let accurate_available = encoder_check.as_ref().is_some_and(Result::is_ok);
    let accurate_reason = if !config.core003_enabled {
        "core003_not_enabled"
    } else if !available {
        reason
    } else {
        encoder_check.as_ref().map_or("DEPENDENCY_MISSING", |r| {
            r.as_ref().err().map_or("available", |e| e.code)
        })
    };
    json!({
        "schema_version":0,"core_version":env!("CARGO_PKG_VERSION"),"platform":format!("{}-{}",std::env::consts::OS,std::env::consts::ARCH),"mock_only":false,
        "features":{
            "probe":{"available":available,"reason":reason},"trim_stream_copy":{"available":available,"reason":reason},
            "prepare_audio_preview":{"available":available && config.core003_enabled && config.core004_enabled,"reason":if config.core003_enabled && config.core004_enabled {reason} else {"core003_and_core004_not_enabled"},"profile":"wav_pcm_f32_preview_v1","preview_only":true},
            "mix_stems_v1":{"available":mix_available,"reason":if !config.mix_stems_enabled {"explicit_opt_in_required"} else if !available {reason} else {mix_check.as_ref().and_then(|r|r.as_ref().err()).map_or("available",|e|e.code)}},
            "video_transform_v1":{"available":accurate_available && config.core004_enabled && config.video_transform_enabled,"reason":if !config.video_transform_enabled {"explicit_opt_in_required"} else if !config.core004_enabled {"core004_not_enabled"} else {accurate_reason},"requires_transcode":true},
            "storage_preview":{"available":available && config.core004_enabled,"reason":if config.core004_enabled {reason}else{"core004_not_enabled"}},
            "trim_stream_copy_v1":{"available":available && config.core003_enabled,"reason":if config.core003_enabled {reason} else {"core003_not_enabled"},"profile":"mp4_h264_copy_v1"},
            "accurate_reencode":{"available":accurate_available,"reason":accurate_reason,"profile":"mp4_h264_standard_v1"},"transcode":{"available":accurate_available && config.core004_enabled,"reason":if config.core004_enabled {accurate_reason}else{"core004_not_enabled"},"profiles":["mp4_h264_standard_v1","mp4_h264_720p_v1"]},"batch_queue":{"available":available && config.core004_enabled,"reason":if config.core004_enabled {reason}else{"core004_not_enabled"}},"ai_separation":{"available":false,"reason":"not_implemented"},"mock_job":{"available":true}
        },
        "media_backend":{"name":"ffmpeg_cli","ffmpeg_version":versions.as_ref().map(|v|&v.0),"ffprobe_version":versions.as_ref().map(|v|&v.1)},
        "limits":{"accepted_platform":"macOS","demuxer":"mov","container":"mp4","video":"h264","audio":"aac","maximum_streams":16,"maximum_frames":100000,
            "maximum_result_bytes":MAX_MEDIA_RESULT_BYTES,"configured_process_output_bytes":config.max_output_bytes,"configured_timeout_ms":config.timeout_ms,
            "probe_video_codec":"demuxer_supported_single_video","trim_video_codec":"h264","trim_audio_codecs":["aac"],
            "trim_rotation_and_explicit_colour_metadata":"unsupported_pending_acceptance",
            "trim_alignment":"enclose_keyframes","full_frame_and_packet_validation":true,"perceptual_av_sync_verified":false}
        ,"core003_probe_audio_limits":{"enabled":config.core003_enabled,"containers":["wav","flac","mp3","m4a"],"codecs":["pcm_s16le","pcm_s24le","pcm_f32le","flac","mp3","aac_lc"],"sample_rates":[44100,48000],"channels":[1,2],"minimum_duration_us":1000000,"maximum_duration_us":600000000,"maximum_streams":1,"audio_trim_available":false}
        ,"core003_limits":{"enabled":config.core003_enabled,"maximum_duration_us":600000000,"maximum_long_edge":1920,"maximum_short_edge":1080,"maximum_audio_tracks":2,"video":"h264 yuv420p CFR","audio":"AAC-LC 44100/48000Hz mono/stereo","colour":"BT.709 tv or unspecified","accurate_reencode_timeout_ms":config.accurate_reencode_timeout_ms,"accurate_software_encoder":"libx264 CRF23 medium; AAC 192k stereo / 96k mono","audio_content_comparison":"8kHz band-limited, <=60s windows, per channel; not a perceptual listening claim"}
    })
}

fn probe_result(info: Value) -> Result<Value, MediaError> {
    let result =
        json!({"schema_version":0,"mock_only":false,"file_created":false,"media_info":info});
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

/// Execute one real media request; callers publish terminal state only after this
/// function returns, including child reaping and explicit output cleanup.
pub fn execute(
    config: &BackendConfig,
    request: &MediaRequest,
    cancel: &AtomicBool,
) -> Result<Value, MediaError> {
    if prepared::QUARANTINED.load(std::sync::atomic::Ordering::Acquire) {
        return Err(MediaError::new(
            "CLEANUP_FAILED",
            "Backend cleanup remains unconfirmed until process restart.",
        ));
    }
    if matches!(
        request,
        MediaRequest::Transcode { .. }
            | MediaRequest::PrepareAudioPreview { .. }
            | MediaRequest::MixStems { .. }
    ) {
        let prepared = preflight(config, request, cancel)?;
        return execute_prepared(config, &prepared, cancel);
    }
    let strategy = match request {
        MediaRequest::Trim { strategy, .. } => Some(*strategy),
        _ => None,
    };
    if strategy.is_some_and(|s| s != TrimStrategy::StreamCopy) && !config.core003_enabled {
        return Err(MediaError::new(
            "UNSUPPORTED_BACKEND",
            "CORE-003 profiles require explicit backend opt-in.",
        ));
    }
    let timeout = if strategy == Some(TrimStrategy::AccurateReencode) {
        config.accurate_reencode_timeout_ms
    } else {
        config.timeout_ms
    };
    let context = process::Context::with_timeout(config, cancel, timeout);
    context.check()?;
    let path = match request {
        MediaRequest::MixStems { vocals: source, .. }
        | MediaRequest::Probe { source }
        | MediaRequest::PrepareAudioPreview { source, .. }
        | MediaRequest::Trim { source, .. }
        | MediaRequest::Transcode { source, .. } => source,
    };
    let source = files::Source::open(path)?;
    if !cfg!(target_os = "macos") {
        return Err(MediaError::new(
            "UNSUPPORTED_BACKEND",
            "This local file-identity backend has only been accepted on macOS.",
        ));
    }
    tools(&context)?;
    if matches!(request, MediaRequest::Probe { .. })
        && config.core003_enabled
        && let Some(info) = audio_probe::inspect(&context, &source.path)?
    {
        source.unchanged()?;
        context.check()?;
        return probe_result(info);
    }
    let media = media::inspect(&context, &source.path)?;
    match request {
        MediaRequest::Probe { .. } => {
            source.unchanged()?;
            context.check()?;
            let mut info = media.info.clone();
            if config.core003_enabled
                && let Some(rate) = media::verified_cfr_rate(&media)
            {
                info["video_cfr_rate"] = json!(rate);
            }
            probe_result(info)
        }
        MediaRequest::Transcode { .. }
        | MediaRequest::PrepareAudioPreview { .. }
        | MediaRequest::MixStems { .. } => {
            unreachable!("handled above")
        }
        MediaRequest::Trim {
            destination,
            start_us,
            end_us,
            strategy,
            ..
        } => match strategy {
            TrimStrategy::AccurateReencode => {
                accurate::execute(&context, &source, destination, media, *start_us, *end_us)
            }
            TrimStrategy::StreamCopy | TrimStrategy::StreamCopyV1 => trim::execute(
                &context,
                &source,
                destination,
                media,
                *start_us,
                *end_us,
                *strategy == TrimStrategy::StreamCopyV1,
            ),
        },
    }
}
