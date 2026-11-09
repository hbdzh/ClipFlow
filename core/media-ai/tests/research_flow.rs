//! Local integration experiment only: this is not the shared Core job engine.
#![cfg(target_os = "macos")]
use media_ai::{
    AtomicDirectory,
    runner::{RunError, run_group},
};
use std::{
    ffi::OsString,
    fs,
    path::{Path, PathBuf},
    sync::atomic::AtomicBool,
    time::Duration,
};
fn required(name: &str) -> PathBuf {
    PathBuf::from(std::env::var_os(name).expect(name))
}
fn worker_args(worker: &Path, model: &Path, input: &Path, staging: &Path) -> Vec<OsString> {
    vec![
        worker.as_os_str().into(),
        "--model".into(),
        model.as_os_str().into(),
        "--input".into(),
        input.as_os_str().into(),
        "--staging".into(),
        staging.as_os_str().into(),
    ]
}
#[test]
#[ignore = "requires locally provisioned model, runtime and trusted test fixture"]
fn real_worker_stages_two_stems_then_atomic_publish_and_failure_cleans() {
    let python = required("AI001_PYTHON");
    let worker = required("AI001_STREAM_WORKER");
    let model = required("AI001_MODEL");
    let input = required("AI001_INPUT");
    for p in [&python, &worker, &model, &input] {
        assert!(p.is_absolute() && p.is_file());
    }
    let root = std::env::temp_dir().join(format!("media-ai-research-flow-{}", std::process::id()));
    fs::create_dir(&root).expect("test root");
    let flag = AtomicBool::new(false);
    let mut tx = AtomicDirectory::new(&root, "result").expect("create staging");
    let response = run_group(
        &python,
        &worker_args(&worker, &model, &input, tx.staging()),
        Duration::from_secs(30),
        &flag,
        16384,
    )
    .expect("real worker");
    let text = String::from_utf8(response.stdout).unwrap();
    assert!(text.contains("\"schema_version\":1") && text.contains("\"channels\":2"));
    // Actual wave quality, finite values and source identity are checked by
    // independent QA, not implied by this transaction experiment.
    assert!(!root.join("result").exists());
    tx.commit().expect("one directory publication");
    assert!(
        root.join("result/vocals.wav").is_file() && root.join("result/accompaniment.wav").is_file()
    );
    let mut failed = AtomicDirectory::new(&root, "failed-result").unwrap();
    let partial = failed.staging().join("vocals.wav");
    let argv = vec![
        "-c".into(),
        "import os,sys; open(sys.argv[1],'xb').write(b'partial'); os._exit(17)".into(),
        partial.into_os_string(),
    ];
    assert!(matches!(
        run_group(&python, &argv, Duration::from_secs(10), &flag, 16384),
        Err(RunError::ProcessFailed)
    ));
    let stage = failed.staging().to_owned();
    failed
        .cleanup()
        .expect("remove test-owned partial after reap");
    assert!(!stage.exists() && !root.join("failed-result").exists());
    fs::remove_dir_all(root).expect("clean test fixture");
}
