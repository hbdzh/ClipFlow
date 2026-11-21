use media_domain::{AudioSelection, BackendConfig, MediaRequest, TranscodeProfile, VideoTransform};
use std::{path::PathBuf, sync::atomic::AtomicBool};
#[test]
fn transform_is_rejected_before_source_access_without_explicit_opt_in() {
    let config = BackendConfig {
        ffmpeg: PathBuf::from("/no/ffmpeg"),
        ffprobe: PathBuf::from("/no/ffprobe"),
        timeout_ms: 30000,
        accurate_reencode_timeout_ms: 600000,
        core003_enabled: true,
        core004_enabled: true,
        video_transform_enabled: false,
        mix_stems_enabled: false,
        transcode_timeout_ms: 600000,
        max_output_bytes: 8 * 1024 * 1024,
        ai_backend: None,
    };
    let request = MediaRequest::Transcode {
        source: PathBuf::from("/nonexistent/source.mp4"),
        destination: PathBuf::from("/nonexistent/output.mp4"),
        range: None,
        profile: TranscodeProfile::Standard,
        audio_selection: AudioSelection::All,
        video_transform: Some(VideoTransform {
            rotation_degrees: 90,
            scale_percent: 100,
            offset_x: 0,
            offset_y: 0,
        }),
    };
    let error = match media_ffmpeg::preflight(&config, &request, &AtomicBool::new(false)) {
        Ok(_) => panic!("disabled transform accepted"),
        Err(error) => error,
    };
    assert_eq!(error.code, "UNSUPPORTED_OPERATION");
}
