use super::*;
use std::sync::atomic::AtomicUsize;
use std::time::{Duration, Instant};

const CONFIG: &[u8] =
    br#"{"schema_version":0,"media_backend":{"ffmpeg":"/test/ffmpeg","ffprobe":"/test/ffprobe"}}"#;
const PROBE: &[u8] =
    br#"{"schema_version":0,"operation":"probe","source":{"uri":"file:///test/input.mp4"}}"#;

fn wait_for(mut condition: impl FnMut() -> bool) {
    let start = Instant::now();
    while !condition() {
        assert!(
            start.elapsed() < Duration::from_secs(3),
            "worker did not reach bounded checkpoint"
        );
        std::thread::sleep(Duration::from_millis(1));
    }
}

#[test]
fn one_worker_queued_cancel_and_active_cleanup_are_observable_without_poll_driving_work() {
    let calls = Arc::new(AtomicUsize::new(0));
    let cleanup_started = Arc::new(AtomicBool::new(false));
    let release_cleanup = Arc::new(AtomicBool::new(false));
    let count = calls.clone();
    let started = cleanup_started.clone();
    let release = release_cleanup.clone();
    let executor: Executor = Arc::new(move |_, _, cancel| {
        count.fetch_add(1, Ordering::SeqCst);
        while !cancel.load(Ordering::SeqCst) {
            std::thread::sleep(Duration::from_millis(1));
        }
        started.store(true, Ordering::SeqCst);
        while !release.load(Ordering::SeqCst) {
            std::thread::sleep(Duration::from_millis(1));
        }
        Err(MediaError::new(
            "CANCELLED",
            "Controlled cleanup completed.",
        ))
    });
    let mut engine = Engine::with_executor(CONFIG, executor).unwrap();
    let first = engine.submit(PROBE).unwrap();
    let first = first["job_id"].as_str().unwrap().to_owned();
    wait_for(|| calls.load(Ordering::SeqCst) == 1);
    let queued = engine.submit(PROBE).unwrap();
    let queued = queued["job_id"].as_str().unwrap().to_owned();
    assert_eq!(engine.snapshot(&queued).unwrap()["state"], "queued");
    engine.cancel(&queued).unwrap();
    assert_eq!(engine.snapshot(&queued).unwrap()["state"], "cancelled");
    engine.cancel(&first).unwrap();
    wait_for(|| cleanup_started.load(Ordering::SeqCst));
    assert_eq!(engine.snapshot(&first).unwrap()["state"], "cancelling");
    assert!(engine.has_active_jobs());
    release_cleanup.store(true, Ordering::SeqCst);
    wait_for(|| engine.snapshot(&first).unwrap()["state"] == "cancelled");
    assert_eq!(
        calls.load(Ordering::SeqCst),
        1,
        "queued cancelled work must not execute"
    );
    assert!(!engine.has_active_jobs());
}

#[test]
fn engine_drop_cancels_and_joins_its_worker() {
    let entered = Arc::new(AtomicBool::new(false));
    let exited = Arc::new(AtomicBool::new(false));
    let begin = entered.clone();
    let end = exited.clone();
    let executor: Executor = Arc::new(move |_, _, cancel| {
        begin.store(true, Ordering::SeqCst);
        while !cancel.load(Ordering::SeqCst) {
            std::thread::sleep(Duration::from_millis(1));
        }
        end.store(true, Ordering::SeqCst);
        Err(MediaError::new(
            "CANCELLED",
            "Stopped before drop finished.",
        ))
    });
    let mut engine = Engine::with_executor(CONFIG, executor).unwrap();
    engine.submit(PROBE).unwrap();
    wait_for(|| entered.load(Ordering::SeqCst));
    drop(engine);
    assert!(exited.load(Ordering::SeqCst));
}

