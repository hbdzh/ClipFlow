//! AI staging transaction building block; not yet a product job or capability.
//!
//! macOS only, same-volume directory publication in a caller-controlled parent.
//! The caller must keep exclusive ownership of the parent/staging namespace,
//! stop and reap the worker before calling this API, and separately validate
//! audio. Same-UID concurrent modification and crash recovery are not solved.
#![deny(unsafe_op_in_unsafe_fn)]

#[cfg(target_os = "macos")]
pub mod runner;

#[cfg(target_os = "macos")]
use std::os::fd::{AsRawFd, FromRawFd};
#[cfg(unix)]
use std::os::unix::fs::MetadataExt;
use std::{
    ffi::{CString, OsStr},
    fs::{self, File},
    io::{self, Read},
    path::{Component, Path, PathBuf},
    sync::{
        Mutex, OnceLock,
        atomic::{AtomicBool, Ordering},
    },
};

static TRANSACTION_QUARANTINED: AtomicBool = AtomicBool::new(false);
static RETAINED_STAGES: OnceLock<Mutex<Vec<RetainedStage>>> = OnceLock::new();
static TRANSACTION_GATE: Mutex<()> = Mutex::new(());

struct RetainedStage {
    _parent_handle: File,
    _stage_handle: Option<File>,
    _stage: PathBuf,
}

fn retain_stage(parent_handle: File, stage_handle: Option<File>, stage: PathBuf) {
    // A failed lock still leaves the process quarantined; other operations
    // fail closed on a poisoned gate rather than publishing.
    let _gate = TRANSACTION_GATE.lock();
    retain_stage_locked(parent_handle, stage_handle, stage);
}

fn retain_stage_locked(parent_handle: File, stage_handle: Option<File>, stage: PathBuf) {
    TRANSACTION_QUARANTINED.store(true, Ordering::Release);
    let record = RetainedStage {
        _parent_handle: parent_handle,
        _stage_handle: stage_handle,
        _stage: stage,
    };
    match RETAINED_STAGES
        .get_or_init(|| Mutex::new(Vec::new()))
        .lock()
    {
        Ok(mut stages) => stages.push(record),
        // A poisoned registry must not silently release the owner.
        Err(_) => std::mem::forget(record),
    }
}

pub fn transaction_quarantined() -> bool {
    TRANSACTION_QUARANTINED.load(Ordering::Acquire)
}

/// Effective UID of this process on macOS, used for ownership checks.
#[cfg(target_os = "macos")]
pub fn effective_uid() -> u32 {
    // SAFETY: geteuid takes no arguments, returns a uid_t by value, and does
    // not access caller memory or retain references.
    unsafe { geteuid() }
}

