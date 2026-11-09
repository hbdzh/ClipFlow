//! Independent macOS runner probe. Fixed test commands, no production imports.
use media_ai::runner::{run_group, RunError};
use std::{ffi::OsString, path::Path, sync::atomic::AtomicBool, time::Duration};

fn main() {
    assert_eq!(std::env::var("AI001_QA_SENTINEL").as_deref(), Ok("private-sentinel"));
    let flag=AtomicBool::new(false);
    let env=run_group(Path::new("/usr/bin/env"), &[], Duration::from_secs(3), &flag, 16384).expect("environment test child");
    let text=String::from_utf8(env.stdout).expect("UTF-8");
    assert!(!text.contains("AI001_QA_SENTINEL="), "parent secret leaked");
    assert!(!text.contains("PYTHONPATH="), "untrusted Python search path leaked");
    assert!(!text.contains("HOME="), "HOME inherited");
    assert!(text.lines().any(|x|x=="PYTHONNOUSERSITE=1"));
    assert!(text.lines().any(|x|x=="PATH=/usr/bin:/bin"));
    let script = "i=0; while [ $i -lt 600 ]; do printf x; i=$((i+1)); done; i=0; while [ $i -lt 600 ]; do printf y >&2; i=$((i+1)); done; exec /bin/sleep 10";
    let args:Vec<OsString>=vec!["-c".into(),script.into()];
    let out=run_group(Path::new("/bin/sh"),&args,Duration::from_secs(3),&flag,1000);
    assert!(matches!(out,Err(RunError::OutputLimit)),"combined stdout/stderr should be limited: {out:?}");
    println!("environment_sentinel_and_combined_output_limit=PASS");
}
