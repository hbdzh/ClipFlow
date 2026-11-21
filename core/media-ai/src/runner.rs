//! macOS child-process group research component. Not an OS sandbox.
//!
//! A caller must only run a trusted worker which cannot detach or escape its
//! process group. A leaked pipe or unconfirmed group causes quarantine; callers
//! must retain the returned owner and stop using dependent resources.
#[cfg(target_os = "macos")]
use std::os::{
    fd::AsRawFd,
    unix::{ffi::OsStrExt, process::CommandExt},
};
use std::{
    ffi::OsString,
    io::{self, Read},
    path::Path,
    process::{Child, ChildStderr, ChildStdout, Command, ExitStatus, Stdio},
    sync::{
        Mutex, OnceLock,
        atomic::{AtomicBool, Ordering},
    },
    thread,
    time::{Duration, Instant},
};

static QUARANTINED: AtomicBool = AtomicBool::new(false);

/// Reports fail-closed state, including poison cases without a retained child.
pub fn is_quarantined() -> bool {
    QUARANTINED.load(Ordering::Acquire)
}
static RUNNER_GATE: Mutex<()> = Mutex::new(());
static OWNERS: OnceLock<Mutex<Vec<Owner>>> = OnceLock::new();
#[cfg(test)]
static TEST_PANIC_AFTER_SPAWN: AtomicBool = AtomicBool::new(false);
const POLL: Duration = Duration::from_millis(5);
const REAP_TIMEOUT: Duration = Duration::from_secs(3);

#[derive(Debug)]
pub struct RunOutput {
    pub stdout: Vec<u8>,
    pub stderr: Vec<u8>,
}

/// An exit status is exposed only after the child/group and both pipes have
/// been confirmed reclaimed. Callers must validate their own fixed protocol.
#[derive(Debug)]
pub struct RunExitOutput {
    pub output: RunOutput,
    pub status: ExitStatus,
}

#[derive(Debug)]
pub enum RunError {
    InvalidArgument,
    UnsupportedPlatform,
    DependencyMissing,
    ProcessFailed,
    OutputLimit,
    TimedOut,
    Cancelled,
    Quarantined,
    /// A process owner is held in the permanent registry; callers must also
    /// quarantine the associated staging and authorization resources.
    CleanupUnconfirmed(Quarantine),
}

pub struct Quarantine {
    pub receipt: usize,
}
impl std::fmt::Debug for Quarantine {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("Quarantine(redacted)")
    }
}

struct Process {
    child: Child,
    out: ChildStdout,
    err: ChildStderr,
    out_eof: bool,
    err_eof: bool,
    pgid: i32,
    reaped: bool,
}

// Records are intentionally held until process exit: clearing one without an
// audited recovery protocol would release handles for unconfirmed children.
enum Owner {
    Full {
        _process: Process,
    },
    Partial {
        _child: Child,
        _out: Option<ChildStdout>,
        _err: Option<ChildStderr>,
    },
}

fn register_owner(owner: Owner) -> Quarantine {
    QUARANTINED.store(true, Ordering::Release);
    let mut entries = OWNERS
        .get_or_init(|| Mutex::new(Vec::new()))
        .lock()
        .unwrap_or_else(|poison| poison.into_inner());
    entries.push(owner);
    Quarantine {
        receipt: entries.len(),
    }
}

/// Number of unconfirmed process owners permanently held by this process.
pub fn quarantine_count() -> usize {
    OWNERS.get().map_or(0, |items| {
        items
            .lock()
            .unwrap_or_else(|poison| poison.into_inner())
            .len()
    })
}

#[cfg(target_os = "macos")]
struct ProcessGuard(Option<Process>);
#[cfg(target_os = "macos")]
impl ProcessGuard {
    fn process(&mut self) -> &mut Process {
        self.0.as_mut().expect("live process invariant")
    }
    fn take(&mut self) -> Process {
        self.0.take().expect("live process invariant")
    }
}
#[cfg(target_os = "macos")]
impl Drop for ProcessGuard {
    fn drop(&mut self) {
        if let Some(process) = self.0.take() {
            if !process.reaped {
                let _ = group_signal(process.pgid, 9);
            }
            let _ = register_owner(Owner::Full { _process: process });
        }
    }
}