#[test]
fn worker_panic_becomes_failure_and_does_not_lose_queued_jobs() {
    let calls = Arc::new(AtomicUsize::new(0));
    let count = calls.clone();
    let executor: Executor = Arc::new(move |_, _, _| {
        if count.fetch_add(1, Ordering::SeqCst) == 0 {
            panic!("controlled executor panic");
        }
        Ok(json!({"schema_version":0,"mock_only":false,"file_created":false}))
    });
    let mut engine = Engine::with_executor(CONFIG, executor).unwrap();
    let first = engine.submit(PROBE).unwrap();
    let first = first["job_id"].as_str().unwrap().to_owned();
    let second = engine.submit(PROBE).unwrap();
    let second = second["job_id"].as_str().unwrap().to_owned();
    wait_for(|| engine.snapshot(&first).unwrap()["state"] == "failed");
    assert_eq!(
        engine.snapshot(&first).unwrap()["error"]["code"],
        "INTERNAL"
    );
    wait_for(|| engine.snapshot(&second).unwrap()["state"] == "succeeded");
    assert_eq!(calls.load(Ordering::SeqCst), 2);
}

#[test]
fn cancellation_losing_to_completed_commit_does_not_hide_created_file() {
    let entered = Arc::new(AtomicBool::new(false));
    let begin = entered.clone();
    let executor: Executor = Arc::new(move |_, _, cancel| {
        begin.store(true, Ordering::SeqCst);
        while !cancel.load(Ordering::SeqCst) {
            std::thread::sleep(Duration::from_millis(1));
        }
        Ok(
            json!({"schema_version":0,"mock_only":false,"file_created":true,"commit_already_completed":true}),
        )
    });
    let mut engine = Engine::with_executor(CONFIG, executor).unwrap();
    let job = engine.submit(PROBE).unwrap();
    let id = job["job_id"].as_str().unwrap().to_owned();
    wait_for(|| entered.load(Ordering::SeqCst));
    engine.cancel(&id).unwrap();
    wait_for(|| engine.snapshot(&id).unwrap()["state"] == "succeeded");
    assert_eq!(
        engine.snapshot(&id).unwrap()["result"]["file_created"],
        true
    );
}

#[test]
fn core003_validated_request_and_deadline_reach_the_same_worker_without_ghost_jobs() {
    let calls = Arc::new(AtomicUsize::new(0));
    let count = calls.clone();
    let executor: Executor = Arc::new(move |config, request, _| {
        assert!(config.core003_enabled);
        assert_eq!(config.timeout_ms, 30_000);
        assert_eq!(config.accurate_reencode_timeout_ms, 123_456);
        assert!(matches!(
            request,
            MediaRequest::Trim {
                strategy: media_domain::TrimStrategy::AccurateReencode,
                start_us: 1_100_000,
                end_us: 2_600_000,
                ..
            }
        ));
        count.fetch_add(1, Ordering::SeqCst);
        Ok(
            json!({"schema_version":0,"mock_only":false,"file_created":false,"controlled_executor":true}),
        )
    });
    let config = br#"{"schema_version":0,"media_backend":{"ffmpeg":"/test/ffmpeg","ffprobe":"/test/ffprobe","core003_enabled":true,"accurate_reencode_timeout_ms":123456}}"#;
    let mut engine = Engine::with_executor(config, executor).unwrap();
    let mut request = json!({"schema_version":0,"operation":"trim","source":{"uri":"file:///test/input.mp4"},
        "destination":{"uri":"file:///test/output.mp4"},"range":{"start_us":1100000,"end_us":2600000},
        "strategy":"accurate_reencode","stream_selection":"all_supported","allow_reencode_fallback":false});
    assert_eq!(
        engine.submit(request.to_string().as_bytes()).unwrap_err(),
        CoreError::InvalidArgument
    );
    assert_eq!(calls.load(Ordering::SeqCst), 0);
    assert!(!engine.has_active_jobs());
    request["profile"] = json!("mp4_h264_standard_v1");
    let submitted = engine.submit(request.to_string().as_bytes()).unwrap();
    assert_eq!(submitted["job_id"], "media-1");
    let id = submitted["job_id"].as_str().unwrap().to_owned();
    wait_for(|| engine.snapshot(&id).unwrap()["state"] == "succeeded");
    assert_eq!(calls.load(Ordering::SeqCst), 1);
    assert_eq!(engine.snapshot(&id).unwrap()["operation"], "trim");
    assert!(!engine.has_active_jobs());
}
