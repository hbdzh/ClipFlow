//! Explicitly limited macOS AI development backend; not a released capability.
//! The configured root, Python environment and all ancestors are trusted against
//! hostile same-UID modifications. This module is not an OS sandbox.
use media_ai::{
    AtomicDirectory, CommitOutcome, TransactionError,
    runner::{RunError, run_group},
};
use media_domain::{AiBackendConfig, BackendConfig, MediaError};
use serde_json::{Value, json};
use std::{
    ffi::OsString,
    fs::{self, File, OpenOptions},
    io::{Read, Seek, SeekFrom},
    os::unix::fs::{MetadataExt, OpenOptionsExt},
    path::{Component, Path, PathBuf},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    time::{Duration, Instant},
};

const WORKER_SHA: &str = "3277106ef4b6bcb583ad7b08a0aab2dcf8fb687c9de74539715592efa4657e0d";
const MODEL_SHA: &str = "f27742bb52b24cb039614dc76d0764e279df3eb2a9d6daa82c6daee3369ec747";
const MIN_FRAMES: u64 = 2049;
const MAX_FRAMES: u64 = 26_460_000;
const FLOOR: u64 = 64 * 1024 * 1024;
const FORMAT_MAX_FRAMES: u64 = 48_000 * 601;
static RESERVED: AtomicBool = AtomicBool::new(false);
static QUARANTINED: AtomicBool = AtomicBool::new(false);

fn err(code: &'static str, message: &'static str) -> MediaError {
    MediaError::new(code, message)
}
pub(crate) fn any_reserved() -> bool {
    RESERVED.load(Ordering::Acquire) || QUARANTINED.load(Ordering::Acquire)
}
struct Lease {
    poisoned: AtomicBool,
}
impl Lease {
    fn quarantine(&self) {
        self.poisoned.store(true, Ordering::Release);
        QUARANTINED.store(true, Ordering::Release);
    }
}
impl Drop for Lease {
    fn drop(&mut self) {
        if !self.poisoned.load(Ordering::Acquire) {
            RESERVED.store(false, Ordering::Release);
        }
    }
}

fn stable(a: &fs::Metadata, b: &fs::Metadata) -> bool {
    a.dev() == b.dev()
        && a.ino() == b.ino()
        && a.len() == b.len()
        && a.mtime() == b.mtime()
        && a.mtime_nsec() == b.mtime_nsec()
        && a.ctime() == b.ctime()
        && a.ctime_nsec() == b.ctime_nsec()
}
fn same_dir(a: &fs::Metadata, b: &fs::Metadata) -> bool {
    a.is_dir() && b.is_dir() && a.dev() == b.dev() && a.ino() == b.ino()
}
fn no_symlink_components(path: &Path) -> Result<(), MediaError> {
    if !path.is_absolute() {
        return Err(err(
            "INVALID_INPUT",
            "The development path must be absolute.",
        ));
    }
    let mut current = PathBuf::from("/");
    for item in path.components() {
        match item {
            Component::RootDir => {}
            Component::Normal(name) => {
                current.push(name);
                let m = fs::symlink_metadata(&current).map_err(|_| {
                    err("INVALID_INPUT", "The development directory is unavailable.")
                })?;
                if m.file_type().is_symlink() {
                    return Err(err(
                        "INVALID_INPUT",
                        "Symbolic links are not accepted in the development directory.",
                    ));
                }
            }
            _ => {
                return Err(err(
                    "INVALID_INPUT",
                    "The development directory is invalid.",
                ));
            }
        }
    }
    Ok(())
}
fn regular(path: &Path) -> Result<(File, fs::Metadata), MediaError> {
    let file = OpenOptions::new()
        .read(true)
        // O_NOFOLLOW | O_NONBLOCK: avoid waiting on an untrusted FIFO before
        // fstat rejects anything other than a regular file (macOS SDK fcntl.h).
        .custom_flags(0x100 | 0x4)
        .open(path)
        .map_err(|_| {
            err(
                "DEPENDENCY_MISSING",
                "A required development file is unavailable.",
            )
        })?;
    let m = file.metadata().map_err(|_| {
        err(
            "DEPENDENCY_MISSING",
            "A required development file is unavailable.",
        )
    })?;
    if !m.is_file() || m.len() == 0 || m.nlink() != 1 {
        return Err(err(
            "INVALID_INPUT",
            "The input must be a single-link regular file.",
        ));
    }
    Ok((file, m))
}
fn run_error(error: RunError) -> MediaError {
    match error {
        RunError::Cancelled => err("CANCELLED", "The AI operation was cancelled."),
        RunError::TimedOut => err("TIMEOUT", "The AI operation exceeded its deadline."),
        RunError::CleanupUnconfirmed(_) | RunError::Quarantined => {
            err("CLEANUP_FAILED", "AI process cleanup is unconfirmed.")
        }
        RunError::DependencyMissing => err(
            "DEPENDENCY_MISSING",
            "A required AI runtime is unavailable.",
        ),
        RunError::OutputLimit => err(
            "RESOURCE_LIMIT",
            "AI process diagnostics exceeded their limit.",
        ),
        _ => err("PROCESS_FAILED", "The AI worker failed."),
    }
}
fn sha256(path: &Path, timeout: u64) -> Result<String, MediaError> {
    let flag = AtomicBool::new(false);
    let out = run_group(
        Path::new("/usr/bin/shasum"),
        &["-a".into(), "256".into(), path.as_os_str().to_owned()],
        Duration::from_millis(timeout.min(30_000)),
        &flag,
        4096,
    )
    .map_err(run_error)?;
    let text = std::str::from_utf8(&out.stdout)
        .map_err(|_| err("PROCESS_FAILED", "A file hash could not be read."))?;
    let hash = text
        .split_whitespace()
        .next()
        .ok_or_else(|| err("PROCESS_FAILED", "A file hash could not be read."))?;
    if hash.len() != 64 || !hash.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err(err("PROCESS_FAILED", "A file hash could not be read."));
    }
    Ok(hash.to_ascii_lowercase())
}
fn capacity(root: &Path, needed: u64, timeout: u64) -> Result<(), MediaError> {
    let flag = AtomicBool::new(false);
    let out = run_group(
        Path::new("/bin/df"),
        &["-Pk".into(), root.as_os_str().to_owned()],
        Duration::from_millis(timeout.min(30_000)),
        &flag,
        8192,
    )
    .map_err(run_error)?;
    let text = std::str::from_utf8(&out.stdout)
        .map_err(|_| err("RESOURCE_LIMIT", "Storage capacity could not be checked."))?;
    let available = text
        .lines()
        .filter_map(|line| {
            let x = line.split_whitespace().collect::<Vec<_>>();
            if x.len() >= 6 && x[1].parse::<u64>().is_ok() && x[2].parse::<u64>().is_ok() {
                x[3].parse::<u64>().ok()
            } else {
                None
            }
        })
        .next_back()
        .and_then(|k| k.checked_mul(1024))
        .ok_or_else(|| err("RESOURCE_LIMIT", "Storage capacity could not be checked."))?;
    if available < needed.saturating_add(FLOOR) {
        return Err(err("OUT_OF_DISK", "Insufficient unreserved storage."));
    }
    Ok(())
}
fn le16(a: &[u8]) -> u16 {
    u16::from_le_bytes([a[0], a[1]])
}
fn le32(a: &[u8]) -> u32 {
    u32::from_le_bytes([a[0], a[1], a[2], a[3]])
}
/// Strict bounded 44.1 kHz stereo 32-bit IEEE float WAV reader; scans every
/// PCM sample rather than trusting a file header or worker self-report.
fn wav(
    path: &Path,
    peak_bounded: bool,
    cancel: Option<&AtomicBool>,
    deadline: Option<Instant>,
) -> Result<u64, MediaError> {
    let frames = wav_format(path, 44100, 2, MAX_FRAMES, peak_bounded, cancel, deadline)?;
    if frames < MIN_FRAMES {
        return Err(err(
            "WAV_DURATION_UNSUPPORTED",
            "The WAV duration is unsupported.",
        ));
    }
    Ok(frames)
}

