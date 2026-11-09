#![forbid(unsafe_code)]

use std::io::Write;
use std::process::ExitCode;

use media_domain::CoreError;
use media_orchestrator::Engine;
use serde_json::{Value, json};

fn command(args: &[String]) -> Result<(Value, bool), CoreError> {
    let mut engine = Engine::new(br#"{"schema_version":0}"#)?;
    match args {
        [command] if command == "version" => Ok((engine.version(), true)),
        [command] if command == "capabilities" => Ok((engine.capabilities(), true)),
        [command] if command == "mock" => mock(&mut engine, "succeed"),
        [command, outcome] if command == "mock" => mock(&mut engine, outcome),
        _ => Err(CoreError::UnsupportedOperation),
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
            json!({"schema_version":0,"mock_only":true,"error":{"code":error.code(),"message":"Unsupported or invalid CLI request. Use version, capabilities, or mock [succeed|fail|cancel]."}}),
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