#[cfg(target_os = "macos")]
struct SpawnGuard(Option<Child>);
#[cfg(target_os = "macos")]
impl Drop for SpawnGuard {
    fn drop(&mut self) {
        if let Some(mut child) = self.0.take() {
            if let Ok(pid) = i32::try_from(child.id()) {
                let _ = group_signal(pid, 9);
            } else {
                let _ = child.kill();
            }
            let out = child.stdout.take();
            let err = child.stderr.take();
            let _ = register_owner(Owner::Partial {
                _child: child,
                _out: out,
                _err: err,
            });
        }
    }
}

#[cfg(target_os = "macos")]
unsafe extern "C" {
    fn fcntl(fd: std::ffi::c_int, cmd: std::ffi::c_int, ...) -> std::ffi::c_int;
    fn kill(pid: std::ffi::c_int, sig: std::ffi::c_int) -> std::ffi::c_int;
}

#[cfg(target_os = "macos")]
fn nonblocking(fd: std::ffi::c_int) -> io::Result<()> {
    // SAFETY: fd is a live borrowed pipe; F_GETFL and F_SETFL take integer
    // values, no pointer is involved. Constants match macOS SDK sys/fcntl.h.
    let flags = unsafe { fcntl(fd, 3) };
    if flags < 0 {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: as above, preserving original flags and adding O_NONBLOCK.
    if unsafe { fcntl(fd, 4, flags | 0x4) } < 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

#[cfg(target_os = "macos")]
fn group_signal(pgid: i32, sig: i32) -> io::Result<()> {
    if pgid <= 1 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "invalid process group",
        ));
    }
    // SAFETY: pid is a validated positive direct-child process group ID; a
    // negative pid addresses that group. No pointers or retained borrows.
    if unsafe { kill(-pgid, sig) } == 0 {
        Ok(())
    } else {
        Err(io::Error::last_os_error())
    }
}

#[cfg(target_os = "macos")]
fn group_gone(pgid: i32) -> io::Result<bool> {
    match group_signal(pgid, 0) {
        Ok(()) => Ok(false),
        Err(e) if e.raw_os_error() == Some(3) => Ok(true),
        Err(e) => Err(e),
    }
}

fn drain<R: Read>(
    reader: &mut R,
    eof: &mut bool,
    dst: &mut Vec<u8>,
    used: &mut usize,
    limit: usize,
) -> io::Result<bool> {
    if *eof {
        return Ok(false);
    }
    loop {
        let mut buf = [0u8; 8192];
        match reader.read(&mut buf) {
            Ok(0) => {
                *eof = true;
                return Ok(false);
            }
            Ok(n) => {
                let room = limit.saturating_sub(*used);
                let take = n.min(room);
                dst.extend_from_slice(&buf[..take]);
                *used = used.saturating_add(take);
                if take < n {
                    return Ok(true);
                }
            }
            Err(e) if e.kind() == io::ErrorKind::WouldBlock => return Ok(false),
            Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
            Err(e) => return Err(e),
        }
    }
}

#[cfg(target_os = "macos")]
fn quarantine(process: Process) -> RunError {
    RunError::CleanupUnconfirmed(register_owner(Owner::Full { _process: process }))
}