fn wav_format(
    path: &Path,
    rate: u32,
    channels: u16,
    maximum: u64,
    peak_bounded: bool,
    cancel: Option<&AtomicBool>,
    deadline: Option<Instant>,
) -> Result<u64, MediaError> {
    let (mut file, meta) =
        regular(path).map_err(|_| err("INVALID_INPUT", "The WAV file cannot be read."))?;
    let len = meta.len();
    let block = u64::from(channels) * 4;
    // RIFF carries a 32-bit file size; inspect its small headers before
    // rejecting duration so an already-recognized long float WAV does not
    // fall through into the conversion path and consume a queue slot.
    if !(44..=u64::from(u32::MAX) + 8).contains(&len) {
        return Err(err("UNSUPPORTED_FORMAT", "The audio size is unsupported."));
    }
    let mut h = [0u8; 12];
    file.read_exact(&mut h)
        .map_err(|_| err("INVALID_INPUT", "The WAV header is invalid."))?;
    if &h[0..4] != b"RIFF" || &h[8..12] != b"WAVE" || u64::from(le32(&h[4..8])) + 8 != len {
        return Err(err(
            "UNSUPPORTED_FORMAT",
            "Only bounded float WAV is supported.",
        ));
    }
    let mut position = 12u64;
    let mut format = false;
    let mut data = None;
    let mut chunks = 0;
    while position < len {
        chunks += 1;
        if chunks > 256 || len - position < 8 {
            return Err(err("INVALID_INPUT", "The WAV layout is invalid."));
        }
        file.seek(SeekFrom::Start(position))
            .map_err(|_| err("INVALID_INPUT", "The WAV layout is invalid."))?;
        let mut ch = [0u8; 8];
        file.read_exact(&mut ch)
            .map_err(|_| err("INVALID_INPUT", "The WAV layout is invalid."))?;
        let size = u64::from(le32(&ch[4..8]));
        let start = position + 8;
        let end = start
            .checked_add(size)
            .ok_or_else(|| err("INVALID_INPUT", "The WAV layout is invalid."))?;
        let next = end
            .checked_add(size % 2)
            .ok_or_else(|| err("INVALID_INPUT", "The WAV layout is invalid."))?;
        if next > len {
            return Err(err("INVALID_INPUT", "The WAV file is truncated."));
        }
        if &ch[0..4] == b"fmt " {
            if format || !(16..=4096).contains(&size) {
                return Err(err("UNSUPPORTED_FORMAT", "The WAV format is unsupported."));
            }
            let mut f = vec![0u8; size as usize];
            file.read_exact(&mut f)
                .map_err(|_| err("INVALID_INPUT", "The WAV format is invalid."))?;
            let tag = le16(&f[0..2]);
            if tag == 0xfffe && f.len() >= 40 {
                let mask = le32(&f[20..24]);
                let layout_ok = match le16(&f[2..4]) {
                    1 => mask == 0 || mask == 4,
                    2 => mask == 0 || mask == 3,
                    _ => false,
                };
                if !layout_ok {
                    return Err(err(
                        "INVALID_INPUT",
                        "The WAV channel layout is unsupported.",
                    ));
                }
            }
            let extensible = tag == 0xfffe
                && f.len() >= 40
                && le16(&f[16..18]) >= 22
                && le16(&f[18..20]) == 32
                && f[24..40] == [3, 0, 0, 0, 0, 0, 16, 0, 128, 0, 0, 170, 0, 56, 155, 113];
            if !(tag == 3 || extensible)
                || le16(&f[2..4]) != channels
                || le32(&f[4..8]) != rate
                || le32(&f[8..12]) != rate * u32::from(channels) * 4
                || u64::from(le16(&f[12..14])) != block
                || le16(&f[14..16]) != 32
            {
                return Err(err(
                    "UNSUPPORTED_FORMAT",
                    "Only 44.1 kHz stereo float WAV is supported.",
                ));
            }
            format = true;
        } else if &ch[0..4] == b"data" {
            if data.is_some() || size % block != 0 {
                return Err(err("INVALID_INPUT", "The WAV audio layout is invalid."));
            }
            data = Some((start, size));
        }
        position = next;
    }
    let (start, size) =
        data.ok_or_else(|| err("INVALID_INPUT", "The WAV audio data is missing."))?;
    let frames = size / block;
    if !format {
        return Err(err(
            "UNSUPPORTED_FORMAT",
            "The WAV duration or format is unsupported.",
        ));
    }
    if frames == 0 || frames > maximum {
        return Err(err(
            "WAV_DURATION_UNSUPPORTED",
            "The WAV duration is unsupported.",
        ));
    }
    file.seek(SeekFrom::Start(start))
        .map_err(|_| err("INVALID_INPUT", "The WAV audio cannot be read."))?;
    let mut bytes = [0u8; 32768];
    let mut remain = size;
    while remain > 0 {
        if cancel.is_some_and(|c| c.load(Ordering::Acquire)) {
            return Err(err("CANCELLED", "The AI operation was cancelled."));
        }
        if deadline.is_some_and(|d| Instant::now() >= d) {
            return Err(err("TIMEOUT", "The AI operation exceeded its deadline."));
        }
        let n = usize::try_from(remain.min(bytes.len() as u64))
            .map_err(|_| err("INTERNAL", "The audio size is invalid."))?;
        file.read_exact(&mut bytes[..n])
            .map_err(|_| err("INVALID_INPUT", "The WAV audio is truncated."))?;
        for sample in bytes[..n].as_chunks::<4>().0 {
            let v = f32::from_le_bytes([sample[0], sample[1], sample[2], sample[3]]);
            if !v.is_finite() || (peak_bounded && v.abs() > 2.0) {
                return Err(err(
                    "INVALID_INPUT",
                    "The WAV contains unsupported samples.",
                ));
            }
        }
        remain -= n as u64;
    }
    if !stable(
        &meta,
        &file
            .metadata()
            .map_err(|_| err("SOURCE_CHANGED", "Audio identity changed."))?,
    ) {
        return Err(err("SOURCE_CHANGED", "Audio identity changed."));
    }
    Ok(frames)
}

