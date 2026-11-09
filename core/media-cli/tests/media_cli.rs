use std::process::Command;

#[test]
fn media_cli_rejects_range_and_flags_without_spawning_untrusted_commands() {
    for args in [
        vec!["trim", "/tmp/input.mp4", "/tmp/output.mp4", "10", "5"],
        vec!["trim", "/tmp/input.mp4", "/tmp/output.mp4", "1.1", "5"],
        vec![
            "trim",
            "/tmp/input.mp4",
            "/tmp/output.mp4",
            "0",
            "5",
            "--extra",
            "anything",
        ],
    ] {
        let output = Command::new(env!("CARGO_BIN_EXE_media-cli"))
            .args(args)
            .output()
            .unwrap();
        assert!(!output.status.success());
        let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
        assert!(value["error"]["code"].as_i64().unwrap() < 0);
        assert!(output.stderr.is_empty());
    }
}

#[test]
fn invalid_tool_configuration_is_not_silently_replaced_by_path_discovery() {
    let output = Command::new(env!("CARGO_BIN_EXE_media-cli"))
        .args(["probe", "/tmp/input.mp4"])
        .env("MEDIASTUDIO_FFMPEG", "relative/tool")
        .env("MEDIASTUDIO_FFPROBE", "/tmp/missing-ffprobe")
        .output()
        .unwrap();
    assert!(!output.status.success());
    let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(value["error"]["code"], -1);
}