#[cfg(target_os = "macos")]
fn terminate(mut guard: ProcessGuard, reason: RunError) -> RunError {
    // Direct child remains unreaped when signalled, preventing PID reuse.
    let process = guard.process();
    if !process.reaped {
        match group_signal(process.pgid, 9) {
            Ok(()) => (),
            Err(e) if e.raw_os_error() == Some(3) => (),
            Err(_) => return quarantine(guard.take()),
        }
    }
    let until = Instant::now() + REAP_TIMEOUT;
    let mut discard_out = Vec::new();
    let mut discard_err = Vec::new();
    let mut used = 0usize;
    loop {
        let process = guard.process();
        if !process.reaped {
            match process.child.try_wait() {
                Ok(Some(_)) => process.reaped = true,
                Ok(None) => (),
                Err(_) => return quarantine(guard.take()),
            }
        }
        // Nonblocking drains avoid an unbounded join when a descendant holds
        // an inherited pipe. Never wait unboundedly on child or pipe handles.
        let _ = drain(
            &mut process.out,
            &mut process.out_eof,
            &mut discard_out,
            &mut used,
            0,
        );
        let _ = drain(
            &mut process.err,
            &mut process.err_eof,
            &mut discard_err,
            &mut used,
            0,
        );
        if process.reaped && process.out_eof && process.err_eof {
            match group_gone(process.pgid) {
                Ok(true) => {
                    drop(guard.take());
                    return reason;
                }
                Ok(false) => (),
                Err(_) => return quarantine(guard.take()),
            }
        }
        if Instant::now() >= until {
            return quarantine(guard.take());
        }
        thread::sleep(POLL);
    }
}

/// Launch a fixed trusted executable with argv (never a shell), stripped
/// environment, bounded combined diagnostics, timeout and group cancellation.
///
/// This function does not validate executable/worker identity, establish an OS
/// sandbox, detect a descendant that escaped the group and closed its pipes,
/// clean media files, or implement a Core job. Caller must gate these separately.
#[cfg(target_os = "macos")]
pub fn run_group(
    program: &Path,
    args: &[OsString],
    timeout: Duration,
    cancel: &AtomicBool,
    output_limit: usize,
) -> Result<RunOutput, RunError> {
    let value = run_group_with_status(program, args, timeout, cancel, output_limit)?;
    if value.status.success() {
        Ok(value.output)
    } else {
        Err(RunError::ProcessFailed)
    }
}