struct Snapshot {
    root: PathBuf,
    root_handle: File,
    root_identity: fs::Metadata,
    source: PathBuf,
    source_handle: File,
    source_identity: fs::Metadata,
    python_identity: fs::Metadata,
    worker_identity: fs::Metadata,
    model_identity: fs::Metadata,
    config: AiBackendConfig,
    frames: u64,
    conversion: Option<Conversion>,
}

#[derive(Clone)]
struct Conversion {
    ffmpeg: PathBuf,
    ffprobe: PathBuf,
    ffmpeg_identity: fs::Metadata,
    ffprobe_identity: fs::Metadata,
    sample_rate: u32,
    channels: u16,
}

fn inspect_conversion(
    config: &BackendConfig,
    source: &Path,
    deadline: Instant,
) -> Result<Conversion, MediaError> {
    let tool_meta = |path: &Path| -> Result<fs::Metadata, MediaError> {
        let m = fs::metadata(path).map_err(|_| {
            err(
                "DEPENDENCY_MISSING",
                "A required media tool is unavailable.",
            )
        })?;
        if !m.is_file() {
            return Err(err(
                "DEPENDENCY_MISSING",
                "A required media tool is unavailable.",
            ));
        }
        Ok(m)
    };
    let ffmpeg_identity = tool_meta(&config.ffmpeg)?;
    let ffprobe_identity = tool_meta(&config.ffprobe)?;
    let args: Vec<OsString> = [
        "-v",
        "error",
        "-protocol_whitelist",
        "file",
        "-format_whitelist",
        "wav,flac,mp3,mov",
        "-show_entries",
        "stream=codec_type,codec_name,profile,sample_rate,channels,channel_layout:format=format_name",
        "-of",
        "json",
    ]
    .into_iter()
    .map(OsString::from)
    .chain(std::iter::once(source.as_os_str().to_owned()))
    .collect();
    let flag = AtomicBool::new(false);
    let output = run_group(
        &config.ffprobe,
        &args,
        deadline.saturating_duration_since(Instant::now()),
        &flag,
        16_384,
    )
    .map_err(|e| match e {
        RunError::CleanupUnconfirmed(_) | RunError::Quarantined => run_error(e),
        RunError::TimedOut => run_error(e),
        _ => err(
            "UNSUPPORTED_FORMAT",
            "The input format cannot be inspected.",
        ),
    })?;
    let value: Value = serde_json::from_slice(&output.stdout)
        .map_err(|_| err("UNSUPPORTED_FORMAT", "The input metadata is invalid."))?;
    let streams = value["streams"]
        .as_array()
        .ok_or_else(|| err("UNSUPPORTED_FORMAT", "The audio stream is unavailable."))?;
    if streams.len() != 1 || streams[0]["codec_type"].as_str() != Some("audio") {
        return Err(err(
            "UNSUPPORTED_FORMAT",
            "Exactly one audio stream is required.",
        ));
    }
    let stream = &streams[0];
    let codec = stream["codec_name"].as_str().unwrap_or("");
    let format = value["format"]["format_name"].as_str().unwrap_or("");
    let allowed = (format == "wav" && matches!(codec, "pcm_s16le" | "pcm_s24le" | "pcm_f32le"))
        || (format == "flac" && codec == "flac")
        || (format == "mp3" && codec == "mp3")
        || (format.split(',').any(|x| x == "m4a")
            && codec == "aac"
            && stream["profile"].as_str() == Some("LC"));
    let sample_rate = stream["sample_rate"]
        .as_str()
        .and_then(|s| s.parse::<u32>().ok())
        .unwrap_or(0);
    let channels = stream["channels"]
        .as_u64()
        .and_then(|n| u16::try_from(n).ok())
        .unwrap_or(0);
    let layout = stream["channel_layout"].as_str();
    let valid_layout = matches!(
        (channels, layout),
        (1, None | Some("mono")) | (2, None | Some("stereo"))
    );
    if !allowed || !matches!(sample_rate, 44100 | 48000) || !valid_layout {
        return Err(err(
            "UNSUPPORTED_FORMAT",
            "The audio encoding, rate or channels are unsupported.",
        ));
    }
    Ok(Conversion {
        ffmpeg: config.ffmpeg.clone(),
        ffprobe: config.ffprobe.clone(),
        ffmpeg_identity,
        ffprobe_identity,
        sample_rate,
        channels,
    })
}
impl Snapshot {
    fn verify(&self) -> Result<(), MediaError> {
        let now = fs::symlink_metadata(&self.root)
            .map_err(|_| err("SOURCE_CHANGED", "Development directory changed."))?;
        let held = self
            .root_handle
            .metadata()
            .map_err(|_| err("SOURCE_CHANGED", "Development directory changed."))?;
        if now.file_type().is_symlink()
            || !same_dir(&self.root_identity, &now)
            || !same_dir(&self.root_identity, &held)
        {
            return Err(err("SOURCE_CHANGED", "Development directory changed."));
        }
        let path = fs::symlink_metadata(&self.source)
            .map_err(|_| err("SOURCE_CHANGED", "Input changed during processing."))?;
        let held = self
            .source_handle
            .metadata()
            .map_err(|_| err("SOURCE_CHANGED", "Input changed during processing."))?;
        if path.file_type().is_symlink()
            || !stable(&self.source_identity, &path)
            || !stable(&self.source_identity, &held)
        {
            return Err(err("SOURCE_CHANGED", "Input changed during processing."));
        }
        for (path, old) in [
            (&self.config.python, &self.python_identity),
            (&self.config.worker, &self.worker_identity),
            (&self.config.model, &self.model_identity),
        ] {
            let m = fs::metadata(path)
                .map_err(|_| err("SOURCE_CHANGED", "A required AI component changed."))?;
            if !stable(old, &m) {
                return Err(err("SOURCE_CHANGED", "A required AI component changed."));
            }
        }
        if let Some(c) = &self.conversion {
            for (path, old) in [
                (&c.ffmpeg, &c.ffmpeg_identity),
                (&c.ffprobe, &c.ffprobe_identity),
            ] {
                let now = fs::metadata(path)
                    .map_err(|_| err("SOURCE_CHANGED", "A required media tool changed."))?;
                if !stable(old, &now) {
                    return Err(err("SOURCE_CHANGED", "A required media tool changed."));
                }
            }
        }
        Ok(())
    }
}
fn quarantine(snapshot: &Arc<Snapshot>, lease: &Arc<Lease>) {
    lease.quarantine();
    // Unconfirmed processes may still use the private namespace and inputs.
    // Retain both descriptors and the reservation for the process lifetime.
    std::mem::forget(snapshot.clone());
    std::mem::forget(lease.clone());
}
pub(crate) struct AiPlan {
    snapshot: Arc<Snapshot>,
    destination: String,
    lease: Arc<Lease>,
    budget: u64,
    remaining: Duration,
}
pub(crate) struct AiReady {
    tx: AtomicDirectory,
    snapshot: Arc<Snapshot>,
    lease: Arc<Lease>,
    destination: String,
    frames: u64,
    normalization: Option<Value>,
}

