use media_domain::{BackendConfig, MediaError};
use std::ffi::OsString;
use std::io::Read;
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;
use std::time::{Duration, Instant};

pub(crate) struct Context<'a> {
    pub config: &'a BackendConfig,
    pub cancel: &'a AtomicBool,
    deadline: Instant,
    pub media_file_budget: Option<u64>,
}

impl<'a> Context<'a> {
    pub fn new(config: &'a BackendConfig, cancel: &'a AtomicBool) -> Self {
        Self::with_timeout(config, cancel, config.timeout_ms)
    }

    pub fn with_timeout(
        config: &'a BackendConfig,
        cancel: &'a AtomicBool,
        timeout_ms: u64,
    ) -> Self {
        Self::with_budget(config, cancel, Duration::from_millis(timeout_ms))
    }
    pub fn with_budget(
        config: &'a BackendConfig,
        cancel: &'a AtomicBool,
        budget: Duration,
    ) -> Self {
        Self {
            config,
            cancel,
            media_file_budget: None,
            deadline: Instant::now() + budget,
        }
    }

    pub fn check(&self) -> Result<(), MediaError> {
        if self.cancel.load(Ordering::Acquire) {
            return Err(MediaError::new(
                "CANCELLED",
                "Media operation was cancelled.",
            ));
        }
        if Instant::now() >= self.deadline {
            return Err(MediaError::new(
                "TIMEOUT",
                "Media operation exceeded its deadline.",
            ));
        }
        Ok(())
    }

    pub fn run(&self, program: &Path, args: &[OsString]) -> Result<Vec<u8>, MediaError> {
        self.run_checked(program, args, None)
    }
    pub fn run_media(
        &self,
        program: &Path,
        args: &[OsString],
        path: &Path,
        budget: u64,
    ) -> Result<Vec<u8>, MediaError> {
        self.run_checked(program, args, Some((path, budget)))
    }
    fn run_checked(
        &self,
        program: &Path,
        args: &[OsString],
        file: Option<(&Path, u64)>,
    ) -> Result<Vec<u8>, MediaError> {
        self.check()?;
        let child = Command::new(program)
            .args(args)
            .env("LC_ALL", "C")
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|error| {
                if error.kind() == std::io::ErrorKind::PermissionDenied {
                    MediaError::new("PERMISSION_DENIED", "Media tool cannot be executed.")
                } else {
                    MediaError::new("DEPENDENCY_MISSING", "Media tool is not available.")
                }
            })?;
        let mut process = Process {
            child,
            reaped: false,
            out: None,
            err: None,
        };
        let stdout = process.child.stdout.take();
        let stderr = process.child.stderr.take();
        let (Some(stdout), Some(stderr)) = (stdout, stderr) else {
            process.stop()?;
            return Err(MediaError::new(
                "PROCESS_FAILED",
                "Media tool pipes are unavailable.",
            ));
        };
        let exceeded = Arc::new(AtomicBool::new(false));
        let output_limit = self.config.max_output_bytes;
        let out_flag = exceeded.clone();
        let err_flag = exceeded.clone();
        // Drain even after a limit is reached: the controller kills/reaps the child;
        // neither pipe is allowed to deadlock while the other produces diagnostics.
        let output_reader = thread::Builder::new()
            .name("media-stdout".into())
            .spawn(move || capture(stdout, output_limit, out_flag));
        match output_reader {
            Ok(reader) => process.out = Some(reader),
            Err(_) => {
                process.stop()?;
                return Err(MediaError::new(
                    "PROCESS_FAILED",
                    "Media output reader cannot be created.",
                ));
            }
        }
        let diagnostic_reader = thread::Builder::new()
            .name("media-stderr".into())
            .spawn(move || capture(stderr, output_limit, err_flag));
        match diagnostic_reader {
            Ok(reader) => process.err = Some(reader),
            Err(_) => {
                let stopped = process.stop();
                // Even a failed reap must not skip joining an existing reader.
                if let Some(reader) = process.out.take() {
                    let _ = reader.join();
                }
                stopped?;
                return Err(MediaError::new(
                    "PROCESS_FAILED",
                    "Media diagnostic reader cannot be created.",
                ));
            }
        }
        let status = loop {
            if let Err(error) = self.check() {
                break Err(error);
            }
            if file.is_some_and(|(p, n)| std::fs::metadata(p).is_ok_and(|m| m.len() > n)) {
                break Err(MediaError::new(
                    "RESOURCE_LIMIT",
                    "Encoded media exceeded its file budget.",
                ));
            }
            if exceeded.load(Ordering::Acquire) {
                break Err(MediaError::new(
                    "RESOURCE_LIMIT",
                    "Media tool output exceeded its configured bound.",
                ));
            }
            match process.child.try_wait() {
                Ok(Some(status)) => {
                    process.reaped = true;
                    break Ok(status);
                }
                Ok(None) => thread::sleep(Duration::from_millis(5)),
                Err(_) => {
                    break Err(MediaError::new(
                        "PROCESS_FAILED",
                        "Media tool status cannot be read.",
                    ));
                }
            }
        };
        let stopped = if status.is_err() {
            process.stop()
        } else {
            Ok(())
        };
        // Always join both readers before propagating any one error.
        let output = process.out.take().map(|reader| reader.join());
        let diagnostic = process.err.take().map(|reader| reader.join());
        stopped?;
        let output = output
            .ok_or_else(|| {
                MediaError::new("PROCESS_FAILED", "Media output reader is unavailable.")
            })?
            .map_err(|_| MediaError::new("PROCESS_FAILED", "Media output reader failed."))?
            .map_err(|_| MediaError::new("PROCESS_FAILED", "Media output cannot be read."))?;
        let diagnostic = diagnostic
            .ok_or_else(|| {
                MediaError::new("PROCESS_FAILED", "Media diagnostic reader is unavailable.")
            })?
            .map_err(|_| MediaError::new("PROCESS_FAILED", "Media diagnostic reader failed."))?
            .map_err(|_| MediaError::new("PROCESS_FAILED", "Media diagnostic cannot be read."))?;
        let status = status?;
        if file.is_some_and(|(p, n)| std::fs::metadata(p).is_ok_and(|m| m.len() > n)) {
            return Err(MediaError::new(
                "RESOURCE_LIMIT",
                "Encoded media exceeded its file budget.",
            ));
        }
        self.check()?;
        if exceeded.load(Ordering::Acquire) {
            return Err(MediaError::new(
                "RESOURCE_LIMIT",
                "Media tool output exceeded its configured bound.",
            ));
        }
        if !status.success() {
            // FFmpeg 7 propagates AVERROR(ENOSPC) as process status 228. Require
            // both the exact status and its diagnostic, not a path substring.
            if status.code() == Some(228)
                && std::str::from_utf8(&diagnostic)
                    .is_ok_and(|s| s.contains("No space left on device"))
            {
                return Err(MediaError::new(
                    "OUT_OF_DISK",
                    "Media output storage is full.",
                ));
            }
            // Child diagnostics can include private source paths; never echo them
            // into public job errors. Tests independently preserve command evidence.
            return Err(MediaError::new(
                "PROCESS_FAILED",
                "Media tool returned an unsuccessful status.",
            ));
        }
        Ok(output)
    }
}