/// As `run_group`, retaining the confirmed exit status for a fixed caller-owned
/// native protocol. No status escapes before cancellation/cleanup checks.
#[cfg(target_os = "macos")]
pub fn run_group_with_status(
    program: &Path,
    args: &[OsString],
    timeout: Duration,
    cancel: &AtomicBool,
    output_limit: usize,
) -> Result<RunExitOutput, RunError> {
    if QUARANTINED.load(Ordering::Acquire) {
        return Err(RunError::Quarantined);
    }
    if !program.is_absolute()
        || program.as_os_str().as_bytes().contains(&0)
        || args.len() > 64
        || args
            .iter()
            .any(|arg| arg.as_os_str().as_bytes().contains(&0))
        || args
            .iter()
            .try_fold(program.as_os_str().as_bytes().len() + 1, |n, arg| {
                n.checked_add(arg.as_os_str().as_bytes().len() + 1)
            })
            .is_none_or(|n| n > 65_536)
        || timeout.is_zero()
        || timeout > Duration::from_secs(7200)
        || !(1..=1024 * 1024).contains(&output_limit)
    {
        return Err(RunError::InvalidArgument);
    }
    if cancel.load(Ordering::Acquire) {
        return Err(RunError::Cancelled);
    }
    let deadline = Instant::now()
        .checked_add(timeout)
        .ok_or(RunError::InvalidArgument)?;
    // Serialize all AI runner children to avoid a concurrent launch crossing a
    // quarantine transition. The higher-level engine must also hold the shared
    // heavy-media permit; this gate is not a substitute for that scheduler.
    let _gate = loop {
        if QUARANTINED.load(Ordering::Acquire) {
            return Err(RunError::Quarantined);
        }
        if cancel.load(Ordering::Acquire) {
            return Err(RunError::Cancelled);
        }
        if Instant::now() >= deadline {
            return Err(RunError::TimedOut);
        }
        match RUNNER_GATE.try_lock() {
            Ok(lock) => break lock,
            Err(std::sync::TryLockError::WouldBlock) => thread::sleep(POLL),
            Err(std::sync::TryLockError::Poisoned(_)) => {
                QUARANTINED.store(true, Ordering::Release);
                return Err(RunError::Quarantined);
            }
        }
    };
    if QUARANTINED.load(Ordering::Acquire) {
        return Err(RunError::Quarantined);
    }
    let child = Command::new(program)
        .args(args)
        .env_clear()
        .env("LC_ALL", "C")
        .env("PATH", "/usr/bin:/bin")
        .env("PYTHONNOUSERSITE", "1")
        .env("OMP_NUM_THREADS", "2")
        .env("OPENBLAS_NUM_THREADS", "2")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .process_group(0)
        .spawn()
        .map_err(|_| RunError::DependencyMissing)?;
    // From this instruction onward every unwind owns the child in a guard.
    let mut spawned = SpawnGuard(Some(child));
    let child = spawned.0.as_mut().expect("child invariant");
    let pid = i32::try_from(child.id()).unwrap_or(0);
    let out = child.stdout.take();
    let err = child.stderr.take();
    if out.is_none() || err.is_none() {
        let mut child = spawned.0.take().expect("child invariant");
        if pid > 1 {
            let _ = group_signal(pid, 9);
        } else {
            let _ = child.kill();
        }
        // Preserve the child and whichever pipe exists, with no blocking wait.
        return Err(RunError::CleanupUnconfirmed(register_owner(
            Owner::Partial {
                _child: child,
                _out: out,
                _err: err,
            },
        )));
    }
    let out = out.expect("pipe invariant");
    let err = err.expect("pipe invariant");
    let child = spawned.0.take().expect("child invariant");
    let mut guard = ProcessGuard(Some(Process {
        child,
        out,
        err,
        out_eof: false,
        err_eof: false,
        pgid: pid,
        reaped: false,
    }));
    #[cfg(test)]
    if TEST_PANIC_AFTER_SPAWN.swap(false, Ordering::AcqRel) {
        panic!("controlled post-spawn panic");
    }
    if pid <= 1 {
        let _ = guard.process().child.kill();
        return Err(quarantine(guard.take()));
    }
    let process = guard.process();
    let out_nonblocking = nonblocking(process.out.as_raw_fd());
    let err_nonblocking = nonblocking(process.err.as_raw_fd());
    if out_nonblocking.is_err() || err_nonblocking.is_err() {
        // A pipe may still block; never read it here. Retain all ownership.
        let _ = group_signal(pid, 9);
        return Err(quarantine(guard.take()));
    }
    let mut stdout = Vec::new();
    let mut stderr = Vec::new();
    let mut used = 0usize;
    loop {
        if cancel.load(Ordering::Acquire) {
            return Err(terminate(guard, RunError::Cancelled));
        }
        if Instant::now() >= deadline {
            return Err(terminate(guard, RunError::TimedOut));
        }
        let process = guard.process();
        match drain(
            &mut process.out,
            &mut process.out_eof,
            &mut stdout,
            &mut used,
            output_limit,
        ) {
            Ok(true) => return Err(terminate(guard, RunError::OutputLimit)),
            Ok(false) => (),
            Err(_) => return Err(terminate(guard, RunError::ProcessFailed)),
        }
        match drain(
            &mut process.err,
            &mut process.err_eof,
            &mut stderr,
            &mut used,
            output_limit,
        ) {
            Ok(true) => return Err(terminate(guard, RunError::OutputLimit)),
            Ok(false) => (),
            Err(_) => return Err(terminate(guard, RunError::ProcessFailed)),
        }
        match process.child.try_wait() {
            Ok(Some(status)) => {
                process.reaped = true;
                // The leader has been reaped: never signal the numeric group
                // after this point because its identity might be reused.
                let a = drain(
                    &mut process.out,
                    &mut process.out_eof,
                    &mut stdout,
                    &mut used,
                    output_limit,
                );
                let b = drain(
                    &mut process.err,
                    &mut process.err_eof,
                    &mut stderr,
                    &mut used,
                    output_limit,
                );
                if matches!(a, Ok(true)) || matches!(b, Ok(true)) {
                    return Err(terminate(guard, RunError::OutputLimit));
                }
                if a.is_err() || b.is_err() || !process.out_eof || !process.err_eof {
                    return Err(quarantine(guard.take()));
                }
                match group_gone(process.pgid) {
                    Ok(true) => (),
                    _ => return Err(quarantine(guard.take())),
                }
                drop(guard.take());
                return Ok(RunExitOutput {
                    output: RunOutput { stdout, stderr },
                    status,
                });
            }
            Ok(None) => (),
            Err(_) => return Err(terminate(guard, RunError::ProcessFailed)),
        }
        thread::sleep(POLL);
    }
}

