#![cfg(target_os = "macos")]
use media_orchestrator::Engine;
use serde_json::{Value, json};
use std::{
    path::Path,
    process::Command,
    thread,
    time::{Duration, Instant},
};
fn call(engine: &mut Engine, v: Value) -> Value {
    engine.submit(v.to_string().as_bytes()).expect("response")
}
fn wait(engine: &mut Engine, id: &str, target: &[&str]) -> Value {
    let deadline = Instant::now() + Duration::from_secs(90);
    loop {
        let s = engine.snapshot(id).expect("snapshot");
        let state = s["state"].as_str().unwrap();
        if target.contains(&state) {
            return s;
        }
        assert!(Instant::now() < deadline, "timeout: {s}");
        thread::sleep(Duration::from_millis(10));
    }
}
#[test]
#[ignore = "requires macOS and the local FFmpeg/x264 toolchain"]
fn transform_batch_conflict_active_cancel_and_retry_keep_frozen_request() {
    let root = std::env::temp_dir().join(format!(
        "ms-transform-queue-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    std::fs::create_dir(&root).unwrap();
    let source = root.join("source.mp4");
    let first = root.join("first.mp4");
    let retry = root.join("retry.mp4");
    assert!(
        Command::new("/opt/homebrew/bin/ffmpeg")
            .args([
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=1280x720:rate=30:duration=12",
                "-c:v",
                "libx264",
                "-threads",
                "1",
                "-pix_fmt",
                "yuv420p"
            ])
            .arg(&source)
            .status()
            .unwrap()
            .success()
    );
    let original = std::fs::read(&source).unwrap();
    let cfg = json!({"schema_version":0,"media_backend":{"ffmpeg":"/opt/homebrew/bin/ffmpeg","ffprobe":"/opt/homebrew/bin/ffprobe","core003_enabled":true,"core004_enabled":true,"video_transform_enabled":true}});
    let mut engine = Engine::new(cfg.to_string().as_bytes()).unwrap();
    let uri = |p: &Path| media_domain::file_uri(p).unwrap();
    let item = json!({"schema_version":0,"operation":"transcode","source":{"uri":uri(&source)},"destination":{"uri":uri(&first)},"profile":"mp4_h264_standard_v1","video_transform":{"rotation_degrees":90,"scale_percent":125,"offset_x":20,"offset_y":-10}});
    let request = json!({"schema_version":0,"operation":"batch_submit","submission_id":"transform-batch","items":[item]});
    let accepted = call(&mut engine, request.clone());
    assert_eq!(accepted["accepted"], true, "{accepted}");
    let id = accepted["job_ids"][0].as_str().unwrap();
    let mut changed = request;
    changed["items"][0]["video_transform"]["rotation_degrees"] = json!(180);
    let conflict = call(&mut engine, changed);
    assert_eq!(
        conflict["errors"][0]["code"], "SUBMISSION_CONFLICT",
        "{conflict}"
    );
    let running = wait(&mut engine, id, &["running", "succeeded", "failed"]);
    assert_eq!(
        running["state"], "running",
        "task completed before cancellation test"
    );
    // A running queue entry may still be probing or fully decoding the source.
    // Wait for the private MP4 to grow beyond its initial header and verify the
    // matching FFmpeg encoding command is still alive before cancelling.
    let deadline = Instant::now() + Duration::from_secs(90);
    loop {
        let partial = std::fs::read_dir(&root)
            .unwrap()
            .filter_map(Result::ok)
            .map(|entry| entry.path().join("output.mp4"))
            .find(|path| path.metadata().is_ok_and(|m| m.len() > 48));
        if let Some(path) = partial {
            let processes = Command::new("/bin/ps")
                .args(["-axo", "command="])
                .output()
                .unwrap();
            let listing = String::from_utf8_lossy(&processes.stdout);
            if listing.lines().any(|line| {
                line.contains("ffmpeg")
                    && line.contains("-filter_complex")
                    && line.contains(path.to_str().unwrap())
            }) {
                eprintln!(
                    "observed active FFmpeg encoding with {} bytes staged",
                    path.metadata().unwrap().len()
                );
                break;
            }
        }
        assert!(
            Instant::now() < deadline,
            "never observed FFmpeg encoding with staged media"
        );
        let state = engine.snapshot(id).unwrap();
        assert_eq!(
            state["state"], "running",
            "encoding finished before observation: {state}"
        );
        thread::sleep(Duration::from_millis(10));
    }
    engine.cancel(id).unwrap();
    let cancelled = wait(&mut engine, id, &["cancelled", "failed", "succeeded"]);
    assert_eq!(cancelled["state"], "cancelled", "{cancelled}");
    assert!(!first.exists());
    let remaining: Vec<_> = std::fs::read_dir(&root)
        .unwrap()
        .map(|e| e.unwrap().path())
        .collect();
    assert_eq!(
        remaining,
        vec![source.clone()],
        "cancel left private output behind"
    );
    let response = call(
        &mut engine,
        json!({"schema_version":0,"operation":"job_retry","job_id":id,"new_destination":{"uri":uri(&retry)},"submission_id":"transform-retry"}),
    );
    assert_eq!(response["accepted"], true, "{response}");
    let id2 = response["job_ids"][0].as_str().unwrap();
    let done = wait(&mut engine, id2, &["succeeded", "failed", "cancelled"]);
    assert_eq!(done["state"], "succeeded", "{done}");
    assert_eq!(
        done["result"]["transcode_plan"]["video_transform"],
        json!({"rotation_degrees":90,"scale_percent":125,"offset_x":20,"offset_y":-10})
    );
    assert_eq!(std::fs::read(&source).unwrap(), original);
    assert!(retry.is_file());
    let probe = Command::new("/opt/homebrew/bin/ffprobe")
        .args(["-v", "error", "-show_streams", "-of", "json"])
        .arg(&retry)
        .output()
        .unwrap();
    assert!(probe.status.success());
    let info: Value = serde_json::from_slice(&probe.stdout).unwrap();
    let video = &info["streams"][0];
    assert_eq!(video["codec_name"], "h264");
    assert_eq!(
        (video["width"].as_i64(), video["height"].as_i64()),
        (Some(720), Some(1280))
    );
    assert!(
        Command::new("/opt/homebrew/bin/ffmpeg")
            .args(["-v", "error", "-xerror", "-i"])
            .arg(&retry)
            .args(["-f", "null", "-"])
            .status()
            .unwrap()
            .success()
    );
    drop(engine);
    std::fs::remove_file(retry).unwrap();
    std::fs::remove_file(source).unwrap();
    std::fs::remove_dir(root).unwrap();
}
