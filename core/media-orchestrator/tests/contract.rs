use media_domain::CoreError;
use media_orchestrator::Engine;

const CONFIG: &[u8] = br#"{"schema_version":0}"#;
const MOCK: &[u8] = br#"{"schema_version":0,"operation":"mock"}"#;

fn id(response: &serde_json::Value) -> &str {
    response["job_id"].as_str().unwrap()
}

#[test]
fn explicit_mock_success_and_failure_do_not_produce_files() {
    for (outcome, terminal) in [("succeed", "succeeded"), ("fail", "failed")] {
        let mut engine = Engine::new(CONFIG).unwrap();
        let request =
            format!("{{\"schema_version\":0,\"operation\":\"mock\",\"outcome\":\"{outcome}\"}}");
        let response = engine.submit(request.as_bytes()).unwrap();
        assert_eq!(response["schema_version"], 0);
        assert_eq!(response["state"], "queued");
        assert!(engine.has_active_jobs());
        assert_eq!(engine.snapshot(id(&response)).unwrap()["state"], "running");
        let done = engine.snapshot(id(&response)).unwrap();
        assert_eq!(done["state"], terminal);
        assert!(!engine.has_active_jobs());
        assert_eq!(engine.snapshot(id(&response)).unwrap(), done);
        if outcome == "succeed" {
            assert_eq!(done["result"]["mock_only"], true);
            assert_eq!(done["result"]["file_created"], false);
        } else {
            assert_eq!(done["error"]["code"], "MOCK_FAILED");
            assert!(done["result"].is_null());
        }
    }
}

#[test]
fn cancel_waits_for_ack_and_terminal_cancel_is_idempotent() {
    for after_running in [false, true] {
        let mut engine = Engine::new(CONFIG).unwrap();
        let submitted = engine.submit(MOCK).unwrap();
        let job = id(&submitted);
        if after_running {
            engine.snapshot(job).unwrap();
        }
        engine.cancel(job).unwrap();
        engine.cancel(job).unwrap();
        assert!(engine.has_active_jobs());
        assert_eq!(engine.snapshot(job).unwrap()["state"], "cancelling");
        assert!(engine.has_active_jobs());
        assert_eq!(engine.snapshot(job).unwrap()["state"], "cancelled");
        assert!(!engine.has_active_jobs());
        engine.cancel(job).unwrap();
        assert_eq!(engine.snapshot(job).unwrap()["state"], "cancelled");
    }
}

#[test]
fn retained_terminal_jobs_are_bounded_and_failed_submit_does_not_consume_capacity() {
    let mut engine = Engine::new(br#"{"schema_version":0,"max_jobs":1}"#).unwrap();
    assert_eq!(
        engine
            .submit(br#"{"schema_version":0,"operation":"trim"}"#)
            .unwrap_err(),
        CoreError::UnsupportedOperation
    );
    let response = engine.submit(MOCK).unwrap();
    engine.snapshot(id(&response)).unwrap();
    engine.snapshot(id(&response)).unwrap();
    assert_eq!(engine.submit(MOCK).unwrap_err(), CoreError::LimitExceeded);
    assert_eq!(
        engine.snapshot(id(&response)).unwrap()["state"],
        "succeeded"
    );
}

#[test]
fn snapshots_and_cancellation_reject_unknown_ids_without_mutation() {
    let mut engine = Engine::new(CONFIG).unwrap();
    assert_eq!(engine.snapshot("missing").unwrap_err(), CoreError::NotFound);
    assert_eq!(engine.cancel("missing").unwrap_err(), CoreError::NotFound);
    assert_eq!(engine.snapshot("").unwrap_err(), CoreError::InvalidArgument);
    assert!(!engine.has_active_jobs());
}

#[test]
fn real_media_features_remain_unavailable() {
    let engine = Engine::new(CONFIG).unwrap();
    let version = engine.version();
    assert_eq!(version["schema_version"], 0);
    assert_eq!(version["abi_version"], 0);
    let caps = engine.capabilities();
    for feature in [
        "probe",
        "trim_stream_copy",
        "accurate_reencode",
        "ai_separation",
    ] {
        assert_eq!(caps["features"][feature]["available"], false);
    }
    assert_eq!(caps["features"]["mock_job"]["available"], true);
    assert_eq!(caps["media_backend"]["name"], "mock");
}
