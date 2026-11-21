use media_domain::CoreError;
use media_orchestrator::Engine;
use serde_json::{Value, json};

fn request() -> Vec<u8> {
    json!({"schema_version":0,"operation":"storage_preview","items":[{"schema_version":0,"operation":"transcode","source":{"uri":"file:///definitely-missing-media-preview/source.mp4"},"destination":{"uri":"file:///definitely-missing-media-preview/target.mp4"},"profile":"mp4_h264_standard_v1"}]}).to_string().into_bytes()
}

#[test]
fn legacy_engine_rejects_preview_without_turning_it_into_a_job() {
    let mut engine = Engine::new(br#"{"schema_version":0}"#).unwrap();
    assert_eq!(
        engine.submit(&request()).unwrap_err(),
        CoreError::UnsupportedOperation
    );
    assert!(!engine.has_active_jobs());
}

#[cfg(target_os = "macos")]
#[test]
fn failed_preview_allocates_no_jobs_or_persistent_resources() {
    let mut engine = Engine::new(br#"{"schema_version":0,"media_backend":{"ffmpeg":"/definitely-missing-media-preview/ffmpeg","ffprobe":"/definitely-missing-media-preview/ffprobe","core003_enabled":true,"core004_enabled":true}}"#).unwrap();
    let snapshot = |engine: &mut Engine| -> Value {
        engine
            .submit(br#"{"schema_version":0,"operation":"queue_snapshot"}"#)
            .unwrap()
    };
    let before = snapshot(&mut engine);
    let result = engine.submit(&request()).unwrap();
    assert_eq!(result["operation"], "storage_preview");
    assert_eq!(result["ready"], false);
    assert!(result.get("accepted").is_none() && result.get("job_ids").is_none());
    assert_eq!(result["errors"][0]["index"], 0);
    assert!(
        !result
            .to_string()
            .contains("definitely-missing-media-preview")
    );
    assert_eq!(snapshot(&mut engine), before);
    assert!(!engine.has_active_jobs());
}
