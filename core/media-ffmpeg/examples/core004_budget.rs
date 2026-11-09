//! Owner-only deadline harness: real preflight, controlled idle wait, real execute.
use media_domain::{
    AudioSelection, BackendConfig, MediaRange, MediaRequest, TranscodeProfile, TrimStrategy,
};
use serde_json::json;
use std::{
    path::PathBuf,
    sync::atomic::AtomicBool,
    time::{Duration, Instant},
};
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = std::env::args().collect::<Vec<_>>();
    if !matches!(args.len(), 9 | 11) {
        return Err("SOURCE DESTINATION FFMPEG probe|copy|accurate|transcode GENERIC_MS ACCURATE_MS TRANSCODE_MS IDLE_MS [START_US END_US]".into());
    }
    let config = BackendConfig {
        ffmpeg: PathBuf::from(&args[3]),
        ffprobe: PathBuf::from("/opt/homebrew/bin/ffprobe"),
        timeout_ms: args[5].parse()?,
        accurate_reencode_timeout_ms: args[6].parse()?,
        transcode_timeout_ms: args[7].parse()?,
        core003_enabled: true,
        core004_enabled: true,
        max_output_bytes: 16 * 1024 * 1024,
        ai_backend: None,
    };
    let source = PathBuf::from(&args[1]);
    let destination = PathBuf::from(&args[2]);
    let range = if args.len() == 11 {
        MediaRange {
            start_us: args[9].parse()?,
            end_us: args[10].parse()?,
        }
    } else {
        MediaRange {
            start_us: 0,
            end_us: 500000,
        }
    };
    let request = match args[4].as_str() {
        "probe" => MediaRequest::Probe { source },
        "copy" => MediaRequest::Trim {
            source,
            destination,
            start_us: range.start_us,
            end_us: range.end_us,
            strategy: TrimStrategy::StreamCopyV1,
        },
        "accurate" => MediaRequest::Trim {
            source,
            destination,
            start_us: range.start_us,
            end_us: range.end_us,
            strategy: TrimStrategy::AccurateReencode,
        },
        "transcode" => MediaRequest::Transcode {
            source,
            destination,
            range: Some(range),
            profile: TranscodeProfile::Standard,
            audio_selection: AudioSelection::None,
        },
        _ => return Err("unknown operation".into()),
    };
    let cancel = AtomicBool::new(false);
    let start = Instant::now();
    let prepared = media_ffmpeg::preflight(&config, &request, &cancel);
    let preflight_ms = start.elapsed().as_secs_f64() * 1000.0;
    let mut value = json!({"preflight_ms":preflight_ms});
    match prepared {
        Err(error) => {
            value["error_phase"] = json!("preflight");
            value["error_code"] = json!(error.code);
        }
        Ok(prepared) => {
            let idle = Instant::now();
            std::thread::sleep(Duration::from_millis(args[8].parse()?));
            value["idle_ms"] = json!(idle.elapsed().as_secs_f64() * 1000.0);
            let executing = Instant::now();
            let result = media_ffmpeg::execute_prepared(&config, &prepared, &cancel);
            value["execute_ms"] = json!(executing.elapsed().as_secs_f64() * 1000.0);
            match result {
                Ok(result) => value["result"] = result,
                Err(error) => {
                    value["error_phase"] = json!("execute");
                    value["error_code"] = json!(error.code);
                }
            }
        }
    }
    value["wall_ms"] = json!(start.elapsed().as_secs_f64() * 1000.0);
    println!("{value}");
    Ok(())
}
