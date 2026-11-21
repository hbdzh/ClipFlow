//! Developer-only real backend harness; no fabricated result or golden override.
use media_domain::{AudioSelection, BackendConfig, MediaRequest, TranscodeProfile};
use std::{path::PathBuf, sync::atomic::AtomicBool};
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = std::env::args().collect::<Vec<_>>();
    if !matches!(args.len(), 5 | 7) {
        return Err("usage: core004_smoke SOURCE DESTINATION p01|p02 all|none|STREAM_INDEX [START_US END_US]".into());
    }
    let config = BackendConfig {
        ffmpeg: PathBuf::from("/opt/homebrew/bin/ffmpeg"),
        ffprobe: PathBuf::from("/opt/homebrew/bin/ffprobe"),
        timeout_ms: 30000,
        accurate_reencode_timeout_ms: 600000,
        transcode_timeout_ms: 600000,
        core003_enabled: true,
        core004_enabled: true,
        video_transform_enabled: false,
        mix_stems_enabled: false,
        max_output_bytes: 16 * 1024 * 1024,
        ai_backend: None,
    };
    let req = MediaRequest::Transcode {
        source: PathBuf::from(&args[1]),
        destination: PathBuf::from(&args[2]),
        range: if args.len() == 7 {
            Some(media_domain::MediaRange {
                start_us: args[5].parse()?,
                end_us: args[6].parse()?,
            })
        } else {
            None
        },
        profile: if args[3] == "p02" {
            TranscodeProfile::Small720
        } else {
            TranscodeProfile::Standard
        },
        audio_selection: match args[4].as_str() {
            "all" => AudioSelection::All,
            "none" => AudioSelection::None,
            n => AudioSelection::One {
                stream_index: n.parse()?,
            },
        },
        video_transform: None,
    };
    let result = media_ffmpeg::execute(&config, &req, &AtomicBool::new(false));
    match result {
        Ok(value) => println!("{value}"),
        Err(e) => {
            eprintln!("{}: {}", e.code, e.message);
            std::process::exit(1)
        }
    }
    Ok(())
}
