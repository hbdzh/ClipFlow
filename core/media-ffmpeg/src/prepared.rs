use crate::{
    accurate,
    files::{self, Source, Temporary},
    media::{self, Media},
    process::{Context, args},
    scope, transcode,
};
use media_domain::{BackendConfig, MediaError, MediaRequest, TrimStrategy};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    path::{Path, PathBuf},
    sync::{Mutex, OnceLock, atomic::AtomicBool},
};
static TARGETS: OnceLock<Mutex<BTreeMap<PathBuf, String>>> = OnceLock::new();
struct TargetLease(PathBuf);
impl Drop for TargetLease {
    fn drop(&mut self) {
        if let Ok(mut t) = TARGETS
            .get_or_init(|| Mutex::new(Default::default()))
            .lock()
        {
            t.remove(&self.0);
        }
    }
}
static PLAN_BYTES: Mutex<u64> = Mutex::new(0);
struct PlanLease(u64);
impl Drop for PlanLease {
    fn drop(&mut self) {
        if let Ok(mut n) = PLAN_BYTES.lock() {
            *n = n.saturating_sub(self.0);
        }
    }
}
pub(crate) static QUARANTINED: AtomicBool = AtomicBool::new(false);
const FLOOR: u64 = 64 * 1024 * 1024;
static RESERVED: OnceLock<Mutex<BTreeMap<String, u64>>> = OnceLock::new();
fn ledger() -> &'static Mutex<BTreeMap<String, u64>> {
    RESERVED.get_or_init(|| Mutex::new(BTreeMap::new()))
}
fn processing_budget(config: &BackendConfig, request: &MediaRequest) -> std::time::Duration {
    std::time::Duration::from_millis(match request {
        MediaRequest::Probe { .. }
        | MediaRequest::Trim {
            strategy: TrimStrategy::StreamCopy | TrimStrategy::StreamCopyV1,
            ..
        } => config.timeout_ms,
        MediaRequest::Trim {
            strategy: TrimStrategy::AccurateReencode,
            ..
        } => config.accurate_reencode_timeout_ms,
        MediaRequest::Transcode { .. } => config.transcode_timeout_ms,
    })
}
fn remaining_budget(
    total: std::time::Duration,
    spent: std::time::Duration,
) -> Result<std::time::Duration, MediaError> {
    total
        .checked_sub(spent)
        .filter(|remaining| !remaining.is_zero())
        .ok_or_else(|| {
            MediaError::new(
                "TIMEOUT",
                "Media processing phases exhausted their shared operation budget.",
            )
        })
}
fn resource() -> MediaError {
    MediaError::new(
        "RESOURCE_LIMIT",
        "Storage or retained execution plan budget is unavailable.",
    )
}
struct Lease {
    volume: String,
    bytes: u64,
}
impl Drop for Lease {
    fn drop(&mut self) {
        if let Ok(mut l) = ledger().lock()
            && let Some(n) = l.get_mut(&self.volume)
        {
            *n = n.saturating_sub(self.bytes);
            if *n == 0 {
                l.remove(&self.volume);
            }
        }
    }
}
pub struct PreparedMedia {
    quarantined: AtomicBool,
    processing_budget: std::time::Duration,
    preflight_elapsed: std::time::Duration,
    request: MediaRequest,
    source: Source,
    media: Media,
    destination: Option<PathBuf>,
    parent_identity: Option<String>,
    volume: String,
    budget: u64,
    available: u64,
    retained: u64,
    options: Option<transcode::Options>,
    _lease: Option<Lease>,
    _plan: PlanLease,
    _target: Option<TargetLease>,
}
pub fn quarantined() -> bool {
    QUARANTINED.load(std::sync::atomic::Ordering::Acquire)
}
/// Scalar diagnostics only; each lock is released before acquiring the next.
/// Quarantined leases remain visible after the owning job became terminal.
pub fn resource_snapshot() -> Value {
    let bytes = ledger()
        .lock()
        .ok()
        .map(|l| l.values().copied().fold(0_u64, u64::saturating_add));
    let targets = TARGETS
        .get_or_init(|| Mutex::new(Default::default()))
        .lock()
        .ok()
        .map(|t| t.len());
    let plan = PLAN_BYTES.lock().ok().map(|n| *n);
    json!({"reserved_storage_bytes":bytes,"reserved_target_count":targets,"estimated_retained_plan_bytes":plan,"quarantined":quarantined()||bytes.is_none()||targets.is_none()||plan.is_none()})
}
impl PreparedMedia {
    pub fn mark_quarantined(&self) {
        QUARANTINED.store(true, std::sync::atomic::Ordering::Release);
        self.quarantined
            .store(true, std::sync::atomic::Ordering::Release);
    }
    pub fn mark_cleanup_unconfirmed(&self) {
        self.mark_quarantined();
    }