// Own the child and both drains as one unit. Any early return or unwinding panic
// first kills/reaps the direct child, then joins every reader already created.
struct Process {
    child: Child,
    reaped: bool,
    out: Option<thread::JoinHandle<std::io::Result<Vec<u8>>>>,
    err: Option<thread::JoinHandle<std::io::Result<Vec<u8>>>>,
}
impl Process {
    fn stop(&mut self) -> Result<(), MediaError> {
        if self.reaped {
            return Ok(());
        }
        let _ = self.child.kill();
        self.child.wait().map_err(|_| {
            MediaError::new("CLEANUP_FAILED", "Media child exit could not be confirmed.")
        })?;
        self.reaped = true;
        Ok(())
    }
}
impl Drop for Process {
    fn drop(&mut self) {
        let _ = self.stop();
        if let Some(reader) = self.out.take() {
            let _ = reader.join();
        }
        if let Some(reader) = self.err.take() {
            let _ = reader.join();
        }
    }
}

fn capture(
    mut reader: impl Read,
    maximum: usize,
    exceeded: Arc<AtomicBool>,
) -> std::io::Result<Vec<u8>> {
    let mut saved = Vec::new();
    let mut chunk = [0_u8; 8192];
    loop {
        let len = reader.read(&mut chunk)?;
        if len == 0 {
            break;
        }
        let retain = len.min(maximum.saturating_sub(saved.len()));
        saved.extend_from_slice(&chunk[..retain]);
        if retain < len {
            exceeded.store(true, Ordering::Release);
        }
    }
    Ok(saved)
}

pub(crate) fn args(items: &[&str]) -> Vec<OsString> {
    items.iter().map(OsString::from).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn config(timeout_ms: u64, max_output_bytes: usize) -> BackendConfig {
        BackendConfig {
            ffmpeg: PathBuf::from("/bin/sleep"),
            ffprobe: PathBuf::from("/bin/sleep"),
            timeout_ms,
            accurate_reencode_timeout_ms: timeout_ms,
            core003_enabled: false,
            core004_enabled: false,
            transcode_timeout_ms: 600000,
            max_output_bytes,
        }
    }

    #[test]
    fn actual_child_deadline_is_bounded_and_reaped() {
        let config = config(20, 1024);
        let flag = AtomicBool::new(false);
        let context = Context::new(&config, &flag);
        let start = Instant::now();
        let error = context
            .run(Path::new("/bin/sleep"), &args(&["10"]))
            .expect_err("sleep must time out");
        assert_eq!(error.code, "TIMEOUT");
        assert!(start.elapsed() < Duration::from_secs(2));
    }

    #[test]
    fn encoded_file_budget_is_checked_even_when_child_exits_immediately() {
        let path =
            std::env::temp_dir().join(format!("mediastudio-budget-{}.bin", std::process::id()));
        let config = config(2000, 1024);
        let flag = AtomicBool::new(false);
        let context = Context::new(&config, &flag);
        let command = args(&[
            "if=/dev/zero",
            &format!("of={}", path.display()),
            "bs=1024",
            "count=1",
        ]);
        let result = context.run_media(Path::new("/bin/dd"), &command, &path, 512);
        let _ = std::fs::remove_file(path);
        assert_eq!(result.expect_err("file size budget").code, "RESOURCE_LIMIT");
    }
    #[test]
    fn endless_output_is_killed_instead_of_filling_pipes() {
        let config = config(2000, 1024);
        let flag = AtomicBool::new(false);
        let context = Context::new(&config, &flag);
        let error = context
            .run(Path::new("/usr/bin/yes"), &[])
            .expect_err("output must be bounded");
        assert_eq!(error.code, "RESOURCE_LIMIT");
    }
}