fn remove_intermediates(
    paths: &[PathBuf],
    snapshot: &Snapshot,
    outer: &Path,
    outer_handle: &File,
    outer_identity: &fs::Metadata,
) -> Result<(), MediaError> {
    for path in paths {
        let root_path = fs::symlink_metadata(&snapshot.root)
            .map_err(|_| err("CLEANUP_FAILED", "AI staging cleanup is unconfirmed."))?;
        let root_fd = snapshot
            .root_handle
            .metadata()
            .map_err(|_| err("CLEANUP_FAILED", "AI staging cleanup is unconfirmed."))?;
        let outer_path = fs::symlink_metadata(outer)
            .map_err(|_| err("CLEANUP_FAILED", "AI staging cleanup is unconfirmed."))?;
        let outer_fd = outer_handle
            .metadata()
            .map_err(|_| err("CLEANUP_FAILED", "AI staging cleanup is unconfirmed."))?;
        if root_path.file_type().is_symlink()
            || !same_dir(&snapshot.root_identity, &root_path)
            || !same_dir(&snapshot.root_identity, &root_fd)
            || outer_path.file_type().is_symlink()
            || !same_dir(outer_identity, &outer_path)
            || !same_dir(outer_identity, &outer_fd)
            || path.parent() != Some(outer)
        {
            return Err(err("CLEANUP_FAILED", "AI staging cleanup is unconfirmed."));
        }
        match fs::symlink_metadata(path) {
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => continue,
            Ok(m) if m.is_file() && !m.file_type().is_symlink() && m.nlink() == 1 => {
                fs::remove_file(path)
                    .map_err(|_| err("CLEANUP_FAILED", "AI staging cleanup is unconfirmed."))?;
            }
            _ => return Err(err("CLEANUP_FAILED", "AI staging cleanup is unconfirmed.")),
        }
    }
    Ok(())
}

