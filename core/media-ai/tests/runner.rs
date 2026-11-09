#![cfg(target_os = "macos")]
use media_ai::runner::{RunError, run_group};
use std::{
    ffi::OsString,
    path::Path,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    thread,
    time::{Duration, Instant},
};

#[test]
fn successful_child_captures_output_with_clean_environment() {
    let cancel = AtomicBool::new(false);
    let args = [OsString::from("hello")];
    let out = run_group(
        Path::new("/usr/bin/printf"),
        &args,
        Duration::from_secs(3),
        &cancel,
        4096,
    )
    .expect("child");
    assert_eq!(out.stdout, b"hello");
    assert!(out.stderr.is_empty());
}

#[test]
fn rejects_non_absolute_program_and_bounds_output() {
    use std::os::unix::ffi::OsStringExt;
    let cancel = AtomicBool::new(false);
    assert!(matches!(
        run_group(
            Path::new("relative"),
            &[],
            Duration::from_secs(1),
            &cancel,
            1024
        ),
        Err(RunError::InvalidArgument)
    ));
    assert!(matches!(
        run_group(
            Path::new("/usr/bin/yes"),
            &[],
            Duration::from_secs(3),
            &cancel,
            1024
        ),
        Err(RunError::OutputLimit)
    ));
    let nul_arg = OsString::from_vec(b"bad\0argument".to_vec());
    assert!(matches!(
        run_group(
            Path::new("/usr/bin/printf"),
            &[nul_arg],
            Duration::from_secs(1),
            &cancel,
            1024
        ),
        Err(RunError::InvalidArgument)
    ));
    let huge = OsString::from("a".repeat(65_536));
    assert!(matches!(
        run_group(
            Path::new("/usr/bin/printf"),
            &[huge],
            Duration::from_secs(1),
            &cancel,
            1024
        ),
        Err(RunError::InvalidArgument)
    ));
}

#[test]
fn timeout_kills_reaps_and_confirms_group_empty() {
    let cancel = AtomicBool::new(false);
    let start = Instant::now();
    assert!(matches!(
        run_group(
            Path::new("/bin/sleep"),
            &["10".into()],
            Duration::from_millis(100),
            &cancel,
            1024
        ),
        Err(RunError::TimedOut)
    ));
    assert!(start.elapsed() < Duration::from_secs(6));
}

#[test]
fn cancellation_stops_running_direct_child() {
    let c = Arc::new(AtomicBool::new(false));
    let flag = c.clone();
    let t = thread::spawn(move || {
        thread::sleep(Duration::from_millis(100));
        flag.store(true, Ordering::Release);
    });
    let out = run_group(
        Path::new("/bin/sleep"),
        &["10".into()],
        Duration::from_secs(5),
        &c,
        1024,
    );
    t.join().unwrap();
    assert!(matches!(out, Err(RunError::Cancelled)));
}

#[test]
fn nonzero_exit_is_failure_without_diagnostic_leak() {
    let c = AtomicBool::new(false);
    let args = ["-c".into(), "printf secret-to-stderr >&2; exit 13".into()];
    let error = run_group(
        Path::new("/bin/sh"),
        &args,
        Duration::from_secs(3),
        &c,
        1024,
    )
    .unwrap_err();
    assert!(matches!(error, RunError::ProcessFailed));
    assert!(!format!("{error:?}").contains("secret-to-stderr"));
}

#[test]
fn cancellation_signals_spawned_process_group() {
    use std::os::unix::fs::PermissionsExt;
    let nonce = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let dir = std::env::temp_dir().join(format!("ai-runner-child-{}-{nonce}", std::process::id()));
    std::fs::create_dir(&dir).expect("exclusive private fixture");
    std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o700)).expect("permissions");
    let path = dir.join("pid");
    let child_path = path.clone();
    let cancel = Arc::new(AtomicBool::new(false));
    let flag = cancel.clone();
    let t = thread::spawn(move || {
        let deadline = Instant::now() + Duration::from_secs(3);
        while !child_path.exists() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(5));
        }
        flag.store(true, Ordering::Release);
    });
    // Fixed test-only shell program: pathname is a positional argv, never
    // interpolated into shell source. Both child and descendant remain in group.
    let args: Vec<OsString> = vec![
        "-c".into(),
        "sleep 10 & echo $! > \"$1\"; wait".into(),
        "test".into(),
        path.as_os_str().to_owned(),
    ];
    let result = run_group(
        Path::new("/bin/sh"),
        &args,
        Duration::from_secs(5),
        &cancel,
        1024,
    );
    t.join().unwrap();
    let pid: u32 = std::fs::read_to_string(&path)
        .expect("descendant was spawned")
        .trim()
        .parse()
        .expect("numeric pid");
    std::fs::remove_file(&path).expect("remove marker");
    std::fs::remove_dir(&dir).expect("remove owned fixture");
    assert!(
        matches!(result, Err(RunError::Cancelled)),
        "group must be confirmed gone: {result:?}"
    );
    let status = std::process::Command::new("/bin/kill")
        .args(["-0", &pid.to_string()])
        .output()
        .expect("kill status");
    assert!(!status.status.success(), "descendant PID remains live");
}

#[test]
fn parent_environment_sentinel_is_not_inherited() {
    const CHILD: &str = "MEDIASTUDIO_AI001_ENV_TEST_CHILD";
    const SENTINEL: &str = "MEDIASTUDIO_AI001_ENV_TEST_SENTINEL";
    if std::env::var_os(CHILD).is_none() {
        // Only this isolated test subprocess gets a synthetic marker: no
        // process-global environment mutation races other tests.
        let result = std::process::Command::new(std::env::current_exe().expect("test binary"))
            .args([
                "--exact",
                "parent_environment_sentinel_is_not_inherited",
                "--nocapture",
            ])
            .env(CHILD, "1")
            .env(SENTINEL, "synthetic-not-a-secret")
            .output()
            .expect("isolated environment test");
        assert!(result.status.success(), "isolated environment test failed");
        return;
    }
    assert_eq!(
        std::env::var(SENTINEL).ok().as_deref(),
        Some("synthetic-not-a-secret")
    );
    let cancel = AtomicBool::new(false);
    let out = run_group(
        Path::new("/usr/bin/env"),
        &[],
        Duration::from_secs(3),
        &cancel,
        4096,
    )
    .expect("child environment");
    let text = String::from_utf8(out.stdout).expect("UTF-8 environment");
    assert!(!text.contains(SENTINEL));
    assert!(!text.contains(CHILD));
    assert!(text.lines().any(|s| s == "LC_ALL=C"));
    assert!(text.lines().any(|s| s == "PYTHONNOUSERSITE=1"));
}
