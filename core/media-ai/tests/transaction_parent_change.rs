#![cfg(target_os = "macos")]
use media_ai::{AtomicDirectory, TransactionError, transaction_quarantined};
use std::fs;
#[test]
fn parent_replacement_is_detected_and_cleanup_quarantines() {
    let base = std::env::temp_dir().join(format!("media-ai-parent-{}", std::process::id()));
    fs::create_dir(&base).unwrap();
    let parent = base.join("parent");
    let original = base.join("original");
    fs::create_dir(&parent).unwrap();
    let mut tx = AtomicDirectory::new(&parent, "out").unwrap();
    fs::write(tx.staging().join("vocals.wav"), b"v").unwrap();
    fs::write(tx.staging().join("accompaniment.wav"), b"a").unwrap();
    fs::rename(&parent, &original).unwrap();
    fs::create_dir(&parent).unwrap();
    fs::write(parent.join("keep"), b"untouched").unwrap();
    assert_eq!(tx.commit().unwrap_err(), TransactionError::ParentChanged);
    assert_eq!(
        tx.cleanup().unwrap_err(),
        TransactionError::CleanupUnconfirmed
    );
    assert!(transaction_quarantined());
    assert_eq!(fs::read(parent.join("keep")).unwrap(), b"untouched");
    assert!(!parent.join("out").exists());
    drop(tx);
    fs::remove_dir_all(base).unwrap();
}
