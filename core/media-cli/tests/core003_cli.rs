use std::process::Command;

#[test]
fn explicit_accurate_cli_rejects_bad_range_before_tool_configuration() {
    for range in [["10", "5"], ["1.1", "5"], ["-1", "5"]] {
        let output = Command::new(env!("CARGO_BIN_EXE_media-cli"))
            .args([
                "trim-accurate",
                "/tmp/input.mp4",
                "/tmp/output.mp4",
                range[0],
                range[1],
            ])
            .env("MEDIASTUDIO_FFMPEG", "/not-installed/ffmpeg")
            .env("MEDIASTUDIO_FFPROBE", "/not-installed/ffprobe")
            .output()
            .unwrap();
        assert!(!output.status.success());
        let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
        assert_eq!(value["error"]["code"], -1);
        assert!(output.stderr.is_empty());
    }
}

#[test]
fn explicit_accurate_cli_rejects_out_of_bounds_deadline_without_spawning() {
    let output = Command::new(env!("CARGO_BIN_EXE_media-cli"))
        .args([
            "trim-accurate",
            "/tmp/input.mp4",
            "/tmp/output.mp4",
            "0",
            "1000000",
        ])
        .env("MEDIASTUDIO_FFMPEG", "/not-installed/ffmpeg")
        .env("MEDIASTUDIO_FFPROBE", "/not-installed/ffprobe")
        .env("MEDIASTUDIO_ACCURATE_TIMEOUT_MS", "7200001")
        .output()
        .unwrap();
    assert!(!output.status.success());
    let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(value["error"]["code"], -1);
}

#[test]
fn legacy_media_capabilities_ignore_the_new_deadline_environment() {
    let output = Command::new(env!("CARGO_BIN_EXE_media-cli"))
        .args(["capabilities", "--media"])
        .env("MEDIASTUDIO_FFMPEG", "/not-installed/ffmpeg")
        .env("MEDIASTUDIO_FFPROBE", "/not-installed/ffprobe")
        .env("MEDIASTUDIO_ACCURATE_TIMEOUT_MS", "not-an-integer")
        .output()
        .unwrap();
    assert!(output.status.success());
    let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(value["features"]["accurate_reencode"]["available"], false);
}

#[test]
fn new_capability_mode_validates_the_new_deadline_environment() {
    let output = Command::new(env!("CARGO_BIN_EXE_media-cli"))
        .args(["capabilities", "--core003"])
        .env("MEDIASTUDIO_FFMPEG", "/not-installed/ffmpeg")
        .env("MEDIASTUDIO_FFPROBE", "/not-installed/ffprobe")
        .env("MEDIASTUDIO_ACCURATE_TIMEOUT_MS", "not-an-integer")
        .output()
        .unwrap();
    assert!(!output.status.success());
    let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(value["error"]["code"], -1);
}

#[test]
fn explicit_copy_cli_ignores_accurate_deadline_and_reaches_real_engine_failure() {
    let source = std::env::temp_dir().join(format!(
        "mediastudio-core003-missing-{}",
        std::process::id()
    ));
    assert!(!source.exists());
    let output = Command::new(env!("CARGO_BIN_EXE_media-cli"))
        .args([
            "trim-copy-v1",
            source.to_str().unwrap(),
            "/tmp/output-core003-unused.mp4",
            "0",
            "1000000",
        ])
        .env("MEDIASTUDIO_FFMPEG", "/not-installed/ffmpeg")
        .env("MEDIASTUDIO_FFPROBE", "/not-installed/ffprobe")
        .env("MEDIASTUDIO_ACCURATE_TIMEOUT_MS", "not-an-integer")
        .output()
        .unwrap();
    assert!(!output.status.success());
    let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(value["mock_only"], false);
    assert_eq!(value["final"]["operation"], "trim");
    assert_eq!(value["final"]["state"], "failed");
    assert!(value["final"]["result"].is_null());
}