fn ffmpeg_run(
    program: &Path,
    args: Vec<OsString>,
    deadline: Instant,
    cancel: &AtomicBool,
) -> Result<(), MediaError> {
    let remaining = deadline.saturating_duration_since(Instant::now());
    if remaining.is_zero() {
        return Err(err("TIMEOUT", "The AI operation exceeded its deadline."));
    }
    run_group(program, &args, remaining, cancel, 16384).map_err(run_error)?;
    Ok(())
}

fn normalize(
    snapshot: &Snapshot,
    root: &Path,
    deadline: Instant,
    cancel: &AtomicBool,
) -> Result<(PathBuf, u64, Value), MediaError> {
    let c = snapshot
        .conversion
        .as_ref()
        .ok_or_else(|| err("PROCESS_FAILED", "Audio conversion is unavailable."))?;
    let native = root.join("decoded-native.wav");
    let provisional = root.join("normalized-provisional.wav");
    let normalized = root.join("normalized.wav");
    let args =
        |input: &Path, output: &Path, filter: Option<String>, limit: bool| -> Vec<OsString> {
            let mut a: Vec<OsString> = [
                "-nostdin",
                "-hide_banner",
                "-v",
                "error",
                "-xerror",
                "-protocol_whitelist",
                "file",
                "-format_whitelist",
                "wav,flac,mp3,mov",
                "-i",
            ]
            .into_iter()
            .map(Into::into)
            .collect();
            a.push(input.as_os_str().to_owned());
            a.extend(
                ["-map", "0:a:0", "-vn", "-sn", "-dn"]
                    .into_iter()
                    .map(OsString::from),
            );
            if let Some(filter) = filter {
                a.push("-af".into());
                a.push(filter.into());
            }
            if limit {
                a.extend(["-t", "601"].into_iter().map(OsString::from));
            }
            let max_bytes = if limit {
                FORMAT_MAX_FRAMES * 8 + 1024 * 1024
            } else {
                (MAX_FRAMES + 2) * 8 + 1024 * 1024
            };
            a.push("-fs".into());
            a.push(max_bytes.to_string().into());
            a.extend(
                ["-c:a", "pcm_f32le", "-f", "wav", "-n"]
                    .into_iter()
                    .map(OsString::from),
            );
            a.push(output.as_os_str().to_owned());
            a
        };
    ffmpeg_run(
        &c.ffmpeg,
        args(&snapshot.source, &native, None, true),
        deadline,
        cancel,
    )?;
    let len = fs::metadata(&native)
        .map_err(|_| err("INVALID_INPUT", "The decoded audio is unavailable."))?
        .len();
    if len >= FORMAT_MAX_FRAMES * 8 + 1024 * 1024 - 16 * 1024 {
        return Err(err(
            "RESOURCE_LIMIT",
            "The decoded audio exceeded its storage limit.",
        ));
    }
    snapshot.verify()?;
    let source_frames = wav_format(
        &native,
        c.sample_rate,
        c.channels,
        FORMAT_MAX_FRAMES,
        true,
        Some(cancel),
        Some(deadline),
    )?;
    let expected =
        (source_frames * 44100 + u64::from(c.sample_rate) / 2) / u64::from(c.sample_rate);
    if !(MIN_FRAMES..=MAX_FRAMES).contains(&expected) {
        return Err(err(
            "UNSUPPORTED_FORMAT",
            "The audio duration is unsupported.",
        ));
    }
    let filter = if c.channels == 1 {
        "pan=stereo|c0=c0|c1=c0,aresample=44100"
    } else {
        "aresample=44100"
    };
    ffmpeg_run(
        &c.ffmpeg,
        args(&native, &provisional, Some(filter.into()), false),
        deadline,
        cancel,
    )?;
    let len = fs::metadata(&provisional)
        .map_err(|_| err("INVALID_INPUT", "The converted audio is unavailable."))?
        .len();
    if len >= (MAX_FRAMES + 2) * 8 + 1024 * 1024 - 16 * 1024 {
        return Err(err(
            "RESOURCE_LIMIT",
            "The converted audio exceeded its storage limit.",
        ));
    }
    let actual = wav_format(
        &provisional,
        44100,
        2,
        MAX_FRAMES + 2,
        true,
        Some(cancel),
        Some(deadline),
    )?;
    let delta =
        i64::try_from(expected).unwrap_or(i64::MAX) - i64::try_from(actual).unwrap_or(i64::MIN);
    if !(-1..=1).contains(&delta) {
        return Err(err(
            "OUTPUT_VALIDATION_FAILED",
            "Audio resampling has an unsupported frame discrepancy.",
        ));
    }
    let filter = format!("apad,atrim=end_sample={expected}");
    ffmpeg_run(
        &c.ffmpeg,
        args(&provisional, &normalized, Some(filter), false),
        deadline,
        cancel,
    )?;
    let len = fs::metadata(&normalized)
        .map_err(|_| err("INVALID_INPUT", "The converted audio is unavailable."))?
        .len();
    if len >= (MAX_FRAMES + 2) * 8 + 1024 * 1024 - 16 * 1024 {
        return Err(err(
            "RESOURCE_LIMIT",
            "The converted audio exceeded its storage limit.",
        ));
    }
    if wav(&normalized, true, Some(cancel), Some(deadline))? != expected {
        return Err(err(
            "OUTPUT_VALIDATION_FAILED",
            "Audio normalization has an incorrect duration.",
        ));
    }
    snapshot.verify()?;
    Ok((
        normalized,
        expected,
        json!({"source_sample_rate":c.sample_rate,"source_channels":c.channels,"decoded_source_frames":source_frames,"tail_adjustment_frames":delta}),
    ))
}
impl AiPlan {
    pub(crate) fn prepare(
        config: &BackendConfig,
        source: &Path,
        destination: &Path,
    ) -> Result<Self, MediaError> {
        if !cfg!(target_arch = "aarch64") {
            return Err(err(
                "UNSUPPORTED_BACKEND",
                "The AI development backend is not accepted on this platform.",
            ));
        }
        let a = config.ai_backend.clone().ok_or_else(|| {
            err(
                "UNSUPPORTED_BACKEND",
                "The AI development backend is not enabled.",
            )
        })?;
        let preflight_started = Instant::now();
        let preflight_deadline = preflight_started + Duration::from_millis(a.timeout_ms);
        let root = a.development_root.clone().ok_or_else(|| {
            err(
                "UNSUPPORTED_BACKEND",
                "The AI development root is not configured.",
            )
        })?;
        no_symlink_components(&root)?;
        let root_meta = fs::symlink_metadata(&root)
            .map_err(|_| err("INVALID_INPUT", "The development directory is unavailable."))?;
        if !root_meta.is_dir()
            || root_meta.uid() != media_ai::effective_uid()
            || root_meta.mode() & 0o7777 != 0o700
        {
            return Err(err(
                "INVALID_INPUT",
                "The development directory must be owned and private.",
            ));
        }
        let root_file = OpenOptions::new()
            .read(true)
            .custom_flags(0x100)
            .open(&root)
            .map_err(|_| err("INVALID_INPUT", "The development directory is unavailable."))?;
        if !same_dir(
            &root_meta,
            &root_file
                .metadata()
                .map_err(|_| err("INVALID_INPUT", "The development directory is unavailable."))?,
        ) {
            return Err(err("SOURCE_CHANGED", "The development directory changed."));
        }
        let leaf = |p: &Path| -> Result<String, MediaError> {
            let sub = p.strip_prefix(&root).map_err(|_| {
                err(
                    "INVALID_INPUT",
                    "The path must be directly inside the development root.",
                )
            })?;
            let mut c = sub.components();
            match (c.next(), c.next()) {
                (Some(Component::Normal(n)), None) => n
                    .to_str()
                    .map(str::to_owned)
                    .ok_or_else(|| err("INVALID_INPUT", "The file name is invalid.")),
                _ => Err(err(
                    "INVALID_INPUT",
                    "The path must be directly inside the development root.",
                )),
            }
        };
        let _source_name = leaf(source)?;
        let dest = leaf(destination)?;
        match fs::symlink_metadata(destination) {
            Ok(_) => return Err(err("OUTPUT_EXISTS", "The destination already exists.")),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(_) => {
                return Err(err(
                    "PERMISSION_DENIED",
                    "The destination cannot be inspected.",
                ));
            }
        }
        let (source_file, source_meta) = regular(source)
            .map_err(|_| err("INVALID_INPUT", "The source is unavailable or unsupported."))?;
        let (frames, conversion) = match wav(source, true, None, Some(preflight_deadline)) {
            Ok(frames) => (frames, None),
            Err(e) if e.code == "WAV_DURATION_UNSUPPORTED" => {
                return Err(err(
                    "UNSUPPORTED_FORMAT",
                    "The WAV duration is unsupported.",
                ));
            }
            Err(e) if e.code == "UNSUPPORTED_FORMAT" => {
                if source_meta.len() > 512 * 1024 * 1024 {
                    return Err(err("RESOURCE_LIMIT", "The audio input is too large."));
                }
                (
                    0,
                    Some(inspect_conversion(config, source, preflight_deadline)?),
                )
            }
            Err(e) => return Err(e),
        };
        let (_, worker_meta) = regular(&a.worker)
            .map_err(|_| err("DEPENDENCY_MISSING", "The AI worker is unavailable."))?;
        let (_, model_meta) = regular(&a.model)
            .map_err(|_| err("DEPENDENCY_MISSING", "The AI model is unavailable."))?;
        // Python launcher in a venv is often a symlink; trusted local configuration
        // and metadata of its resolved regular binary remain required.
        let python_meta = fs::metadata(&a.python)
            .map_err(|_| err("DEPENDENCY_MISSING", "The Python runtime is unavailable."))?;
        if !python_meta.is_file() {
            return Err(err(
                "DEPENDENCY_MISSING",
                "The Python runtime is unavailable.",
            ));
        }
        let remaining_ms = || -> Result<u64, MediaError> {
            let ms = preflight_deadline
                .saturating_duration_since(Instant::now())
                .as_millis();
            if ms == 0 {
                Err(err("TIMEOUT", "The AI operation exceeded its deadline."))
            } else {
                Ok(ms.min(u64::MAX as u128) as u64)
            }
        };
        if sha256(&a.worker, remaining_ms()?)? != WORKER_SHA {
            return Err(err(
                "DEPENDENCY_MISSING",
                "The fixed worker identity does not match.",
            ));
        }
        if sha256(&a.model, remaining_ms()?)? != MODEL_SHA {
            return Err(err(
                "MODEL_INVALID",
                "The fixed model identity does not match.",
            ));
        }
        let other = media_ffmpeg::resource_snapshot();
        if other["global_quarantined"].as_bool() == Some(true)
            || other["quarantined"].as_bool() != Some(false)
            || other["reserved_storage_bytes"].as_u64() != Some(0)
        {
            return Err(err(
                "RESOURCE_LIMIT",
                "Other media storage reservations are active.",
            ));
        }
        if QUARANTINED.load(Ordering::Acquire) {
            return Err(err("RESOURCE_LIMIT", "AI resources are quarantined."));
        }
        if RESERVED
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_err()
        {
            return Err(err(
                "RESOURCE_LIMIT",
                "An AI storage reservation is active.",
            ));
        }
        let lease = Arc::new(Lease {
            poisoned: AtomicBool::new(false),
        });
        let budget_frames = if conversion.is_some() {
            MAX_FRAMES
        } else {
            frames
        };
        let features = (budget_frames / 1024 + 1).saturating_mul(512 * 4 * 4);
        let outputs = budget_frames.saturating_mul(8).saturating_mul(2);
        let intermediates = if conversion.is_some() {
            FORMAT_MAX_FRAMES * 8 + MAX_FRAMES * 8 * 2 + 3 * 1024 * 1024
        } else {
            0
        };
        let budget = features
            .saturating_add(outputs)
            .saturating_add(intermediates)
            .saturating_add(FLOOR);
        let snapshot = Arc::new(Snapshot {
            root,
            root_handle: root_file,
            root_identity: root_meta,
            source: source.to_owned(),
            source_handle: source_file,
            source_identity: source_meta,
            python_identity: python_meta,
            worker_identity: worker_meta,
            model_identity: model_meta,
            config: a,
            frames,
            conversion,
        });
        if let Err(e) = capacity(&snapshot.root, budget, remaining_ms()?) {
            if e.code == "CLEANUP_FAILED" {
                quarantine(&snapshot, &lease);
            }
            return Err(e);
        }
        snapshot.verify()?;
        let remaining = preflight_deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return Err(err("TIMEOUT", "The AI operation exceeded its deadline."));
        }
        Ok(Self {
            snapshot,
            destination: dest,
            lease,
            budget,
            remaining,
        })
    }
    pub(crate) fn summary(&self) -> Value {
        let frames = if self.snapshot.conversion.is_some() {
            None
        } else {
            Some(self.snapshot.frames)
        };
        json!({"source_name":self.snapshot.source.file_name().and_then(|x|x.to_str()).unwrap_or("unavailable"),"destination_name":self.destination,"requested_range_us":null,"profile":null,"audio_selection":null,"reserved_storage_bytes":self.budget,"estimated_retained_plan_bytes":4096,"sample_rate":44100,"channels":2,"frames":frames,"model_id":"umx_hq_vocals_onnx_v1"})
    }
    pub(crate) fn mark_quarantined(&self) {
        quarantine(&self.snapshot, &self.lease);
    }
    pub(crate) fn run(&self, cancel: &AtomicBool) -> Result<AiReady, MediaError> {
        let deadline = Instant::now() + self.remaining;
        self.snapshot.verify()?;
        if let Err(e) = capacity(
            &self.snapshot.root,
            self.budget,
            deadline
                .saturating_duration_since(Instant::now())
                .as_millis()
                .min(u64::MAX as u128) as u64,
        ) {
            if e.code == "CLEANUP_FAILED" {
                self.mark_quarantined();
            }
            return Err(e);
        }
        if cancel.load(Ordering::Acquire) {
            return Err(err("CANCELLED", "The AI operation was cancelled."));
        }
        if Instant::now() >= deadline {
            return Err(err("TIMEOUT", "The AI operation exceeded its deadline."));
        }
        let mut tx =
            AtomicDirectory::new(&self.snapshot.root, &self.destination).map_err(|e| match e {
                TransactionError::CleanupUnconfirmed => {
                    quarantine(&self.snapshot, &self.lease);
                    err("CLEANUP_FAILED", "AI staging cleanup is unconfirmed.")
                }
                _ => err("PROCESS_FAILED", "AI staging could not be created."),
            })?;
        let outer = tx
            .staging()
            .parent()
            .ok_or_else(|| err("CLEANUP_FAILED", "AI staging cleanup is unconfirmed."))?
            .to_owned();
        let opened_outer = tx.clone_stage_handle();
        let (outer_handle, outer_identity) = match opened_outer {
            Ok(file) => match (file.metadata(), fs::symlink_metadata(&outer)) {
                (Ok(held), Ok(path))
                    if !path.file_type().is_symlink() && same_dir(&held, &path) =>
                {
                    (file, held)
                }
                _ => {
                    quarantine(&self.snapshot, &self.lease);
                    return Err(err("CLEANUP_FAILED", "AI staging cleanup is unconfirmed."));
                }
            },
            Err(_) => {
                quarantine(&self.snapshot, &self.lease);
                return Err(err("CLEANUP_FAILED", "AI staging cleanup is unconfirmed."));
            }
        };
        let intermediates = [
            outer.join("decoded-native.wav"),
            outer.join("normalized-provisional.wav"),
            outer.join("normalized.wav"),
        ];
        let mut frames = self.snapshot.frames;
        let mut normalization = None;
        let validated = (|| -> Result<(), MediaError> {
            let input = if self.snapshot.conversion.is_some() {
                let (path, count, detail) = normalize(&self.snapshot, &outer, deadline, cancel)?;
                frames = count;
                normalization = Some(detail);
                path
            } else {
                self.snapshot.source.clone()
            };
            let a = &self.snapshot.config;
            let args: Vec<OsString> = vec![
                "-I".into(),
                a.worker.as_os_str().into(),
                "--model".into(),
                a.model.as_os_str().into(),
                "--input".into(),
                input.into_os_string(),
                "--staging".into(),
                tx.staging().as_os_str().into(),
            ];
            let result = run_group(
                &a.python,
                &args,
                deadline.saturating_duration_since(Instant::now()),
                cancel,
                16384,
            )
            .map_err(run_error)?;
            self.snapshot.verify()?;
            let value: Value = serde_json::from_slice(&result.stdout)
                .map_err(|_| err("PROCESS_FAILED", "The AI worker response is invalid."))?;
            if value
                != json!({"schema_version":1,"model_id":"open-unmix-umxhq-vocals-onnx-e06097d3","frames":frames,"sample_rate":44100,"channels":2,"outputs":["vocals.wav","accompaniment.wav"]})
            {
                return Err(err("PROCESS_FAILED", "The AI worker response is invalid."));
            }
            for name in ["vocals.wav", "accompaniment.wav"] {
                if wav(
                    &tx.staging().join(name),
                    false,
                    Some(cancel),
                    Some(deadline),
                )
                .map_err(|e| {
                    if e.code == "CANCELLED" || e.code == "TIMEOUT" {
                        e
                    } else {
                        err(
                            "OUTPUT_VALIDATION_FAILED",
                            "The AI output could not be validated.",
                        )
                    }
                })? != frames
                {
                    return Err(err(
                        "OUTPUT_VALIDATION_FAILED",
                        "The AI output has an incorrect duration.",
                    ));
                }
            }
            Ok(())
        })();
        if validated
            .as_ref()
            .is_err_and(|e| e.code == "CLEANUP_FAILED")
        {
            quarantine(&self.snapshot, &self.lease);
            return validated.map(|_| unreachable!());
        }
        if remove_intermediates(
            &intermediates,
            &self.snapshot,
            &outer,
            &outer_handle,
            &outer_identity,
        )
        .is_err()
        {
            quarantine(&self.snapshot, &self.lease);
            return Err(err("CLEANUP_FAILED", "AI staging cleanup is unconfirmed."));
        }
        if let Err(e) = validated {
            if tx.cleanup().is_err() {
                quarantine(&self.snapshot, &self.lease);
                return Err(err("CLEANUP_FAILED", "AI staging cleanup is unconfirmed."));
            }
            return Err(e);
        }
        Ok(AiReady {
            tx,
            snapshot: self.snapshot.clone(),
            lease: self.lease.clone(),
            destination: self.destination.clone(),
            frames,
            normalization,
        })
    }
}
impl AiReady {
    pub(crate) fn cleanup(mut self) -> Result<(), MediaError> {
        if self.tx.cleanup().is_err() {
            quarantine(&self.snapshot, &self.lease);
            return Err(err("CLEANUP_FAILED", "AI staging cleanup is unconfirmed."));
        }
        Ok(())
    }
    /// Called under the queue state lock after checking cancellation.
    pub(crate) fn commit(&mut self) -> Result<(Value, bool), MediaError> {
        self.snapshot.verify()?;
        let outcome = self.tx.commit().map_err(|e| match e {
            TransactionError::OutputExists => {
                err("OUTPUT_EXISTS", "The destination already exists.")
            }
            TransactionError::CleanupUnconfirmed => {
                quarantine(&self.snapshot, &self.lease);
                err("CLEANUP_FAILED", "AI staging cleanup is unconfirmed.")
            }
            _ => err("PROCESS_FAILED", "The AI output could not be published."),
        })?;
        let uncertain = outcome == CommitOutcome::CommittedCleanupUnconfirmed;
        if uncertain {
            quarantine(&self.snapshot, &self.lease);
        }
        let mut result = json!({"model_id":"umx_hq_vocals_onnx_v1","stems":["vocals.wav","accompaniment.wav"],"directory_name":self.destination,"frames":self.frames,"sample_rate":44100,"channels":2,"input_output_frame_offset":0,"cleanup_unconfirmed":uncertain});
        if let Some(ref detail) = self.normalization {
            result["normalization"] = detail.clone();
        }
        Ok((result, uncertain))
    }
}
