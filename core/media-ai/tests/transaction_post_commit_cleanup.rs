#![cfg(target_os = "macos")]
use media_ai::{AtomicDirectory, CommitOutcome, TransactionError, transaction_quarantined};
use std::fs;
#[test]
fn published_result_remains_successful_when_outer_cleanup_is_unconfirmed() {
    let root = std::env::temp_dir().join(format!("media-ai-post-commit-{}", std::process::id()));
    fs::create_dir(&root).unwrap();
    let mut tx = AtomicDirectory::new(&root, "result").unwrap();
    let ready = tx.staging().to_owned();
    fs::write(ready.join("vocals.wav"), b"vocals").unwrap();
    fs::write(ready.join("accompaniment.wav"), b"accompaniment").unwrap();
    let outer = ready.parent().unwrap();
    fs::write(outer.join("unknown"), b"do not delete").unwrap();
    assert_eq!(
        tx.commit().unwrap(),
        CommitOutcome::CommittedCleanupUnconfirmed
    );
    assert_eq!(fs::read(root.join("result/vocals.wav")).unwrap(), b"vocals");
    assert_eq!(fs::read(outer.join("unknown")).unwrap(), b"do not delete");
    assert!(transaction_quarantined());
    assert!(matches!(
        AtomicDirectory::new(&root, "next"),
        Err(TransactionError::CleanupUnconfirmed)
    ));
    drop(tx);
    fs::remove_dir_all(root).unwrap();
}