    pub fn source_identity(&self) -> String {
        self.source.identity()
    }
    pub fn destination_identity(&self) -> &Path {
        self.destination.as_deref().unwrap_or(Path::new(""))
    }
    pub fn storage_identity(&self) -> String {
        self.volume.clone()
    }
    pub fn estimated_output_bytes(&self) -> u64 {
        self.budget
    }
    pub fn available_storage_bytes(&self) -> u64 {
        self.available
    }
    pub fn estimated_plan_bytes(&self) -> u64 {
        self.retained
    }
    pub fn summary(&self) -> Value {
        json!({"estimated_output_bytes":self.budget,"reserved_storage_bytes":self._lease.as_ref().map_or(0,|l|l.bytes),"available_storage_bytes_at_preflight":self.available,"estimated_retained_plan_bytes":self.retained,"transcode_plan":self.options.as_ref().map(|o|o.summary())})
    }
}
fn parent_identity(path: &Path) -> Result<String, MediaError> {
    let meta = std::fs::metadata(path.parent().ok_or_else(resource)?)
        .map_err(|_| MediaError::new("PERMISSION_DENIED", "Output directory is unavailable."))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        Ok(format!("{}:{}", meta.dev(), meta.ino()))
    }
    #[cfg(not(unix))]
    {
        let _ = meta;
        Err(resource())
    }
}
fn storage(context: &Context<'_>, destination: &Path) -> Result<(String, u64), MediaError> {
    let parent = destination.parent().ok_or_else(resource)?;
    let metadata = std::fs::metadata(parent).map_err(|_| resource())?;
    #[cfg(unix)]
    let volume = {
        use std::os::unix::fs::MetadataExt;
        metadata.dev().to_string()
    };
    #[cfg(not(unix))]
    let volume = {
        let _ = metadata;
        return Err(resource());
    };
    let mut command = args(&["-Pk"]);
    command.push(parent.as_os_str().to_owned());
    let bytes = context.run(Path::new("/bin/df"), &command)?;
    let text = std::str::from_utf8(&bytes).map_err(|_| resource())?;
    let rows = text
        .lines()
        .filter_map(|line| {
            let c = line.split_whitespace().collect::<Vec<_>>();
            if c.len() >= 6 && c[1].parse::<u64>().is_ok() && c[2].parse::<u64>().is_ok() {
                c[3].parse::<u64>().ok()
            } else {
                None
            }
        })
        .collect::<Vec<_>>();
    if rows.len() != 1 {
        return Err(resource());
    }
    let available = rows[0].checked_mul(1024).ok_or_else(resource)?;
    Ok((volume, available))
}
/// Compare names using this volume's actual create-new semantics. Never create
/// the final destination; every marker lives in our private sibling directory.
fn aliases_reserved(
    destination: &Path,
    targets: &BTreeMap<PathBuf, String>,
) -> Result<bool, MediaError> {
    let identity = parent_identity(destination)?;
    let mut probe = Temporary::new(destination)?;
    let directory = probe.path.parent().ok_or_else(resource)?.to_path_buf();
    let mut owned = Vec::new();
    let mut collision = false;
    let mut failure = None;
    for path in targets
        .iter()
        .filter(|(_, id)| **id == identity)
        .map(|(p, _)| p.as_path())
        .chain(std::iter::once(destination))
    {
        let Some(name) = path.file_name() else {
            failure = Some(resource());
            break;
        };
        let marker = directory.join(name);
        match std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&marker)
        {
            Ok(file) => {
                drop(file);
                owned.push(marker);
            }
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                collision = true;
                break;
            }
            Err(_) => {
                failure = Some(MediaError::new(
                    "PERMISSION_DENIED",
                    "Output name cannot be checked on this volume.",
                ));
                break;
            }
        }
    }
    let mut cleanup_failed = false;
    for marker in owned {
        if std::fs::remove_file(marker).is_err() {
            cleanup_failed = true;
        }
    }
    if probe.cleanup().is_err() {
        cleanup_failed = true;
    }
    if cleanup_failed {
        QUARANTINED.store(true, std::sync::atomic::Ordering::Release);
        return Err(MediaError::new(
            "CLEANUP_FAILED",
            "Output name probe cleanup remains unconfirmed.",
        ));
    }
    if let Some(error) = failure {
        return Err(error);
    }
    Ok(collision)
}
/// Rust-only retry guard. Physical parent identity plus OS filename semantics;
/// this creates only self-owned zero-byte names inside a private probe directory.
pub fn destinations_equivalent(a: &Path, b: &Path) -> Result<bool, MediaError> {
    let a_parent = parent_identity(a)?;
    let b_parent = parent_identity(b)?;
    if a_parent != b_parent {
        return Ok(false);
    }
    aliases_reserved(a, &BTreeMap::from([(b.to_path_buf(), b_parent)]))
}
pub fn preflight(
    config: &BackendConfig,
    request: &MediaRequest,
    cancel: &AtomicBool,
) -> Result<PreparedMedia, MediaError> {
    let started = std::time::Instant::now();
    let processing_budget = processing_budget(config, request);
    if QUARANTINED.load(std::sync::atomic::Ordering::Acquire) {
        return Err(MediaError::new(
            "CLEANUP_FAILED",
            "Backend cleanup remains unconfirmed until process restart.",
        ));
    }
    if !config.core004_enabled || !config.core003_enabled {
        return Err(MediaError::new(
            "UNSUPPORTED_BACKEND",
            "CORE-004 requires explicit CORE-003 and CORE-004 opt-in.",
        ));
    }
    if !cfg!(target_os = "macos") {
        return Err(MediaError::new(
            "UNSUPPORTED_BACKEND",
            "Local queue preflight is accepted only on macOS.",
        ));
    }
    let context = Context::with_budget(config, cancel, processing_budget);
    context.check()?;
    let (path, dest) = match request {
        MediaRequest::Probe { source } => (source, None),
        MediaRequest::Trim {
            source,
            destination,
            ..
        }
        | MediaRequest::Transcode {
            source,
            destination,
            ..
        } => (source, Some(destination)),
    };
    let source = Source::open(path)?;
    crate::tools(&context)?;
    let media = media::inspect(&context, &source.path)?;
    let mut target = None;
    let mut captured_parent = None;
    let mut options = None;
    let mut budget = 0;
    let mut available = 0;
    let mut volume = String::new();
    let mut lease = None;
    let destination = if let Some(dest) = dest {
        let dest = files::destination(&source, dest)?;
        let original_parent = parent_identity(&dest)?;
        captured_parent = Some(original_parent.clone());
        let (start, end) = match request {
            MediaRequest::Trim {
                start_us,
                end_us,
                strategy,
                ..
            } => {
                if *strategy != TrimStrategy::AccurateReencode {
                    crate::trim::preflight(
                        &media,
                        *start_us,
                        *end_us,
                        *strategy != TrimStrategy::StreamCopy,
                    )?;
                }
                (*start_us, *end_us)
            }
            MediaRequest::Transcode { range, .. } => range
                .as_ref()
                .map_or((0, media.duration_us), |r| (r.start_us, r.end_us)),
            _ => unreachable!(),
        };
        if start < 0 || end <= start || end > media.duration_us {
            return Err(MediaError::new(
                "INVALID_INPUT",
                "Requested range is outside the source timeline.",
            ));
        }
        budget = u64::try_from((i128::from(end - start) * 4_100_000 + 999_999) / 1_000_000)
            .map_err(|_| resource())?
            .checked_add(16 * 1024 * 1024)
            .ok_or_else(resource)?;
        if budget > 3 * 1024 * 1024 * 1024 {
            return Err(resource());
        }
        if let MediaRequest::Transcode {
            profile,
            audio_selection,
            ..
        } = request
        {
            scope::supported(&media)?;
            accurate::encoders(&context)?;
            options = Some(transcode::options(
                &media,
                *profile,
                audio_selection,
                budget,
            )?);
            transcode::preflight(&media, start, end, options.as_ref().ok_or_else(resource)?)?;
        }
        if let MediaRequest::Trim {
            strategy: TrimStrategy::AccurateReencode,
            ..
        } = request
        {
            accurate::encoders(&context)?;
            accurate::preflight(&media, start, end)?;
        }
        let mut probe = Temporary::new(&dest)?;
        if let Err(error) = probe.cleanup() {
            QUARANTINED.store(true, std::sync::atomic::Ordering::Release);
            return Err(error);
        }
        (volume, available) = storage(&context, &dest)?;
        let reserve = budget
            .checked_mul(2)
            .and_then(|b| b.checked_add(FLOOR))
            .ok_or_else(resource)?;
        let mut l = ledger().lock().map_err(|_| resource())?;
        let total = l
            .get(&volume)
            .copied()
            .unwrap_or(0)
            .checked_add(reserve)
            .ok_or_else(resource)?;
        if total.checked_add(FLOOR).is_none_or(|v| v > available) {
            return Err(MediaError::new(
                "OUT_OF_DISK",
                "Insufficient unreserved output storage.",
            ));
        }
        l.insert(volume.clone(), total);
        lease = Some(Lease {
            volume: volume.clone(),
            bytes: reserve,
        });
        drop(l);
        let mut targets = TARGETS
            .get_or_init(|| Mutex::new(Default::default()))
            .lock()
            .map_err(|_| resource())?;
        if aliases_reserved(&dest, &targets)? {
            return Err(MediaError::new(
                "OUTPUT_RESERVED",
                "Output is already reserved by an accepted operation.",
            ));
        }
        targets.insert(dest.clone(), original_parent);
        target = Some(TargetLease(dest.clone()));
        Some(dest)
    } else {
        None
    };
    source.unchanged()?;
    context.check()?;
    let retained = serde_json::to_vec(&media.info)
        .map_err(|_| resource())?
        .len()
        .saturating_mul(8)
        .saturating_add(
            serde_json::to_vec(&media.packets)
                .map_err(|_| resource())?
                .len()
                .saturating_mul(8),
        )
        .saturating_add(media.frames.len().saturating_mul(64))
        .saturating_add(4096) as u64;
    let current_parent = destination.as_deref().map(parent_identity).transpose()?;
    if current_parent != captured_parent {
        return Err(MediaError::new(
            "SOURCE_CHANGED",
            "Output directory identity changed during preflight.",
        ));
    }
    let parent_identity = captured_parent;
    let mut total = PLAN_BYTES.lock().map_err(|_| resource())?;
    let next = total.checked_add(retained).ok_or_else(resource)?;
    if next > 128 * 1024 * 1024 {
        return Err(resource());
    }
    *total = next;
    drop(total);
    let plan_lease = PlanLease(retained);
    context.check()?;
    let preflight_elapsed = started.elapsed();
    remaining_budget(processing_budget, preflight_elapsed)?;
    Ok(PreparedMedia {
        processing_budget,
        preflight_elapsed,
        parent_identity,
        quarantined: AtomicBool::new(false),
        _target: target,
        _plan: plan_lease,
        request: request.clone(),
        source,
        media,
        destination,
        volume,
        budget,
        available,
        retained,
        options,
        _lease: lease,
    })
}
impl Drop for PreparedMedia {
    fn drop(&mut self) {
        if self.quarantined.load(std::sync::atomic::Ordering::Acquire) {
            if let Some(l) = self._lease.take() {
                std::mem::forget(l);
            }
            if let Some(t) = self._target.take() {
                std::mem::forget(t);
            }
        }
    }
}
pub fn execute_prepared(
    config: &BackendConfig,
    prepared: &PreparedMedia,
    cancel: &AtomicBool,
) -> Result<Value, MediaError> {
    if QUARANTINED.load(std::sync::atomic::Ordering::Acquire) {
        return Err(MediaError::new(
            "CLEANUP_FAILED",
            "Backend cleanup remains unconfirmed until process restart.",
        ));
    }
    let result = execute_inner(config, prepared, cancel);
    if result.as_ref().is_err_and(|e| e.code == "CLEANUP_FAILED") {
        QUARANTINED.store(true, std::sync::atomic::Ordering::Release);
        prepared
            .quarantined
            .store(true, std::sync::atomic::Ordering::Release);
    }
    result
}
fn execute_inner(
    config: &BackendConfig,
    prepared: &PreparedMedia,
    cancel: &AtomicBool,
) -> Result<Value, MediaError> {
    // Only processing inside this item's preflight is charged. Queue residence
    // and other items' preflight work never advance this stored duration.
    let remaining = remaining_budget(prepared.processing_budget, prepared.preflight_elapsed)?;
    let mut context = Context::with_budget(config, cancel, remaining);
    context.media_file_budget = Some(prepared.budget);
    context.check()?;
    prepared.source.unchanged()?;
    if let Some(dest) = &prepared.destination {
        let current = files::destination(&prepared.source, dest)?;
        if current != *dest || Some(parent_identity(dest)?) != prepared.parent_identity {
            return Err(MediaError::new(
                "SOURCE_CHANGED",
                "Output directory identity changed after preflight.",
            ));
        }
        let (volume, available) = storage(&context, dest)?;
        if volume != prepared.volume {
            return Err(MediaError::new(
                "SOURCE_CHANGED",
                "Output storage identity changed.",
            ));
        }
        if available < prepared.budget.checked_add(FLOOR).ok_or_else(resource)? {
            return Err(MediaError::new(
                "OUT_OF_DISK",
                "Output storage no longer satisfies its budget.",
            ));
        }
    }
    if let MediaRequest::Transcode { range, .. } = &prepared.request {
        let (s, e) = range
            .as_ref()
            .map_or((0, prepared.media.duration_us), |r| (r.start_us, r.end_us));
        crate::tools(&context)?;
        return accurate::execute_options(
            &context,
            &prepared.source,
            prepared.destination.as_deref().ok_or_else(resource)?,
            prepared.media.clone(),
            s,
            e,
            prepared.options.as_ref(),
        );
    }
    crate::tools(&context)?;
    match &prepared.request {
        MediaRequest::Probe { .. } => {
            prepared.source.unchanged()?;
            Ok(
                json!({"schema_version":0,"mock_only":false,"file_created":false,"media_info":prepared.media.info}),
            )
        }
        MediaRequest::Trim {
            start_us,
            end_us,
            strategy,
            ..
        } => {
            let dest = prepared.destination.as_deref().ok_or_else(resource)?;
            match strategy {
                TrimStrategy::AccurateReencode => accurate::execute(
                    &context,
                    &prepared.source,
                    dest,
                    prepared.media.clone(),
                    *start_us,
                    *end_us,
                ),
                _ => crate::trim::execute(
                    &context,
                    &prepared.source,
                    dest,
                    prepared.media.clone(),
                    *start_us,
                    *end_us,
                    *strategy == TrimStrategy::StreamCopyV1,
                ),
            }
        }
        _ => unreachable!(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn processing_deadline_uses_the_request_operation_configuration() {
        let config = BackendConfig {
            ffmpeg: PathBuf::from("/missing"),
            ffprobe: PathBuf::from("/missing"),
            timeout_ms: 17,
            accurate_reencode_timeout_ms: 31,
            transcode_timeout_ms: 47,
            core003_enabled: true,
            core004_enabled: true,
            max_output_bytes: 1024,
            ai_backend: None,
        };
        let source = PathBuf::from("/input.mp4");
        let destination = PathBuf::from("/output.mp4");
        assert_eq!(
            processing_budget(
                &config,
                &MediaRequest::Probe {
                    source: source.clone()
                }
            )
            .as_millis(),
            17
        );
        for strategy in [
            TrimStrategy::StreamCopy,
            TrimStrategy::StreamCopyV1,
            TrimStrategy::AccurateReencode,
        ] {
            assert_eq!(
                processing_budget(
                    &config,
                    &MediaRequest::Trim {
                        source: source.clone(),
                        destination: destination.clone(),
                        start_us: 0,
                        end_us: 1000,
                        strategy
                    }
                )
                .as_millis(),
                if strategy == TrimStrategy::AccurateReencode {
                    31
                } else {
                    17
                }
            );
        }
        assert_eq!(
            processing_budget(
                &config,
                &MediaRequest::Transcode {
                    source,
                    destination,
                    range: None,
                    profile: media_domain::TranscodeProfile::Standard,
                    audio_selection: media_domain::AudioSelection::None
                }
            )
            .as_millis(),
            47
        );
    }
    #[test]
    fn processing_budget_spans_phases_without_charging_idle_queue_time() {
        use std::time::Duration;
        assert_eq!(
            remaining_budget(Duration::from_secs(10), Duration::from_secs(6)).expect("remaining"),
            Duration::from_secs(4)
        );
        assert_eq!(
            remaining_budget(Duration::from_secs(10), Duration::from_secs(10))
                .expect_err("exhausted")
                .code,
            "TIMEOUT"
        );
        assert_eq!(
            remaining_budget(Duration::from_secs(10), Duration::from_secs(11))
                .expect_err("overrun")
                .code,
            "TIMEOUT"
        );
    }
    #[test]
    fn reserved_names_follow_volume_alias_rules_and_remove_only_owned_markers() {
        let stamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let directory =
            std::env::temp_dir().join(format!("mediastudio-alias-{}-{stamp}", std::process::id()));
        std::fs::create_dir(&directory).expect("private parent");
        for (a, b) in [
            ("case.mp4", "CASE.mp4"),
            ("é.mp4", "e\u{301}.mp4"),
            ("output.mp4", "other.mp4"),
        ] {
            let first = directory.join(a);
            let second = directory.join(b);
            let file = std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&first)
                .expect("first marker");
            drop(file);
            let expected = match std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&second)
            {
                Ok(file) => {
                    drop(file);
                    std::fs::remove_file(&second).expect("second cleanup");
                    false
                }
                Err(error) => {
                    assert_eq!(error.kind(), std::io::ErrorKind::AlreadyExists);
                    true
                }
            };
            std::fs::remove_file(&first).expect("first cleanup");
            let targets =
                BTreeMap::from([(first, parent_identity(&second).expect("parent identity"))]);
            assert_eq!(
                aliases_reserved(&second, &targets).expect("OS alias check"),
                expected
            );
            assert_eq!(std::fs::read_dir(&directory).expect("parent").count(), 0);
        }
        std::fs::remove_dir(directory).expect("parent cleanup");
    }
}
