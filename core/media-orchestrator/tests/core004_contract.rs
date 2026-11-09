//! Queue control state is distinct from media jobs, including rejected batches.
use media_domain::CoreError;
use media_orchestrator::Engine;
use serde_json::{Value, json};
const CONFIG:&[u8]=br#"{"schema_version":0,"max_jobs":1,"media_backend":{"ffmpeg":"/not-installed/ffmpeg","ffprobe":"/not-installed/ffprobe","core003_enabled":true,"core004_enabled":true}}"#;
fn submit(engine: &mut Engine, value: Value) -> Value {
    engine.submit(value.to_string().as_bytes()).unwrap()
}
fn snapshot(engine: &mut Engine) -> Value {
    submit(
        engine,
        json!({"schema_version":0,"operation":"queue_snapshot"}),
    )
}
#[test]
fn control_messages_do_not_create_fake_jobs_or_consume_legacy_capacity() {
    let mut e = Engine::new(CONFIG).unwrap();
    assert_eq!(e.version()["stage"], "CORE-004");
    for _ in 0..110 {
        let v = snapshot(&mut e);
        assert_eq!(v["active_count"], 0);
        assert_eq!(v["queue_capacity"], 32);
        assert!(v.get("job_id").is_none());
    }
    let v = submit(
        &mut e,
        json!({"schema_version":0,"operation":"queue_reorder","job_ids":[]}),
    );
    assert_eq!(v["accepted"], true);
    let v = submit(
        &mut e,
        json!({"schema_version":0,"operation":"queue_reorder","job_ids":["ghost"]}),
    );
    assert_eq!(v["accepted"], false);
    assert_eq!(snapshot(&mut e)["queued_job_ids"], json!([]));
    assert_eq!(
        e.submit(br#"{"schema_version":0,"operation":"mock"}"#)
            .unwrap_err(),
        CoreError::UnsupportedOperation
    );
    let cleared = submit(
        &mut e,
        json!({"schema_version":0,"operation":"history_clear"}),
    );
    assert_eq!(cleared["cleared_count"], 0);
    assert!(!e.has_active_jobs());
}
#[test]
fn business_preflight_failure_rejects_whole_batch_without_ghost_id() {
    let mut e = Engine::new(CONFIG).unwrap();
    let item = json!({"schema_version":0,"operation":"transcode","source":{"uri":"file:///not-installed/input.mp4"},"destination":{"uri":"file:///tmp/core004-uncreated.mp4"},"profile":"mp4_h264_standard_v1"});
    let result = submit(
        &mut e,
        json!({"schema_version":0,"operation":"batch_submit","submission_id":"not-accepted","items":[item]}),
    );
    assert_eq!(result["accepted"], false);
    assert!(result.get("job_ids").is_none());
    assert_eq!(result["errors"][0]["index"], 0);
    let current = snapshot(&mut e);
    assert_eq!(current["active_count"], 0);
    assert_eq!(current["history"], json!([]));
    assert!(!e.has_active_jobs());
}
#[test]
fn old_explicit_backend_does_not_enable_new_operations_or_preflight() {
    let mut e=Engine::new(br#"{"schema_version":0,"media_backend":{"ffmpeg":"/not-installed/ffmpeg","ffprobe":"/not-installed/ffprobe"}}"#).unwrap();
    assert_eq!(
        e.submit(br#"{"schema_version":0,"operation":"queue_snapshot"}"#)
            .unwrap_err(),
        CoreError::UnsupportedOperation
    );
    assert_eq!(e.version()["stage"], "CORE-002");
    assert!(!e.has_active_jobs());
}
