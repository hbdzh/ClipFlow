use std::{
    io::Write,
    path::PathBuf,
    process::{Command, Stdio},
    sync::atomic::{AtomicU64, Ordering},
};
static NEXT: AtomicU64 = AtomicU64::new(1);
struct Config(PathBuf);
impl Config {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "mediastudio-core004-cli-{}-{}.json",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        assert!(!path.exists());
        std::fs::write(&path,br#"{"schema_version":0,"media_backend":{"ffmpeg":"/missing/ffmpeg","ffprobe":"/missing/ffprobe","core003_enabled":true,"core004_enabled":true}}"#).unwrap();
        Self(path)
    }
}
impl Drop for Config {
    fn drop(&mut self) {
        std::fs::remove_file(&self.0).unwrap();
    }
}
fn run(input: &[u8]) -> std::process::Output {
    let config = Config::new();
    let mut child = Command::new(env!("CARGO_BIN_EXE_media-cli"))
        .arg("core004-session")
        .arg(&config.0)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child.stdin.take().unwrap().write_all(input).unwrap();
    child.wait_with_output().unwrap()
}
#[test]
fn session_is_thin_and_explicit_close_is_not_a_media_job() {
    let o=run(b"{\"command\":\"submit\",\"request\":{\"schema_version\":0,\"operation\":\"queue_snapshot\"}}\n{\"command\":\"close\"}\n");
    assert!(o.status.success());
    let rows = String::from_utf8(o.stdout)
        .unwrap()
        .lines()
        .map(|s| serde_json::from_str::<serde_json::Value>(s).unwrap())
        .collect::<Vec<_>>();
    assert_eq!(rows.len(), 2);
    assert_eq!(rows[0]["response"]["active_count"], 0);
    assert!(rows[0]["response"].get("job_id").is_none());
    assert_eq!(rows[1]["response"]["closed"], true);
    assert!(o.stderr.is_empty());
}
#[test]
fn oversized_wrapper_never_reinterprets_its_tail_as_commands() {
    let mut input = vec![b'x'; 65538];
    input.extend_from_slice(b"\n{\"command\":\"close\"}\n");
    let o = run(&input);
    assert!(!o.status.success());
    let text = String::from_utf8(o.stdout).unwrap();
    let lines = text.lines().collect::<Vec<_>>();
    assert_eq!(lines.len(), 1);
    let value: serde_json::Value = serde_json::from_str(lines[0]).unwrap();
    assert_eq!(value["error"]["code"], -9);
    assert!(o.stderr.is_empty());
}
#[test]
fn utf8_failure_is_distinct_and_eof_is_not_successful_close() {
    let o = run(&[0xff, b'\n']);
    assert!(!o.status.success());
    let value: serde_json::Value = serde_json::from_slice(&o.stdout).unwrap();
    assert_eq!(value["error"]["code"], -3);
}
#[test]
fn transcode_does_not_accept_arbitrary_profiles_or_negative_audio_indices() {
    for (profile, audio) in [("libx264-custom", "all"), ("mp4_h264_standard_v1", "-1")] {
        let o = Command::new(env!("CARGO_BIN_EXE_media-cli"))
            .args([
                "transcode",
                "/tmp/source.mp4",
                "/tmp/new.mp4",
                profile,
                audio,
            ])
            .output()
            .unwrap();
        assert!(!o.status.success());
        let v: serde_json::Value = serde_json::from_slice(&o.stdout).unwrap();
        assert_eq!(v["error"]["code"], -1);
        assert!(o.stderr.is_empty());
    }
}
