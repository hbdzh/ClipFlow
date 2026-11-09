#![cfg(target_os = "macos")]
use media_ai::{AtomicDirectory, TransactionError, transaction_quarantined};
use std::fs;
#[test]
fn swapped_outer_directory_cannot_publish_replacement() {
    let root = std::env::temp_dir().join(format!("media-ai-outer-change-{}", std::process::id()));
    fs::create_dir(&root).unwrap();
    let mut tx = AtomicDirectory::new(&root, "result").unwrap();
    let ready = tx.staging().to_owned();
    let outer = ready.parent().unwrap().to_owned();
    fs::write(ready.join("vocals.wav"), b"original").unwrap();
    fs::write(ready.join("accompaniment.wav"), b"original").unwrap();
    let displaced = root.join("displaced");
    fs::rename(&outer, &displaced).unwrap();
    fs::create_dir(&outer).unwrap();
    fs::create_dir(outer.join("ready")).unwrap();
    fs::write(outer.join("ready/vocals.wav"), b"foreign").unwrap();
    fs::write(outer.join("ready/accompaniment.wav"), b"foreign").unwrap();
    assert_eq!(tx.commit().unwrap_err(), TransactionError::InvalidStaging);
    assert!(!root.join("result").exists());
    assert_eq!(
        tx.cleanup().unwrap_err(),
        TransactionError::CleanupUnconfirmed
    );
    assert!(transaction_quarantined());
    assert_eq!(
        fs::read(outer.join("ready/vocals.wav")).unwrap(),
        b"foreign"
    );
    assert_eq!(
        fs::read(displaced.join("ready/vocals.wav")).unwrap(),
        b"original"
    );
    drop(tx);
    fs::remove_dir_all(root).unwrap();
}