#[cfg(not(target_os = "macos"))]
pub fn run_group(
    _program: &Path,
    _args: &[OsString],
    _timeout: Duration,
    _cancel: &AtomicBool,
    _output_limit: usize,
) -> Result<RunOutput, RunError> {
    Err(RunError::UnsupportedPlatform)
}

#[cfg(not(target_os = "macos"))]
pub fn run_group_with_status(
    _program: &Path,
    _args: &[OsString],
    _timeout: Duration,
    _cancel: &AtomicBool,
    _output_limit: usize,
) -> Result<RunExitOutput, RunError> {
    Err(RunError::UnsupportedPlatform)
}

#[cfg(all(test, target_os = "macos"))]
mod tests {
    use super::*;

    #[test]
    fn explicit_status_keeps_generic_runner_nonzero_rejection() {
        let flag = AtomicBool::new(false);
        let value = run_group_with_status(
            Path::new("/usr/bin/false"),
            &[],
            Duration::from_secs(3),
            &flag,
            1024,
        )
        .expect("confirmed nonzero child");
        assert!(!value.status.success());
        assert_eq!(value.status.code(), Some(1));
        assert!(matches!(
            run_group(
                Path::new("/usr/bin/false"),
                &[],
                Duration::from_secs(3),
                &flag,
                1024
            ),
            Err(RunError::ProcessFailed)
        ));
    }

    fn test_child() -> Process {
        let mut child = Command::new("/bin/sleep")
            .arg("10")
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .process_group(0)
            .spawn()
            .expect("child");
        let pgid = i32::try_from(child.id()).expect("pid");
        let out = child.stdout.take().expect("stdout");
        let err = child.stderr.take().expect("stderr");
        Process {
            child,
            out,
            err,
            out_eof: false,
            err_eof: false,
            pgid,
            reaped: false,
        }
    }

    #[test]
    fn unwind_and_dropped_error_preserve_registry_owners() {
        const CHILD: &str = "MEDIASTUDIO_AI001_UNWIND_TEST_CHILD";
        if std::env::var_os(CHILD).is_none() {
            let output = Command::new(std::env::current_exe().expect("test binary"))
                .arg("--exact")
                .arg("runner::tests::unwind_and_dropped_error_preserve_registry_owners")
                .arg("--nocapture")
                .env(CHILD, "1")
                .output()
                .expect("isolated test process");
            assert!(output.status.success(), "isolated ownership test failed");
            return;
        }
        TEST_PANIC_AFTER_SPAWN.store(true, Ordering::Release);
        let trapped = std::panic::catch_unwind(|| {
            let _ = run_group(
                Path::new("/bin/sleep"),
                &["10".into()],
                Duration::from_secs(2),
                &AtomicBool::new(false),
                1024,
            );
        });
        assert!(trapped.is_err());
        assert_eq!(quarantine_count(), 1);
        assert!(matches!(
            run_group(
                Path::new("/bin/sleep"),
                &["1".into()],
                Duration::from_secs(2),
                &AtomicBool::new(false),
                1024
            ),
            Err(RunError::Quarantined)
        ));
        // Dropping a separate CleanupUnconfirmed receipt must not close or
        // release its corresponding OS owner from the global registry.
        let p = test_child();
        let _ = group_signal(p.pgid, 9);
        {
            let receipt = quarantine(p);
            assert!(matches!(receipt, RunError::CleanupUnconfirmed(_)));
            assert_eq!(quarantine_count(), 2);
        }
        assert_eq!(quarantine_count(), 2);
    }
}
