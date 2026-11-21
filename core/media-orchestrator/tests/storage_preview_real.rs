#![cfg(target_os = "macos")]
use media_orchestrator::Engine;
use serde_json::{Value, json};
use std::path::PathBuf;
use std::process::Command;

fn call(engine: &mut Engine, value: Value) -> Value {
    engine
        .submit(value.to_string().as_bytes())
        .expect("Core response")
}

#[test]
#[ignore = "requires the accepted local FFmpeg 7 toolchain; run explicitly"]
fn real_preview_aggregates_same_volume_without_jobs_output_or_reservations() {
    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let root = std::env::temp_dir().join(format!("ms-preview-real-{}-{stamp}", std::process::id()));
    std::fs::create_dir(&root).expect("isolated directory");
    let source = root.join("source.mp4");
    let ffmpeg = PathBuf::from("/opt/homebrew/bin/ffmpeg");
    let ffprobe = PathBuf::from("/opt/homebrew/bin/ffprobe");
    assert!(
        Command::new(&ffmpeg)
            .args([
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=160x90:rate=30:duration=1",
                "-c:v",
                "libx264",
                "-threads",
                "1",
                "-pix_fmt",
                "yuv420p"
            ])
            .arg(&source)
            .status()
            .expect("generate synthetic fixture")
            .success()
    );
    let original = std::fs::read(&source).expect("source bytes");
    let config = json!({"schema_version":0,"media_backend":{"ffmpeg":ffmpeg,"ffprobe":ffprobe,"core003_enabled":true,"core004_enabled":true}});
    let mut engine = Engine::new(config.to_string().as_bytes()).expect("engine");
    let item = |name: &str| json!({"schema_version":0,"operation":"transcode","source":{"uri":media_domain::file_uri(&source).unwrap()},"destination":{"uri":media_domain::file_uri(&root.join(name)).unwrap()},"profile":"mp4_h264_standard_v1"});
    let before = call(
        &mut engine,
        json!({"schema_version":0,"operation":"queue_snapshot"}),
    );
    let response = call(
        &mut engine,
        json!({"schema_version":0,"operation":"storage_preview","items":[item("one.mp4"),item("two.mp4")]}),
    );
    assert_eq!(response["ready"], true, "{response}");
    assert_eq!(response["volumes"].as_array().unwrap().len(), 1);
    assert_eq!(response["volumes"][0]["item_indices"], json!([0, 1]));
    assert_eq!(
        response["volumes"][0]["estimated_output_bytes"],
        41_754_432_u64
    );
    assert_eq!(
        response["volumes"][0]["required_storage_bytes"],
        284_835_456_u64
    );
    assert_eq!(
        call(
            &mut engine,
            json!({"schema_version":0,"operation":"queue_snapshot"})
        ),
        before
    );
    assert_eq!(std::fs::read_dir(&root).unwrap().count(), 1);
    assert_eq!(std::fs::read(&source).unwrap(), original);
    assert!(!engine.has_active_jobs());
    let backend = media_domain::parse_config(config.to_string().as_bytes())
        .unwrap()
        .media_backend
        .unwrap();
    let media_domain::Request::Media(reserved_request) =
        media_domain::parse_request(item("reserved.mp4").to_string().as_bytes()).unwrap()
    else {
        panic!("media request");
    };
    let held = media_ffmpeg::preflight(
        &backend,
        &reserved_request,
        &std::sync::atomic::AtomicBool::new(false),
    )
    .expect("existing admission reservation");
    assert_eq!(held.summary()["reserved_storage_bytes"], 108_863_296_u64);
    let with_held = call(
        &mut engine,
        json!({"schema_version":0,"operation":"queue_snapshot"}),
    );
    let preview_with_held = call(
        &mut engine,
        json!({"schema_version":0,"operation":"storage_preview","items":[item("one.mp4"),item("two.mp4")]}),
    );
    assert_eq!(preview_with_held["ready"], true);
    assert_eq!(
        preview_with_held["volumes"][0]["required_storage_bytes"],
        393_698_752_u64
    );
    assert_eq!(
        call(
            &mut engine,
            json!({"schema_version":0,"operation":"queue_snapshot"})
        ),
        with_held
    );
    assert_eq!(std::fs::read_dir(&root).unwrap().count(), 1);
    drop(held);
    assert_eq!(
        call(
            &mut engine,
            json!({"schema_version":0,"operation":"queue_snapshot"})
        ),
        before
    );
    let target = root.join("existing.mp4");
    std::fs::write(&target, b"KEEP TARGET").unwrap();
    let rejected = call(
        &mut engine,
        json!({"schema_version":0,"operation":"storage_preview","items":[item("existing.mp4")]}),
    );
    assert_eq!(rejected["ready"], false);
    assert_eq!(rejected["errors"][0]["code"], "OUTPUT_EXISTS");
    assert_eq!(std::fs::read(&target).unwrap(), b"KEEP TARGET");
    assert_eq!(
        call(
            &mut engine,
            json!({"schema_version":0,"operation":"queue_snapshot"})
        ),
        before
    );
    drop(engine);
    std::fs::remove_file(target).unwrap();
    std::fs::remove_file(source).unwrap();
    std::fs::remove_dir(root).unwrap();
}
