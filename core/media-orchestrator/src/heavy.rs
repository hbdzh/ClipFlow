//! Process-wide heavy-work permit. A cleanup failure quarantines the permit;
//! no later engine is allowed to reuse an unconfirmed process/resource slot.
use media_domain::MediaError;
use std::sync::atomic::{AtomicBool, Ordering};
static HELD: AtomicBool = AtomicBool::new(false);
static QUARANTINED: AtomicBool = AtomicBool::new(false);
pub(crate) struct Permit {
    poisoned: bool,
}
pub(crate) fn quarantined() -> bool {
    QUARANTINED.load(Ordering::Acquire) || media_ffmpeg::quarantined()
}
pub(crate) fn quarantine() {
    QUARANTINED.store(true, Ordering::Release);
}
pub(crate) fn try_acquire() -> Result<Option<Permit>, MediaError> {
    if quarantined() {
        return Err(MediaError::new(
            "CLEANUP_FAILED",
            "A process-wide heavy resource has not been confirmed clean.",
        ));
    }
    if HELD
        .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
        .is_err()
    {
        return Ok(None);
    }
    if quarantined() {
        HELD.store(false, Ordering::Release);
        return Err(MediaError::new(
            "CLEANUP_FAILED",
            "A process-wide heavy resource is quarantined.",
        ));
    }
    Ok(Some(Permit { poisoned: false }))
}
impl Permit {
    pub(crate) fn quarantine(&mut self) {
        self.poisoned = true;
        QUARANTINED.store(true, Ordering::Release);
    }
}
impl Drop for Permit {
    fn drop(&mut self) {
        if !self.poisoned {
            HELD.store(false, Ordering::Release);
        }
    }
}
