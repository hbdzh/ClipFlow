use std::process::Command;

fn run(args: &[&str]) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_media-cli"))
        .args(args)
        .output()
        .unwrap()
}

#[test]
fn cli_version_and_capabilities_are_real_calls_with_mock_media_scope() {
    let version = run(&["version"]);
    assert!(version.status.success());
    let value: serde_json::Value = serde_json::from_slice(&version.stdout).unwrap();
    assert_eq!(value["core_version"], "0.1.0-dev");
    assert_eq!(value["stage"], "CORE-001");
    let capabilities = run(&["capabilities"]);
    assert!(capabilities.status.success());
    let value: serde_json::Value = serde_json::from_slice(&capabilities.stdout).unwrap();
    assert_eq!(value["features"]["probe"]["available"], false);
}

#[test]
fn cli_reports_success_failure_and_cancellation_without_media_files() {
    for (mode, state, success) in [
        ("succeed", "succeeded", true),
        ("fail", "failed", false),
        ("cancel", "cancelled", true),
    ] {
        let output = run(&["mock", mode]);
        assert_eq!(output.status.success(), success);
        let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
        assert_eq!(value["mock_only"], true);
        assert_eq!(value["final"]["state"], state);
        assert_eq!(value["snapshots"].as_array().unwrap().len(), 3);
    }
}

#[test]
fn cli_never_fakes_probe_or_trim_success() {
    for args in [
        vec!["probe", "input.mp4"],
        vec!["trim"],
        vec!["mock", "unknown"],
        vec![],
    ] {
        let output = run(&args);
        assert!(!output.status.success());
        let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
        assert!(value["error"]["code"].as_i64().unwrap() < 0);
    }
}

#[test]
#[cfg(unix)]
fn cli_rejects_non_utf8_arguments_without_panicking() {
    use std::ffi::OsString;
    use std::os::unix::ffi::OsStringExt;

    let output = Command::new(env!("CARGO_BIN_EXE_media-cli"))
        .arg(OsString::from_vec(vec![0xff]))
        .output()
        .unwrap();
    assert!(!output.status.success());
    let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(value["error"]["code"], -3);
    assert!(output.stderr.is_empty());
}
