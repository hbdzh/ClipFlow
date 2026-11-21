#![cfg(target_os = "macos")]
use media_orchestrator::Engine;
use serde_json::{Value, json};
use std::{
    path::Path,
    process::Command,
    thread,
    time::{Duration, Instant},
};
fn submit(engine: &mut Engine, v: Value) -> Value {
    engine.submit(v.to_string().as_bytes()).unwrap()
}
fn wait(engine: &mut Engine, id: &str) -> Value {
    let deadline = Instant::now() + Duration::from_secs(60);
    loop {
        let s = engine.snapshot(id).unwrap();
        if ["succeeded", "failed", "cancelled"].contains(&s["state"].as_str().unwrap()) {
            return s;
        }
        assert!(Instant::now() < deadline, "{s}");
        thread::sleep(Duration::from_millis(10));
    }
}
#[test]
#[ignore = "requires macOS FFmpeg and ffprobe"]
fn real_mix_preserves_samples_and_rejects_bad_inputs() {
    let root = std::env::temp_dir().join(format!(
        "ms-mix-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    std::fs::create_dir(&root).unwrap();
    let vocals = root.join("vocals.wav");
    let accomp = root.join("accompaniment.wav");
    for (path, sample) in [(&vocals, "1.6"), (&accomp, "1.2")] {
        assert!(
            Command::new("/opt/homebrew/bin/ffmpeg")
                .args([
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    &format!("aevalsrc={sample}|{sample}:s=44100:d=2"),
                    "-c:a",
                    "pcm_f32le"
                ])
                .arg(path)
                .status()
                .unwrap()
                .success()
        );
    }
    let cfg = json!({"schema_version":0,"media_backend":{"ffmpeg":"/opt/homebrew/bin/ffmpeg","ffprobe":"/opt/homebrew/bin/ffprobe","core003_enabled":true,"core004_enabled":true,"mix_stems_enabled":true}});
    let mut engine = Engine::new(cfg.to_string().as_bytes()).unwrap();
    let uri = |p: &Path| media_domain::file_uri(p).unwrap();
    for (iv, ia, expected) in [
        (true, true, 1.4_f32),
        (true, false, 0.8_f32),
        (false, true, 0.6_f32),
    ] {
        let dst = root.join(format!("{iv}-{ia}.wav"));
        let req = json!({"schema_version":0,"operation":"mix_stems","vocals":{"uri":uri(&vocals)},"accompaniment":{"uri":uri(&accomp)},"destination":{"uri":uri(&dst)},"include_vocals":iv,"include_accompaniment":ia});
        let s = submit(&mut engine, req);
        let id = s["job_id"].as_str().unwrap_or_else(|| panic!("{s}"));
        let done = wait(&mut engine, id);
        assert_eq!(done["state"], "succeeded", "{done}");
        assert_eq!(done["result"]["sample_count"], 88200);
        engine.cancel(id).unwrap();
        assert_eq!(engine.snapshot(id).unwrap()["state"], "succeeded");
        assert!(dst.exists());
        let decoded = Command::new("/opt/homebrew/bin/ffmpeg")
            .args(["-v", "error", "-xerror", "-i"])
            .arg(&dst)
            .args(["-f", "f32le", "-acodec", "pcm_f32le", "-"])
            .output()
            .unwrap();
        assert!(decoded.status.success());
        assert_eq!(decoded.stdout.len(), 88200 * 2 * 4);
        for chunk in decoded.stdout.as_chunks::<4>().0 {
            let actual = f32::from_le_bytes(*chunk);
            assert!((actual - expected).abs() < 0.0001, "{actual} != {expected}");
        }
    }
    let same = json!({"schema_version":0,"operation":"mix_stems","vocals":{"uri":uri(&vocals)},"accompaniment":{"uri":uri(&vocals)},"destination":{"uri":uri(&root.join("bad.wav"))},"include_vocals":true,"include_accompaniment":true});
    let rejected = submit(&mut engine, same);
    assert_eq!(rejected["errors"][0]["code"], "INVALID_INPUT", "{rejected}");
    assert!(!root.join("bad.wav").exists());

    let hardlink = root.join("same-inode.wav");
    std::fs::hard_link(&vocals, &hardlink).unwrap();
    let base = json!({"schema_version":0,"operation":"mix_stems","vocals":{"uri":uri(&vocals)},"accompaniment":{"uri":uri(&hardlink)},"destination":{"uri":uri(&root.join("alias-output.wav"))},"include_vocals":true,"include_accompaniment":false});
    let rejected = submit(&mut engine, base.clone());
    assert_eq!(rejected["errors"][0]["code"], "INVALID_INPUT", "{rejected}");
    let mut existing = base.clone();
    existing["accompaniment"]["uri"] = json!(uri(&accomp));
    existing["destination"]["uri"] = json!(uri(&accomp));
    let rejected = submit(&mut engine, existing);
    assert_eq!(rejected["errors"][0]["code"], "OUTPUT_EXISTS", "{rejected}");
    let shorter = root.join("shorter.wav");
    assert!(
        Command::new("/opt/homebrew/bin/ffmpeg")
            .args([
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "aevalsrc=0.1|0.1:s=44100:d=1",
                "-c:a",
                "pcm_f32le"
            ])
            .arg(&shorter)
            .status()
            .unwrap()
            .success()
    );
    let mut mismatch = base.clone();
    mismatch["accompaniment"]["uri"] = json!(uri(&shorter));
    let rejected = submit(&mut engine, mismatch);
    assert_eq!(rejected["errors"][0]["code"], "INVALID_INPUT", "{rejected}");
    let pcm16 = root.join("pcm16.wav");
    assert!(
        Command::new("/opt/homebrew/bin/ffmpeg")
            .args([
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "aevalsrc=0.1|0.1:s=44100:d=2",
                "-c:a",
                "pcm_s16le"
            ])
            .arg(&pcm16)
            .status()
            .unwrap()
            .success()
    );
    let mut unsupported = base.clone();
    unsupported["accompaniment"]["uri"] = json!(uri(&pcm16));
    let rejected = submit(&mut engine, unsupported);
    assert_eq!(
        rejected["errors"][0]["code"], "OUTPUT_VALIDATION_FAILED",
        "{rejected}"
    );
    let nan_stem = root.join("nonfinite.wav");
    let mut bytes = std::fs::read(&accomp).unwrap();
    let data_pos = bytes.windows(4).position(|w| w == b"data").unwrap();
    bytes[data_pos + 8..data_pos + 12].copy_from_slice(&f32::NAN.to_le_bytes());
    std::fs::write(&nan_stem, bytes).unwrap();
    let mut inaudible = base.clone();
    inaudible["accompaniment"]["uri"] = json!(uri(&nan_stem));
    let rejected = submit(&mut engine, inaudible);
    assert_eq!(
        rejected["errors"][0]["code"], "OUTPUT_VALIDATION_FAILED",
        "{rejected}"
    );
    assert!(!root.join("alias-output.wav").exists());
    let mut disabled = base;
    disabled["include_vocals"] = json!(false);
    disabled["include_accompaniment"] = json!(false);
    assert!(engine.submit(disabled.to_string().as_bytes()).is_err());
    drop(engine);
    std::fs::remove_dir_all(root).unwrap();
}
#[test]
#[ignore = "requires macOS FFmpeg and process observation"]
fn cancel_after_mix_process_writes_staging() {
    let root = std::env::temp_dir().join(format!(
        "ms-mix-cancel-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    std::fs::create_dir(&root).unwrap();
    let vocals = root.join("vocals.wav");
    let accomp = root.join("accompaniment.wav");
    let out = root.join("mix.wav");
    assert!(
        Command::new("/opt/homebrew/bin/ffmpeg")
            .args([
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "aevalsrc=0.25|0.25:s=44100:d=180",
                "-c:a",
                "pcm_f32le"
            ])
            .arg(&vocals)
            .status()
            .unwrap()
            .success()
    );
    std::fs::copy(&vocals, &accomp).unwrap();
    use std::os::unix::fs::PermissionsExt;
    let wrapper = root.join("ffmpeg-slow");
    std::fs::write(&wrapper, "#!/usr/bin/python3\nimport os,sys\na=sys.argv[1:]\nif '-filter_complex' in a:\n i=a.index('-i'); a[i:i]=['-readrate','10']\nos.execv('/opt/homebrew/bin/ffmpeg',['ffmpeg']+a)\n").unwrap();
    std::fs::set_permissions(&wrapper, std::fs::Permissions::from_mode(0o700)).unwrap();
    let config = json!({"schema_version":0,"media_backend":{"ffmpeg":wrapper,"ffprobe":"/opt/homebrew/bin/ffprobe","core003_enabled":true,"core004_enabled":true,"mix_stems_enabled":true}});
    let mut engine = Engine::new(config.to_string().as_bytes()).unwrap();
    let uri = |p: &Path| media_domain::file_uri(p).unwrap();
    let response = submit(
        &mut engine,
        json!({"schema_version":0,"operation":"mix_stems","vocals":{"uri":uri(&vocals)},"accompaniment":{"uri":uri(&accomp)},"destination":{"uri":uri(&out)},"include_vocals":true,"include_accompaniment":true}),
    );
    let id = response["job_id"]
        .as_str()
        .unwrap_or_else(|| panic!("{response}"));
    let deadline = Instant::now() + Duration::from_secs(60);
    let child_pid = loop {
        let staged = std::fs::read_dir(&root)
            .unwrap()
            .filter_map(Result::ok)
            .map(|x| x.path().join("output.mp4"))
            .find(|p| p.metadata().is_ok_and(|m| m.len() > 4096));
        if let Some(path) = staged {
            let listing = Command::new("/bin/ps")
                .args(["-axo", "pid=,command="])
                .output()
                .unwrap();
            assert!(listing.status.success());
            let text = String::from_utf8_lossy(&listing.stdout);
            if let Some(line) = text.lines().find(|line| {
                line.contains("ffmpeg")
                    && line.contains("-filter_complex")
                    && line.contains(path.to_str().unwrap())
            }) {
                let pid: u32 = line.split_whitespace().next().unwrap().parse().unwrap();
                eprintln!(
                    "observed encoder pid={pid} staged_bytes={}",
                    path.metadata().unwrap().len()
                );
                break pid;
            }
        }
        assert!(Instant::now() < deadline, "mix encoder not observed");
        let state = engine.snapshot(id).unwrap();
        assert!(
            state["state"] == "queued" || state["state"] == "running",
            "{state}"
        );
        thread::sleep(Duration::from_millis(1));
    };
    engine.cancel(id).unwrap();
    let state = wait(&mut engine, id);
    assert_eq!(state["state"], "cancelled", "{state}");
    assert!(!out.exists());
    let remaining = std::fs::read_dir(&root)
        .unwrap()
        .map(|x| x.unwrap().file_name())
        .collect::<Vec<_>>();
    assert_eq!(remaining.len(), 3, "staging not cleaned: {remaining:?}");
    let listing = Command::new("/bin/ps")
        .args(["-p", &child_pid.to_string(), "-o", "command="])
        .output()
        .unwrap();
    assert!(
        !listing.status.success() || listing.stdout.is_empty(),
        "encoder still alive"
    );
    drop(engine);
    std::fs::remove_dir_all(root).unwrap();
}
