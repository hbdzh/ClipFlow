#![forbid(unsafe_code)]

use std::io::Write;
use std::path::PathBuf;
use std::process::ExitCode;
use std::time::{Duration, Instant};

use media_domain::{
    ACCURATE_REENCODE_PROFILE, CoreError, STREAM_COPY_V1_PROFILE, TrimStrategy, file_uri,
};
use media_orchestrator::Engine;
use serde_json::{Value, json};

fn command(args: &[String]) -> Result<(Value, bool), CoreError> {
    let mut engine = Engine::new(br#"{"schema_version":0}"#)?;
    match args {
        [command] if command == "version" => Ok((engine.version(), true)),
        [command] if command == "capabilities" => Ok((engine.capabilities(), true)),
        [command, flag] if command == "capabilities" && flag == "--media" => {
            let engine = Engine::new(&media_config(false, false)?)?;
            Ok((engine.capabilities(), true))
        }
        [command, flag] if command == "capabilities" && flag == "--core003" => {
            let engine = Engine::new(&media_config(true, true)?)?;
            Ok((engine.capabilities(), true))
        }
        [command, source] if command == "probe" => media_job(probe_request(source)?, None),
        [command, source, flag, delay] if command == "probe" && flag == "--cancel-after-ms" => {
            media_job(probe_request(source)?, Some(parse_delay(delay)?))
        }
        [command, source, destination, start, end]
            if matches!(command.as_str(), "trim" | "trim-accurate" | "trim-copy-v1") =>
        {
            media_job(
                trim_request(source, destination, start, end, trim_strategy(command)?)?,
                None,
            )
        }
        [command, source, destination, start, end, flag, delay]
            if matches!(command.as_str(), "trim" | "trim-accurate" | "trim-copy-v1")
                && flag == "--cancel-after-ms" =>
        {
            media_job(
                trim_request(source, destination, start, end, trim_strategy(command)?)?,
                Some(parse_delay(delay)?),
            )
        }
        [command] if command == "mock" => mock(&mut engine, "succeed"),
        [command, outcome] if command == "mock" => mock(&mut engine, outcome),
        _ => Err(CoreError::UnsupportedOperation),
    }
}

fn absolute_path(text: &str) -> Result<PathBuf, CoreError> {
    let path = PathBuf::from(text);
    if text.contains('\0') {
        return Err(CoreError::InvalidArgument);
    }
    if path.is_absolute() {
        Ok(path)
    } else {
        Ok(std::env::current_dir()
            .map_err(|_| CoreError::InvalidArgument)?
            .join(path))
    }
}

fn executable(variable: &str, name: &str) -> Result<PathBuf, CoreError> {
    if let Some(value) = std::env::var_os(variable) {
        let path = PathBuf::from(value);
        if !path.is_absolute() || path.as_os_str().is_empty() {
            return Err(CoreError::InvalidArgument);
        }
        return Ok(path);
    }
    let paths = std::env::var_os("PATH").ok_or(CoreError::UnsupportedOperation)?;
    for directory in std::env::split_paths(&paths) {
        let candidate = directory.join(name);
        if candidate.is_file() {
            return candidate
                .canonicalize()
                .map_err(|_| CoreError::InvalidArgument);
        }
    }
    Err(CoreError::UnsupportedOperation)
}

fn media_config(core003_enabled: bool, accurate_deadline: bool) -> Result<Vec<u8>, CoreError> {
    let ffmpeg = executable("MEDIASTUDIO_FFMPEG", "ffmpeg")?;
    let ffprobe = executable("MEDIASTUDIO_FFPROBE", "ffprobe")?;
    let setting = |name: &str, default: u64| -> Result<u64, CoreError> {
        match std::env::var(name) {
            Ok(value) => value.parse().map_err(|_| CoreError::InvalidArgument),
            Err(std::env::VarError::NotPresent) => Ok(default),
            Err(_) => Err(CoreError::InvalidUtf8),
        }
    };
    let mut config = json!({"schema_version":0,"media_backend":{
        "ffmpeg":ffmpeg.to_str().ok_or(CoreError::InvalidUtf8)?,
        "ffprobe":ffprobe.to_str().ok_or(CoreError::InvalidUtf8)?,
        "timeout_ms":setting("MEDIASTUDIO_TIMEOUT_MS",30_000)?,
        "max_output_bytes":setting("MEDIASTUDIO_MAX_OUTPUT_BYTES",8*1024*1024)?
    }});
    if core003_enabled {
        config["media_backend"]["core003_enabled"] = json!(true);
    }
    if accurate_deadline {
        config["media_backend"]["accurate_reencode_timeout_ms"] =
            json!(setting("MEDIASTUDIO_ACCURATE_TIMEOUT_MS", 600_000)?);
    }
    serde_json::to_vec(&config).map_err(|_| CoreError::Internal)
}

fn probe_request(source: &str) -> Result<Value, CoreError> {
    Ok(
        json!({"schema_version":0,"operation":"probe","source":{"uri":file_uri(&absolute_path(source)?)?}}),
    )
}

fn trim_strategy(command: &str) -> Result<TrimStrategy, CoreError> {
    match command {
        "trim" => Ok(TrimStrategy::StreamCopy),
        "trim-copy-v1" => Ok(TrimStrategy::StreamCopyV1),
        "trim-accurate" => Ok(TrimStrategy::AccurateReencode),
        _ => Err(CoreError::UnsupportedOperation),
    }
}

fn trim_request(
    source: &str,
    destination: &str,
    start: &str,
    end: &str,
    strategy: TrimStrategy,
) -> Result<Value, CoreError> {
    let start_us: i64 = start.parse().map_err(|_| CoreError::InvalidArgument)?;
    let end_us: i64 = end.parse().map_err(|_| CoreError::InvalidArgument)?;
    if start_us < 0 || end_us <= start_us {
        return Err(CoreError::InvalidArgument);
    }
    let mut request = json!({"schema_version":0,"operation":"trim", "source":{"uri":file_uri(&absolute_path(source)?)?},
        "destination":{"uri":file_uri(&absolute_path(destination)?)?},"range":{"start_us":start_us,"end_us":end_us},
        "strategy":"stream_copy","stream_selection":"all_supported","allow_reencode_fallback":false});
    match strategy {
        TrimStrategy::StreamCopy => {}
        TrimStrategy::StreamCopyV1 => request["profile"] = json!(STREAM_COPY_V1_PROFILE),
        TrimStrategy::AccurateReencode => {
            request["strategy"] = json!("accurate_reencode");
            request["profile"] = json!(ACCURATE_REENCODE_PROFILE);
        }
    }
    Ok(request)
}

fn parse_delay(text: &str) -> Result<u64, CoreError> {
    let value: u64 = text.parse().map_err(|_| CoreError::InvalidArgument)?;
    if value > 300_000 {
        return Err(CoreError::InvalidArgument);
    }
    Ok(value)
}

fn media_job(request: Value, cancel_after_ms: Option<u64>) -> Result<(Value, bool), CoreError> {
    let accurate = request["strategy"] == "accurate_reencode";
    let core003 = accurate || request["profile"] == STREAM_COPY_V1_PROFILE;
    let config = media_config(core003, accurate)?;
    let mut engine = Engine::new(&config)?;
    let config_value: Value = serde_json::from_slice(&config).map_err(|_| CoreError::Internal)?;
    let timeout_key = if accurate {
        "accurate_reencode_timeout_ms"
    } else {
        "timeout_ms"
    };
    let timeout_ms = config_value["media_backend"][timeout_key]
        .as_u64()
        .ok_or(CoreError::Internal)?;
    let submitted = engine.submit(request.to_string().as_bytes())?;
    let id = submitted["job_id"]
        .as_str()
        .ok_or(CoreError::Internal)?
        .to_owned();
    let mut snapshots = vec![submitted];
    let start = Instant::now();
    let mut requested_cancel = false;
    loop {
        if !requested_cancel
            && cancel_after_ms.is_some_and(|delay| start.elapsed() >= Duration::from_millis(delay))
        {
            engine.cancel(&id)?;
            requested_cancel = true;
        }
        let current = engine.snapshot(&id)?;
        if snapshots.last() != Some(&current) {
            snapshots.push(current.clone());
        }
        let state = current["state"].as_str().ok_or(CoreError::Internal)?;
        if ["succeeded", "failed", "cancelled"].contains(&state) {
            let error = (state == "failed").then(|| json!({"code":CoreError::Internal.code(),"message":"The media job failed; inspect its terminal error code."}));
            return Ok((
                json!({"schema_version":0,"mock_only":false,"cancel_requested":requested_cancel,"snapshots":snapshots,"final":current,"error":error}),
                state == "succeeded",
            ));
        }
        // Backend enforces its processing deadline. This is only a defensive
        // client deadline; Engine Drop cancels and joins its sole worker.
        if start.elapsed() > Duration::from_millis(timeout_ms.saturating_add(5_000)) {
            engine.cancel(&id)?;
            return Err(CoreError::Internal);
        }
        std::thread::sleep(Duration::from_millis(5));
    }
}

fn mock(engine: &mut Engine, outcome: &str) -> Result<(Value, bool), CoreError> {
    if !["succeed", "fail", "cancel"].contains(&outcome) {
        return Err(CoreError::InvalidArgument);
    }
    let request = json!({
        "schema_version": 0,
        "operation": "mock",
        "outcome": if outcome == "fail" { "fail" } else { "succeed" }
    });
    let submitted = engine.submit(request.to_string().as_bytes())?;
    let id = submitted["job_id"]
        .as_str()
        .ok_or(CoreError::Internal)?
        .to_owned();
    let mut snapshots = vec![submitted];
    if outcome == "cancel" {
        engine.cancel(&id)?;
    }
    // The bounded mock has at most two polls. This bound is defensive and does
    // not represent a timeout or scheduler for real media jobs.
    for _ in 0..8 {
        let current = engine.snapshot(&id)?;
        let terminal = match current["state"].as_str() {
            Some("succeeded" | "cancelled") => Some(true),
            Some("failed") => Some(false),
            _ => None,
        };
        snapshots.push(current.clone());
        if let Some(success) = terminal {
            return Ok((
                json!({"schema_version":0,"mock_only":true,"snapshots":snapshots,"final":current}),
                success,
            ));
        }
    }
    Err(CoreError::Internal)
}

fn main() -> ExitCode {
    let args: Result<Vec<String>, _> = std::env::args_os()
        .skip(1)
        .map(|arg| arg.into_string())
        .collect();
    let output = args
        .map_err(|_| CoreError::InvalidUtf8)
        .and_then(|args| command(&args));
    let (value, success) = match output {
        Ok(output) => output,
        Err(error) => (
            json!({"schema_version":0,"mock_only":true,"error":{"code":error.code(),"message":"Unsupported or invalid CLI request. Use version, capabilities [--media|--core003], mock, probe PATH, or trim|trim-accurate|trim-copy-v1 SOURCE DEST START_US END_US [--cancel-after-ms N]."}}),
            false,
        ),
    };
    let mut stdout = std::io::stdout().lock();
    if writeln!(stdout, "{value}").is_err() {
        return ExitCode::FAILURE;
    }
    if success {
        ExitCode::SUCCESS
    } else {
        ExitCode::FAILURE
    }
}
