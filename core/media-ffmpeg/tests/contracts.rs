use media_domain::{BackendConfig, MediaRequest};
use std::path::PathBuf;
use std::sync::atomic::AtomicBool;

fn missing_backend() -> BackendConfig {
    BackendConfig {
        ffmpeg: PathBuf::from("/definitely-missing-media-core/ffmpeg"),
        ffprobe: PathBuf::from("/definitely-missing-media-core/ffprobe"),
        timeout_ms: 100,
        accurate_reencode_timeout_ms: 600_000,
        core003_enabled: false,
        core004_enabled: false,
        video_transform_enabled: false,
        mix_stems_enabled: false,
        transcode_timeout_ms: 600000,
        max_output_bytes: 1024,
        ai_backend: None,
    }
}

#[test]
fn cancelled_operation_does_not_spawn_or_create_output() {
    let request = MediaRequest::Probe {
        source: PathBuf::from("/definitely-missing-media-core/input.mp4"),
    };
    let error = media_ffmpeg::execute(&missing_backend(), &request, &AtomicBool::new(true))
        .expect_err("Cancellation must be checked before any media process");
    assert_eq!(error.code, "CANCELLED");
}

#[test]
fn missing_source_is_rejected_without_guessing_metadata() {
    let request = MediaRequest::Probe {
        source: PathBuf::from("/definitely-missing-media-core/input.mp4"),
    };
    let error = media_ffmpeg::execute(&missing_backend(), &request, &AtomicBool::new(false))
        .expect_err("Absent source must not report an empty successful probe");
    assert_eq!(error.code, "INVALID_INPUT");
}

#[test]
fn missing_tools_do_not_advertise_media_capability() {
    let capabilities = media_ffmpeg::backend_capabilities(&missing_backend());
    assert_eq!(capabilities["features"]["probe"]["available"], false);
    assert_eq!(
        capabilities["features"]["trim_stream_copy"]["available"],
        false
    );
    assert_eq!(
        capabilities["features"]["accurate_reencode"]["available"],
        false
    );
}
