//! Independent same-group inherited-pipe/quarantine probe.
use media_ai::runner::{quarantine_count, run_group, RunError};
use std::{ffi::OsString, path::Path, sync::atomic::AtomicBool, time::{Duration,Instant}};
fn main() {
    let flag=AtomicBool::new(false);
    // Bounded same-group descendant; the harness lets it exit naturally.
    let args:Vec<OsString>=vec!["-c".into(),"sleep 2 & exit 0".into()];
    let started=Instant::now();
    let outcome=run_group(Path::new("/bin/sh"),&args,Duration::from_secs(4),&flag,4096);
    assert!(matches!(outcome,Err(RunError::CleanupUnconfirmed(_))),"inherited pipe must quarantine: {outcome:?}");
    assert!(started.elapsed()<Duration::from_secs(2),"must not wait indefinitely on inherited pipe");
    assert_eq!(quarantine_count(),1);
    drop(outcome);
    assert_eq!(quarantine_count(),1,"dropping receipt must retain owner");
    assert!(matches!(run_group(Path::new("/bin/sleep"),&["1".into()],Duration::from_secs(3),&flag,4096),Err(RunError::Quarantined)));
    std::thread::sleep(Duration::from_millis(2300));
    println!("leaked_pipe_and_owner_quarantine=PASS");
}
