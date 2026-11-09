#![cfg(target_os = "macos")]
use media_ai::{AtomicDirectory, TransactionError, transaction_quarantined};
use std::fs;
#[test]
fn replaced_stage_is_never_committed_or_cleaned() {
    let root = std::env::temp_dir().join(format!("media-ai-stage-change-{}", std::process::id()));
    fs::create_dir(&root).unwrap();
    let mut tx = AtomicDirectory::new(&root, "result").unwrap();
    let stage = tx.staging().to_owned();
    fs::write(stage.join("vocals.wav"), b"original vocals").unwrap();
    fs::write(stage.join("accompaniment.wav"), b"original accompaniment").unwrap();
    let displaced = root.join("displaced");
    fs::rename(&stage, &displaced).unwrap();
    fs::create_dir(&stage).unwrap();
    fs::write(stage.join("vocals.wav"), b"foreign vocals").unwrap();
    fs::write(stage.join("accompaniment.wav"), b"foreign accompaniment").unwrap();
    assert_eq!(tx.commit().unwrap_err(), TransactionError::InvalidStaging);
    assert!(!root.join("result").exists());
    assert_eq!(
        tx.cleanup().unwrap_err(),
        TransactionError::CleanupUnconfirmed
    );
    assert!(transaction_quarantined());
    assert_eq!(
        fs::read(stage.join("vocals.wav")).unwrap(),
        b"foreign vocals"
    );
    assert_eq!(
        fs::read(displaced.join("vocals.wav")).unwrap(),
        b"original vocals"
    );
    drop(tx);
    fs::remove_dir_all(root).unwrap();
}
