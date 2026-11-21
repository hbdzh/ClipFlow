//! Controlled developer harness for a single explicitly lossy transform.
use media_domain::{AudioSelection, BackendConfig, MediaRequest, TranscodeProfile, VideoTransform};
use std::{
    path::PathBuf,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    time::Duration,
};
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args().collect();
    if !matches!(args.len(), 7 | 8) {
        return Err(
            "usage: transform_smoke SOURCE DEST ROTATION SCALE OFFSET_X OFFSET_Y [CANCEL_MS]"
                .into(),
        );
    }
    let config = BackendConfig {
        ffmpeg: PathBuf::from("/opt/homebrew/bin/ffmpeg"),
        ffprobe: PathBuf::from("/opt/homebrew/bin/ffprobe"),
        timeout_ms: 30000,
        accurate_reencode_timeout_ms: 600000,
        transcode_timeout_ms: 600000,
        core003_enabled: true,
        core004_enabled: true,
        video_transform_enabled: true,
        mix_stems_enabled: false,
        max_output_bytes: 16 * 1024 * 1024,
        ai_backend: None,
    };
    let request = MediaRequest::Transcode {
        source: PathBuf::from(&args[1]),
        destination: PathBuf::from(&args[2]),
        range: None,
        profile: TranscodeProfile::Standard,
        audio_selection: AudioSelection::All,
        video_transform: Some(VideoTransform {
            rotation_degrees: args[3].parse()?,
            scale_percent: args[4].parse()?,
            offset_x: args[5].parse()?,
            offset_y: args[6].parse()?,
        }),
    };
    let cancel = Arc::new(AtomicBool::new(false));
    if let Some(ms) = args.get(7) {
        let delay: u64 = ms.parse()?;
        let token = Arc::clone(&cancel);
        std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(delay));
            token.store(true, Ordering::Release);
        });
    }
    match media_ffmpeg::execute(&config, &request, &cancel) {
        Ok(result) => println!("{result}"),
        Err(error) => {
            eprintln!("{}: {}", error.code, error.message);
            std::process::exit(1)
        }
    }
    Ok(())
}
