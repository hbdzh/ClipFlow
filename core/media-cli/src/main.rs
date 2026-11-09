#![forbid(unsafe_code)]

use std::io::{BufRead, Read, Write};
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
        [command, flag] if command == "capabilities" && flag == "--core004" => {
            let engine = Engine::new(&core004_config()?)?;
            Ok((engine.capabilities(), true))
        }
        [command, source, destination, profile, audio] if command == "transcode" => media_job(
            transcode_request(source, destination, profile, audio)?,
            None,
        ),
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
    let transcode = request["operation"] == "transcode";
    let core003 = accurate || request["profile"] == STREAM_COPY_V1_PROFILE;
    let config = if transcode {
        core004_config()?
    } else {
        media_config(core003, accurate)?
    };
    let mut engine = Engine::new(&config)?;
    let config_value: Value = serde_json::from_slice(&config).map_err(|_| CoreError::Internal)?;
    let timeout_key = if transcode {
        "transcode_timeout_ms"
    } else if accurate {
        "accurate_reencode_timeout_ms"
    } else {
        "timeout_ms"
    };
    let timeout_ms = config_value["media_backend"][timeout_key]
        .as_u64()
        .ok_or(CoreError::Internal)?;
    let submitted = engine.submit(request.to_string().as_bytes())?;
    if submitted.get("accepted") == Some(&json!(false)) {
        return Ok((
            json!({"schema_version":0,"mock_only":false,"preflight":submitted}),
            false,
        ));
    }
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
    if let Ok(args) = &args
        && let [command, path] = args.as_slice()
        && command == "core004-session"
    {
        return session(path);
    }
    let output = args
        .map_err(|_| CoreError::InvalidUtf8)
        .and_then(|args| command(&args));
    let (value, success) = match output {
        Ok(output) => output,
        Err(error) => (
            json!({"schema_version":0,"mock_only":true,"error":{"code":error.code(),"message":"Unsupported or invalid CLI request. Use version, capabilities [--media|--core003|--core004], mock, probe PATH, trim|trim-accurate|trim-copy-v1 SOURCE DEST START_US END_US [--cancel-after-ms N], transcode SOURCE DEST PROFILE all|none|STREAM_INDEX, or core004-session CONFIG_FILE."}}),
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

fn core004_config() -> Result<Vec<u8>, CoreError> {
    let mut value: Value =
        serde_json::from_slice(&media_config(true, true)?).map_err(|_| CoreError::Internal)?;
    value["media_backend"]["core004_enabled"] = json!(true);
    let timeout = match std::env::var("MEDIASTUDIO_TRANSCODE_TIMEOUT_MS") {
        Ok(s) => s.parse::<u64>().map_err(|_| CoreError::InvalidArgument)?,
        Err(std::env::VarError::NotPresent) => 600_000,
        Err(_) => return Err(CoreError::InvalidUtf8),
    };
    value["media_backend"]["transcode_timeout_ms"] = json!(timeout);
    serde_json::to_vec(&value).map_err(|_| CoreError::Internal)
}
fn transcode_request(
    source: &str,
    destination: &str,
    profile: &str,
    audio: &str,
) -> Result<Value, CoreError> {
    let selection = match audio {
        "all" => json!({"mode":"all_supported"}),
        "none" => json!({"mode":"none"}),
        n => {
            json!({"mode":"one","stream_index":n.parse::<u32>().map_err(|_|CoreError::InvalidArgument)?})
        }
    };
    let value = json!({"schema_version":0,"operation":"transcode","source":{"uri":file_uri(&absolute_path(source)?)?},"destination":{"uri":file_uri(&absolute_path(destination)?)?},"profile":profile,"audio_selection":selection});
    media_domain::parse_request(value.to_string().as_bytes())?;
    Ok(value)
}
fn config_file(path: &str) -> Result<Vec<u8>, CoreError> {
    let file = std::fs::File::open(path).map_err(|_| CoreError::InvalidArgument)?;
    let metadata = file.metadata().map_err(|_| CoreError::InvalidArgument)?;
    if !metadata.is_file() || metadata.len() > media_domain::MAX_JSON_BYTES as u64 {
        return Err(CoreError::LimitExceeded);
    }
    let mut bytes = Vec::new();
    file.take((media_domain::MAX_JSON_BYTES + 1) as u64)
        .read_to_end(&mut bytes)
        .map_err(|_| CoreError::InvalidArgument)?;
    if bytes.len() > media_domain::MAX_JSON_BYTES {
        return Err(CoreError::LimitExceeded);
    }
    Ok(bytes)
}
fn session_action(engine: &mut Option<Engine>, value: Value) -> Result<Value, CoreError> {
    let map = value.as_object().ok_or(CoreError::InvalidArgument)?;
    let command = map
        .get("command")
        .and_then(Value::as_str)
        .ok_or(CoreError::InvalidArgument)?;
    let names = match command {
        "submit" => &["command", "request"][..],
        "snapshot" | "cancel" => &["command", "job_id"][..],
        "wait" => &["command", "job_id", "timeout_ms"][..],
        "close" => &["command"][..],
        _ => return Err(CoreError::UnsupportedOperation),
    };
    if map.keys().any(|k| !names.contains(&k.as_str())) {
        return Err(CoreError::InvalidArgument);
    }
    let live = engine.as_mut().ok_or(CoreError::InvalidHandle)?;
    let id = || {
        map.get("job_id")
            .and_then(Value::as_str)
            .ok_or(CoreError::InvalidArgument)
    };
    let response = match command {
        "submit" => {
            let request = map.get("request").ok_or(CoreError::InvalidArgument)?;
            live.submit(request.to_string().as_bytes())?
        }
        "snapshot" => live.snapshot(id()?)?,
        "cancel" => {
            live.cancel(id()?)?;
            json!({"cancel_requested":true})
        }
        "wait" => {
            let timeout = match map.get("timeout_ms") {
                None => 7_200_000,
                Some(v) => v.as_u64().ok_or(CoreError::InvalidArgument)?,
            };
            if !(1..=7_200_000).contains(&timeout) {
                return Err(CoreError::InvalidArgument);
            }
            let start = Instant::now();
            loop {
                let current = live.snapshot(id()?)?;
                if matches!(
                    current["state"].as_str(),
                    Some("succeeded" | "failed" | "cancelled")
                ) {
                    break current;
                }
                if start.elapsed() >= Duration::from_millis(timeout) {
                    return Err(CoreError::Busy);
                }
                std::thread::sleep(Duration::from_millis(5));
            }
        }
        "close" => {
            if live.has_active_jobs() {
                return Err(CoreError::Busy);
            }
            *engine = None;
            json!({"closed":true})
        }
        _ => return Err(CoreError::UnsupportedOperation),
    };
    Ok(json!({"schema_version":0,"command":command,"ok":true,"response":response}))
}
fn session(path: &str) -> ExitCode {
    let created = config_file(path).and_then(|bytes| {
        let config = media_domain::parse_config(&bytes)?;
        if !config
            .media_backend
            .as_ref()
            .is_some_and(|c| c.core004_enabled)
        {
            return Err(CoreError::UnsupportedOperation);
        }
        Engine::new(&bytes)
    });
    let mut engine = match created {
        Ok(e) => Some(e),
        Err(error) => {
            println!(
                "{}",
                json!({"schema_version":0,"ok":false,"error":{"code":error.code(),"message":"Invalid CORE-004 session configuration."}})
            );
            return ExitCode::FAILURE;
        }
    };
    let mut input = std::io::stdin().lock();
    let mut stdout = std::io::stdout().lock();
    loop {
        let mut oversized = false;
        let mut bytes = Vec::new();
        let read = std::io::Read::take(&mut input, (media_domain::MAX_JSON_BYTES + 2) as u64)
            .read_until(b'\n', &mut bytes);
        let result = match read {
            Ok(0) => break,
            Ok(_) => {
                if bytes.last() == Some(&b'\n') {
                    bytes.pop();
                }
                if bytes.last() == Some(&b'\r') {
                    bytes.pop();
                }
                if bytes.len() > media_domain::MAX_JSON_BYTES {
                    oversized = true;
                    Err(CoreError::LimitExceeded)
                } else {
                    std::str::from_utf8(&bytes)
                        .map_err(|_| CoreError::InvalidUtf8)
                        .and_then(|_| {
                            serde_json::from_slice(&bytes).map_err(|_| CoreError::InvalidJson)
                        })
                        .and_then(|v| session_action(&mut engine, v))
                }
            }
            Err(_) => Err(CoreError::InvalidArgument),
        };
        let output = match result {
            Ok(v) => v,
            Err(error) => {
                json!({"schema_version":0,"ok":false,"error":{"code":error.code(),"message":"The session command was rejected; the engine remains owned."}})
            }
        };
        if writeln!(stdout, "{output}").is_err() || stdout.flush().is_err() {
            return ExitCode::FAILURE;
        }
        if oversized {
            return ExitCode::FAILURE;
        }
        if engine.is_none() {
            return ExitCode::SUCCESS;
        }
    }
    // EOF is not a successful explicit close. Drop still cancels and joins owned
    // work, preserving the same RAII safety boundary as the noninteractive CLI.
    ExitCode::FAILURE
}
