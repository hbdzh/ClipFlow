#![cfg(target_os = "macos")]
use media_ai::{AtomicDirectory, TransactionError};
use std::{
    fs,
    path::PathBuf,
    sync::{
        Arc, Barrier,
        atomic::{AtomicU64, Ordering},
    },
    thread,
};

struct Fixture(PathBuf);
static NEXT: AtomicU64 = AtomicU64::new(1);
impl Fixture {
    fn new() -> Self {
        let p = std::env::temp_dir().join(format!(
            "media-ai-test-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&p).unwrap();
        Self(p)
    }
    fn fill(tx: &AtomicDirectory) {
        fs::write(tx.staging().join("vocals.wav"), b"vocals").unwrap();
        fs::write(tx.staging().join("accompaniment.wav"), b"accompaniment").unwrap();
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

#[test]
fn publishes_two_files_once_without_overwriting() {
    let f = Fixture::new();
    let mut tx = AtomicDirectory::new(&f.0, "result").unwrap();
    Fixture::fill(&tx);
    tx.commit().unwrap();
    assert_eq!(fs::read(f.0.join("result/vocals.wav")).unwrap(), b"vocals");
    assert_eq!(
        fs::read(f.0.join("result/accompaniment.wav")).unwrap(),
        b"accompaniment"
    );
    let mut second = AtomicDirectory::new(&f.0, "result").unwrap();
    Fixture::fill(&second);
    assert_eq!(second.commit().unwrap_err(), TransactionError::OutputExists);
    assert_eq!(fs::read(f.0.join("result/vocals.wav")).unwrap(), b"vocals");
    second.cleanup().unwrap();
}
#[test]
fn existing_files_empty_directories_and_dangling_links_are_not_replaced() {
    use std::os::unix::fs::symlink;
    let f = Fixture::new();
    fs::write(f.0.join("existing-file"), b"original").unwrap();
    fs::create_dir(f.0.join("existing-directory")).unwrap();
    symlink("missing-target", f.0.join("existing-link")).unwrap();
    for name in ["existing-file", "existing-directory", "existing-link"] {
        let mut tx = AtomicDirectory::new(&f.0, name).unwrap();
        Fixture::fill(&tx);
        assert_eq!(tx.commit().unwrap_err(), TransactionError::OutputExists);
        tx.cleanup().unwrap();
    }
    assert_eq!(fs::read(f.0.join("existing-file")).unwrap(), b"original");
    assert!(f.0.join("existing-directory").is_dir());
    assert!(
        fs::symlink_metadata(f.0.join("existing-link"))
            .unwrap()
            .file_type()
            .is_symlink()
    );
}
#[test]
fn refuses_unexpected_and_symlink_outputs() {
    use std::os::unix::fs::symlink;
    let f = Fixture::new();
    let mut tx = AtomicDirectory::new(&f.0, "result").unwrap();
    Fixture::fill(&tx);
    fs::remove_file(tx.staging().join("vocals.wav")).unwrap();
    symlink("/etc/passwd", tx.staging().join("vocals.wav")).unwrap();
    assert_eq!(tx.commit().unwrap_err(), TransactionError::InvalidStaging);
    assert!(!f.0.join("result").exists());
    // Fixture owns the intentional symlink; restore it before cleanup.
    fs::remove_file(tx.staging().join("vocals.wav")).unwrap();
    fs::write(tx.staging().join("vocals.wav"), b"vocals").unwrap();
    tx.cleanup().unwrap();
}
#[test]
fn concurrent_commit_has_exactly_one_winner() {
    let f = Fixture::new();
    let mut a = AtomicDirectory::new(&f.0, "result").unwrap();
    let mut b = AtomicDirectory::new(&f.0, "result").unwrap();
    Fixture::fill(&a);
    Fixture::fill(&b);
    let barrier = Arc::new(Barrier::new(2));
    let other = barrier.clone();
    let handle = thread::spawn(move || {
        other.wait();
        let result = a.commit();
        (a, result)
    });
    barrier.wait();
    let result = b.commit();
    let (mut a, ar) = handle.join().unwrap();
    assert_ne!(ar.is_ok(), result.is_ok());
    if let Err(error) = ar {
        assert_eq!(error, TransactionError::OutputExists);
        a.cleanup().unwrap();
    }
    if let Err(error) = result {
        assert_eq!(error, TransactionError::OutputExists);
        b.cleanup().unwrap();
    }
}
#[test]
fn rejects_invalid_leaf_names() {
    let f = Fixture::new();
    for name in ["", ".", "..", "a/b", "/absolute", "bad\0name"] {
        assert!(AtomicDirectory::new(&f.0, name).is_err());
    }
}
