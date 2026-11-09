//! Explicit host-only experiment, not a production Core job or cleanup proof.
#![cfg(target_os = "macos")]
use media_ai::runner::{RunError, run_group};
use std::{
    ffi::OsString,
    fs,
    os::unix::fs::DirBuilderExt,
    path::{Path, PathBuf},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    thread,
    time::{Duration, Instant},
};

fn required(name: &str) -> PathBuf {
    PathBuf::from(std::env::var_os(name).expect(name))
}
fn private_dir(path: &Path) {
    let mut b = fs::DirBuilder::new();
    b.mode(0o700);
    b.create(path).expect("private dir");
}
fn args(worker: &Path, model: &Path, input: &Path, stage: &Path) -> Vec<OsString> {
    vec![
        worker.as_os_str().into(),
        "--model".into(),
        model.as_os_str().into(),
        "--input".into(),
        input.as_os_str().into(),
        "--staging".into(),
        stage.as_os_str().into(),
    ]
}
#[test]
#[ignore = "requires local fixed model, Python environment, approved fixture and macOS ps"]
fn actual_stream_worker_success_and_started_process_group_cancellation() {
    let python = required("AI001_PYTHON");
    let worker = required("AI001_STREAM_WORKER");
    let model = required("AI001_MODEL");
    let input = required("AI001_INPUT");
    let long_input = required("AI001_LONG_INPUT");
    for p in [&python, &worker, &model, &input, &long_input] {
        assert!(p.is_absolute() && p.is_file());
    }
    let root = std::env::temp_dir().join(format!("ai001-real-group-{}", std::process::id()));
    private_dir(&root);
    let good = root.join("good");
    let cancel_stage = root.join("cancel");
    private_dir(&good);
    private_dir(&cancel_stage);
    let flag = AtomicBool::new(false);
    let out = run_group(
        &python,
        &args(&worker, &model, &input, &good),
        Duration::from_secs(30),
        &flag,
        16384,
    )
    .expect("actual CPU worker");
    let response = String::from_utf8(out.stdout).unwrap();
    assert!(response.contains("\"sample_rate\":44100") && response.contains("\"channels\":2"));
    assert!(good.join("vocals.wav").is_file() && good.join("accompaniment.wav").is_file());
    let cancel = Arc::new(AtomicBool::new(false));
    let monitor = cancel.clone();
    let stage_name = cancel_stage.to_string_lossy().into_owned();
    let worker_name = worker.to_string_lossy().into_owned();
    let watch = thread::spawn(move || {
        let until = Instant::now() + Duration::from_secs(5);
        let mut found = None;
        while Instant::now() < until {
            if let Ok(output) = std::process::Command::new("/bin/ps")
                .args(["-ax", "-o", "pid=", "-o", "command="])
                .output()
            {
                for line in String::from_utf8_lossy(&output.stdout).lines() {
                    if line.contains(&stage_name)
                        && line.contains(&worker_name)
                        && let Some(pid) = line
                            .split_whitespace()
                            .next()
                            .and_then(|x| x.parse::<u32>().ok())
                    {
                        found = Some(pid);
                        break;
                    }
                }
            }
            if found.is_some() {
                break;
            }
            thread::sleep(Duration::from_millis(5));
        }
        monitor.store(true, Ordering::Release);
        found
    });
    let result = run_group(
        &python,
        &args(&worker, &model, &long_input, &cancel_stage),
        Duration::from_secs(30),
        &cancel,
        16384,
    );
    let pid = watch
        .join()
        .unwrap()
        .expect("actual worker command observed");
    assert!(matches!(result, Err(RunError::Cancelled)), "{result:?}");
    let check = std::process::Command::new("/bin/kill")
        .args(["-0", &pid.to_string()])
        .output()
        .unwrap();
    assert!(!check.status.success());
    // This test owns this private directory; product staging cleanup remains
    // unimplemented and is not inferred from this remove_dir_all.
    fs::remove_dir_all(&root).unwrap();
    assert!(!root.exists());
}