fn transaction_gate() -> Result<std::sync::MutexGuard<'static, ()>, TransactionError> {
    TRANSACTION_GATE.lock().map_err(|_| {
        TRANSACTION_QUARANTINED.store(true, Ordering::Release);
        TransactionError::CleanupUnconfirmed
    })
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TransactionError {
    InvalidArgument,
    UnsupportedPlatform,
    ParentChanged,
    InvalidStaging,
    OutputExists,
    CleanupUnconfirmed,
    Io,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CommitOutcome {
    Committed,
    /// Publication happened; callers must never retry or report cancellation.
    CommittedCleanupUnconfirmed,
}

pub struct AtomicDirectory {
    parent: PathBuf,
    parent_handle: Option<File>,
    stage_handle: Option<File>,
    ready_handle: Option<File>,
    stage: PathBuf,
    ready: PathBuf,
    stage_name: String,
    destination: String,
    stage_identity: (u64, u64),
    ready_identity: (u64, u64),
    active: bool,
    cleanup_failed: bool,
}

#[cfg(unix)]
fn identity(meta: &fs::Metadata) -> (u64, u64) {
    (meta.dev(), meta.ino())
}
#[cfg(not(unix))]
fn identity(_: &fs::Metadata) -> (u64, u64) {
    (0, 0)
}

fn valid_leaf(name: &str) -> bool {
    if name.is_empty() || name.as_bytes().contains(&0) {
        return false;
    }
    let mut parts = Path::new(name).components();
    matches!(parts.next(), Some(Component::Normal(n)) if n == OsStr::new(name))
        && parts.next().is_none()
}
fn random_stage() -> io::Result<String> {
    let mut bytes = [0u8; 16];
    File::open("/dev/urandom")?.read_exact(&mut bytes)?;
    Ok(format!(
        ".mediastudio-ai-{}",
        bytes.iter().map(|b| format!("{b:02x}")).collect::<String>()
    ))
}

impl AtomicDirectory {
    pub fn new(parent: &Path, destination: &str) -> Result<Self, TransactionError> {
        let _gate = transaction_gate()?;
        if transaction_quarantined() {
            return Err(TransactionError::CleanupUnconfirmed);
        }
        if !cfg!(target_os = "macos") {
            return Err(TransactionError::UnsupportedPlatform);
        }
        if !parent.is_absolute() || !valid_leaf(destination) {
            return Err(TransactionError::InvalidArgument);
        }
        let parent = fs::canonicalize(parent).map_err(|_| TransactionError::Io)?;
        let parent_handle = File::open(&parent).map_err(|_| TransactionError::Io)?;
        if !parent_handle
            .metadata()
            .map_err(|_| TransactionError::Io)?
            .is_dir()
        {
            return Err(TransactionError::InvalidArgument);
        }
        for _ in 0..16 {
            let name = random_stage().map_err(|_| TransactionError::Io)?;
            let stage = parent.join(&name);
            #[cfg(target_os = "macos")]
            let create_result = {
                let leaf =
                    CString::new(name.as_bytes()).map_err(|_| TransactionError::InvalidArgument)?;
                // SAFETY: parent is an open owned directory fd and leaf is a
                // live NUL-terminated single component; no pointer escapes.
                let rc = unsafe { mkdirat(parent_handle.as_raw_fd(), leaf.as_ptr(), 0o700) };
                if rc == 0 {
                    Ok(())
                } else {
                    Err(io::Error::last_os_error())
                }
            };
            #[cfg(not(target_os = "macos"))]
            let create_result = Err(io::Error::other("unsupported platform"));
            match create_result {
                Ok(()) => {
                    #[cfg(target_os = "macos")]
                    let stage_fd = {
                        let leaf = CString::new(name.as_bytes())
                            .map_err(|_| TransactionError::CleanupUnconfirmed)?;
                        // SAFETY: the parent fd and leaf are live. Flags are
                        // O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC from macOS SDK.
                        unsafe {
                            openat(
                                parent_handle.as_raw_fd(),
                                leaf.as_ptr(),
                                0x00100100 | 0x01000000,
                            )
                        }
                    };
                    #[cfg(not(target_os = "macos"))]
                    let stage_fd = -1;
                    if stage_fd < 0 {
                        retain_stage_locked(parent_handle, None, stage);
                        return Err(TransactionError::CleanupUnconfirmed);
                    }
                    #[cfg(target_os = "macos")]
                    // SAFETY: successful openat returned a new, uniquely owned fd.
                    let stage_handle = unsafe { File::from_raw_fd(stage_fd) };
                    #[cfg(not(target_os = "macos"))]
                    let stage_handle = unreachable!();
                    let m = match stage_handle.metadata() {
                        Ok(m) => m,
                        Err(_) => {
                            retain_stage_locked(parent_handle, Some(stage_handle), stage);
                            return Err(TransactionError::CleanupUnconfirmed);
                        }
                    };
                    if !m.is_dir() || m.file_type().is_symlink() {
                        retain_stage_locked(parent_handle, Some(stage_handle), stage);
                        return Err(TransactionError::CleanupUnconfirmed);
                    }
                    #[cfg(target_os = "macos")]
                    let ready_handle = {
                        let leaf = c"ready";
                        // SAFETY: stage_handle is a live owned directory fd and
                        // leaf is a static NUL-terminated single component.
                        let rc = unsafe { mkdirat(stage_handle.as_raw_fd(), leaf.as_ptr(), 0o700) };
                        if rc != 0 {
                            retain_stage_locked(parent_handle, Some(stage_handle), stage);
                            return Err(TransactionError::CleanupUnconfirmed);
                        }
                        // SAFETY: directory and C string stay valid; flags come
                        // from the macOS SDK and forbid following the final link.
                        let fd =
                            unsafe { openat(stage_handle.as_raw_fd(), leaf.as_ptr(), 0x01100100) };
                        if fd < 0 {
                            retain_stage_locked(parent_handle, Some(stage_handle), stage);
                            return Err(TransactionError::CleanupUnconfirmed);
                        }
                        // SAFETY: openat returned a newly owned descriptor.
                        unsafe { File::from_raw_fd(fd) }
                    };
                    #[cfg(not(target_os = "macos"))]
                    let ready_handle: File = unreachable!();
                    let ready_meta = match ready_handle.metadata() {
                        Ok(meta) if meta.is_dir() => meta,
                        _ => {
                            retain_stage_locked(parent_handle, Some(stage_handle), stage);
                            return Err(TransactionError::CleanupUnconfirmed);
                        }
                    };
                    let ready = stage.join("ready");
                    return Ok(Self {
                        parent,
                        parent_handle: Some(parent_handle),
                        stage_handle: Some(stage_handle),
                        ready_handle: Some(ready_handle),
                        stage,
                        ready,
                        stage_name: name,
                        destination: destination.to_string(),
                        stage_identity: identity(&m),
                        ready_identity: identity(&ready_meta),
                        active: true,
                        cleanup_failed: false,
                    });
                }
                Err(e) if e.kind() == io::ErrorKind::AlreadyExists => continue,
                Err(_) => return Err(TransactionError::Io),
            }
        }
        Err(TransactionError::Io)
    }
    pub fn staging(&self) -> &Path {
        &self.ready
    }
    /// Duplicate the directory handle held since transaction creation so
    /// callers can bind auxiliary-file cleanup to the same outer stage.
    pub fn clone_stage_handle(&self) -> Result<File, TransactionError> {
        self.stage_handle
            .as_ref()
            .ok_or(TransactionError::InvalidStaging)?
            .try_clone()
            .map_err(|_| TransactionError::Io)
    }
    fn verify_identity(&self) -> Result<(), TransactionError> {
        let parent = fs::metadata(&self.parent).map_err(|_| TransactionError::ParentChanged)?;
        let held = self
            .parent_handle
            .as_ref()
            .ok_or(TransactionError::ParentChanged)?
            .metadata()
            .map_err(|_| TransactionError::ParentChanged)?;
        if identity(&parent) != identity(&held) {
            return Err(TransactionError::ParentChanged);
        }
        let stage =
            fs::symlink_metadata(&self.stage).map_err(|_| TransactionError::InvalidStaging)?;
        let held_stage = self
            .stage_handle
            .as_ref()
            .ok_or(TransactionError::InvalidStaging)?
            .metadata()
            .map_err(|_| TransactionError::InvalidStaging)?;
        if !stage.is_dir()
            || stage.file_type().is_symlink()
            || identity(&stage) != self.stage_identity
            || identity(&held_stage) != self.stage_identity
        {
            return Err(TransactionError::InvalidStaging);
        }
        let ready =
            fs::symlink_metadata(&self.ready).map_err(|_| TransactionError::InvalidStaging)?;
        let held_ready = self
            .ready_handle
            .as_ref()
            .ok_or(TransactionError::InvalidStaging)?
            .metadata()
            .map_err(|_| TransactionError::InvalidStaging)?;
        if !ready.is_dir()
            || ready.file_type().is_symlink()
            || identity(&ready) != self.ready_identity
            || identity(&held_ready) != self.ready_identity
        {
            return Err(TransactionError::InvalidStaging);
        }
        Ok(())
    }
    fn names(&self, cleanup: bool) -> Result<Vec<PathBuf>, TransactionError> {
        self.verify_identity()?;
        let mut paths = Vec::new();
        // std::fs lacks an fd-relative directory iterator. This path-based
        // scan is only valid under the documented trusted-namespace boundary.
        let scan_root = self.ready.clone();
        for item in fs::read_dir(&scan_root).map_err(|_| TransactionError::InvalidStaging)? {
            let item = item.map_err(|_| TransactionError::InvalidStaging)?;
            let name = item.file_name();
            let allowed = if cleanup {
                [
                    "vocals.wav",
                    "accompaniment.wav",
                    "encoded.f32",
                    "layer0.f32",
                    "layer1.f32",
                    "layer2.f32",
                    "input.wav",
                ]
                .iter()
                .any(|n| name == OsStr::new(n))
            } else {
                name == "vocals.wav" || name == "accompaniment.wav"
            };
            let meta =
                fs::symlink_metadata(item.path()).map_err(|_| TransactionError::InvalidStaging)?;
            #[cfg(unix)]
            let singly_linked = meta.nlink() == 1;
            #[cfg(not(unix))]
            let singly_linked = false;
            if !allowed
                || !meta.is_file()
                || meta.file_type().is_symlink()
                || !singly_linked
                || (!cleanup && meta.len() == 0)
            {
                return Err(TransactionError::InvalidStaging);
            }
            paths.push(item.path());
        }
        if !cleanup
            && (paths.len() != 2
                || !scan_root.join("vocals.wav").is_file()
                || !scan_root.join("accompaniment.wav").is_file())
        {
            return Err(TransactionError::InvalidStaging);
        }
        Ok(paths)
    }
    /// Atomically exposes one directory name. Caller serializes cancellation
    /// against this call and verifies both WAVs before reaching this point.
    pub fn commit(&mut self) -> Result<CommitOutcome, TransactionError> {
        let _gate = transaction_gate()?;
        if transaction_quarantined() {
            return Err(TransactionError::CleanupUnconfirmed);
        }
        if !self.active {
            return Err(TransactionError::InvalidArgument);
        }
        self.names(false)?;
        self.verify_identity()?;
        #[cfg(target_os = "macos")]
        {
            let from = c"ready";
            let to = CString::new(self.destination.as_bytes())
                .map_err(|_| TransactionError::InvalidArgument)?;
            let outer = CString::new(self.stage_name.as_bytes())
                .map_err(|_| TransactionError::InvalidArgument)?;
            let parent_fd = self
                .parent_handle
                .as_ref()
                .ok_or(TransactionError::ParentChanged)?
                .as_raw_fd();
            let stage_fd = self
                .stage_handle
                .as_ref()
                .ok_or(TransactionError::ParentChanged)?
                .as_raw_fd();
            // SAFETY: both CStrings are valid NUL-terminated leaf names, and the
            // owned parent fd remains open throughout this synchronous call.
            // EXCL=4 is from macOS sys/stdio.h. No pointer escapes the call.
            let rc = unsafe { renameatx_np(stage_fd, from.as_ptr(), parent_fd, to.as_ptr(), 4) };
            if rc != 0 {
                let e = io::Error::last_os_error();
                return Err(if e.raw_os_error() == Some(17) {
                    TransactionError::OutputExists
                } else {
                    TransactionError::Io
                });
            }
            // The result is already visible. Never report ordinary failure if
            // removing the now-empty outer stage fails.
            let outer_ok = fs::symlink_metadata(&self.stage).is_ok_and(|m| {
                m.is_dir() && !m.file_type().is_symlink() && identity(&m) == self.stage_identity
            });
            // SAFETY: held parent fd and leaf are live; AT_REMOVEDIR from SDK.
            let removed = outer_ok && unsafe { unlinkat(parent_fd, outer.as_ptr(), 0x80) } == 0;
            self.active = false;
            if removed {
                Ok(CommitOutcome::Committed)
            } else {
                if let Some(parent) = self.parent_handle.take() {
                    retain_stage_locked(parent, self.stage_handle.take(), self.stage.clone());
                }
                Ok(CommitOutcome::CommittedCleanupUnconfirmed)
            }
        }
        #[cfg(not(target_os = "macos"))]
        {
            Err(TransactionError::UnsupportedPlatform)
        }
    }
    /// Only use after the worker has been reaped. Unknown files or identity
    /// changes are left untouched and reported; caller must quarantine them.
    pub fn cleanup(&mut self) -> Result<(), TransactionError> {
        let _gate = transaction_gate()?;
        if self.cleanup_failed {
            return Err(TransactionError::CleanupUnconfirmed);
        }
        let result = self.cleanup_inner();
        if result.is_err() {
            self.cleanup_failed = true;
            TRANSACTION_QUARANTINED.store(true, Ordering::Release);
        }
        result
    }

    fn cleanup_inner(&mut self) -> Result<(), TransactionError> {
        if !self.active {
            return Ok(());
        }
        let paths = self
            .names(true)
            .map_err(|_| TransactionError::CleanupUnconfirmed)?;
        self.verify_identity()
            .map_err(|_| TransactionError::CleanupUnconfirmed)?;
        for path in paths {
            #[cfg(target_os = "macos")]
            {
                use std::os::unix::ffi::OsStrExt;
                let name = path
                    .file_name()
                    .ok_or(TransactionError::CleanupUnconfirmed)?;
                let name = CString::new(name.as_bytes())
                    .map_err(|_| TransactionError::CleanupUnconfirmed)?;
                // SAFETY: stage fd is owned and live; name is a single live
                // NUL-terminated leaf. unlinkat does not follow the leaf link.
                let rc = unsafe {
                    unlinkat(
                        self.ready_handle
                            .as_ref()
                            .ok_or(TransactionError::CleanupUnconfirmed)?
                            .as_raw_fd(),
                        name.as_ptr(),
                        0,
                    )
                };
                if rc != 0 {
                    return Err(TransactionError::CleanupUnconfirmed);
                }
            }
            #[cfg(not(target_os = "macos"))]
            {
                let _ = path;
                return Err(TransactionError::CleanupUnconfirmed);
            }
        }
        #[cfg(target_os = "macos")]
        {
            // SAFETY: the stage fd is live and the static leaf is NUL-terminated.
            let rc = unsafe {
                unlinkat(
                    self.stage_handle
                        .as_ref()
                        .ok_or(TransactionError::CleanupUnconfirmed)?
                        .as_raw_fd(),
                    c"ready".as_ptr(),
                    0x80,
                )
            };
            if rc != 0 {
                return Err(TransactionError::CleanupUnconfirmed);
            }
            let leaf = CString::new(self.stage_name.as_bytes())
                .map_err(|_| TransactionError::CleanupUnconfirmed)?;
            // SAFETY: the parent directory fd and NUL-terminated leaf are
            // live for the call; AT_REMOVEDIR=0x80 from the macOS SDK.
            let rc = unsafe {
                unlinkat(
                    self.parent_handle
                        .as_ref()
                        .ok_or(TransactionError::CleanupUnconfirmed)?
                        .as_raw_fd(),
                    leaf.as_ptr(),
                    0x80,
                )
            };
            if rc != 0 {
                return Err(TransactionError::CleanupUnconfirmed);
            }
        }
        #[cfg(not(target_os = "macos"))]
        return Err(TransactionError::CleanupUnconfirmed);
        self.active = false;
        Ok(())
    }
}

impl Drop for AtomicDirectory {
    fn drop(&mut self) {
        if self.active
            && let Some(parent) = self.parent_handle.take()
        {
            // Never unlink unknown content in Drop; keep its owner and stop
            // subsequent transactions until the process is restarted.
            retain_stage(parent, self.stage_handle.take(), self.stage.clone());
        }
    }
}

#[cfg(target_os = "macos")]
unsafe extern "C" {
    fn geteuid() -> std::ffi::c_uint;
    fn mkdirat(
        dirfd: std::ffi::c_int,
        name: *const std::ffi::c_char,
        mode: std::ffi::c_uint,
    ) -> std::ffi::c_int;
    fn openat(
        dirfd: std::ffi::c_int,
        name: *const std::ffi::c_char,
        flags: std::ffi::c_int,
        ...
    ) -> std::ffi::c_int;
    fn unlinkat(
        dirfd: std::ffi::c_int,
        name: *const std::ffi::c_char,
        flags: std::ffi::c_int,
    ) -> std::ffi::c_int;
    fn renameatx_np(
        fromfd: std::ffi::c_int,
        from: *const std::ffi::c_char,
        tofd: std::ffi::c_int,
        to: *const std::ffi::c_char,
        flags: std::ffi::c_uint,
    ) -> std::ffi::c_int;
}
