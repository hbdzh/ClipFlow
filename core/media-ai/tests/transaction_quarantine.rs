#![cfg(target_os = "macos")]
use media_ai::{AtomicDirectory, TransactionError, transaction_quarantined};
use std::fs;
#[test]
fn abandoned_owner_is_retained_and_new_transactions_are_blocked() {
    let root = std::env::temp_dir().join(format!("media-ai-quarantine-{}", std::process::id()));
    fs::create_dir(&root).unwrap();
    let mut tx = AtomicDirectory::new(&root, "out").unwrap();
    let mut pending = AtomicDirectory::new(&root, "pending").unwrap();
    fs::write(pending.staging().join("vocals.wav"), b"vocals").unwrap();
    fs::write(
        pending.staging().join("accompaniment.wav"),
        b"accompaniment",
    )
    .unwrap();
    let stage = tx.staging().to_owned();
    fs::write(stage.join("unknown"), b"do not delete").unwrap();
    // Cleanup failure is sticky immediately, even while the caller still
    // holds the original owner; already-created transactions cannot publish.
    assert_eq!(
        tx.cleanup().unwrap_err(),
        TransactionError::CleanupUnconfirmed
    );
    assert!(transaction_quarantined());
    assert_eq!(
        pending.commit().unwrap_err(),
        TransactionError::CleanupUnconfirmed
    );
    drop(tx);
    assert!(transaction_quarantined());
    assert_eq!(
        pending.commit().unwrap_err(),
        TransactionError::CleanupUnconfirmed
    );
    assert!(!root.join("pending").exists());
    pending.cleanup().unwrap();
    assert_eq!(fs::read(stage.join("unknown")).unwrap(), b"do not delete");
    assert!(matches!(
        AtomicDirectory::new(&root, "later"),
        Err(TransactionError::CleanupUnconfirmed)
    ));
    // Test cleanup of its own isolated fixture; the production registry does not
    // delete unknown content or declare the retained resource safe to reuse.
    fs::remove_dir_all(root).unwrap();
}
