//! Versioned C ABI. Caller pointer validity and exclusive engine access are required.
#![deny(unsafe_op_in_unsafe_fn)]

use media_domain::CoreError;
use media_orchestrator::Engine;
use serde_json::Value;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::{ptr, slice, str};

#[allow(non_camel_case_types)]
pub struct ms_engine {
    inner: Engine,
    quarantined: bool,
}

#[repr(C)]
#[allow(non_camel_case_types)]
pub struct ms_buffer {
    pub ptr: *mut u8,
    pub len: usize,
}

impl ms_buffer {
    fn empty() -> Self {
        Self {
            ptr: ptr::null_mut(),
            len: 0,
        }
    }
}

fn boundary(action: impl FnOnce() -> Result<(), CoreError>) -> i32 {
    match catch_unwind(AssertUnwindSafe(action)) {
        Ok(Ok(())) => 0,
        Ok(Err(error)) => error.code(),
        Err(payload) => {
            // A user panic payload can itself panic during Drop. It must not drop here.
            std::mem::forget(payload);
            CoreError::Internal.code()
        }
    }
}

unsafe fn with_engine<T>(
    handle: *mut ms_engine,
    action: impl FnOnce(&mut Engine) -> Result<T, CoreError>,
) -> Result<T, CoreError> {
    if handle.is_null() {
        return Err(CoreError::InvalidHandle);
    }
    // SAFETY: the caller supplies one live handle, exclusively borrowed for this call;
    // destroy must not race this access. Non-null invalid handles are caller violations.
    let wrapper = unsafe { &mut *handle };
    if wrapper.quarantined {
        return Err(CoreError::Internal);
    }
    match catch_unwind(AssertUnwindSafe(|| action(&mut wrapper.inner))) {
        Ok(result) => result,
        Err(payload) => {
            wrapper.quarantined = true;
            std::mem::forget(payload);
            Err(CoreError::Internal)
        }
    }
}

unsafe fn input<'a>(bytes: *const u8, len: usize, maximum: usize) -> Result<&'a [u8], CoreError> {
    if len > maximum {
        return Err(CoreError::LimitExceeded);
    }
    if bytes.is_null() || len == 0 {
        return Err(CoreError::InvalidArgument);
    }
    // SAFETY: caller keeps these len bytes readable and nonaliasing for the call;
    // nonempty length is bounded above before constructing a Rust slice.
    Ok(unsafe { slice::from_raw_parts(bytes, len) })
}

unsafe fn prepare_output(out: *mut ms_buffer) -> Result<(), CoreError> {
    if out.is_null() {
        return Err(CoreError::InvalidArgument);
    }
    // SAFETY: caller supplies fresh, aligned writable storage, not an owned old
    // buffer. ptr::write does not read potentially uninitialized output storage.
    unsafe { ptr::write(out, ms_buffer::empty()) };
    Ok(())
}

unsafe fn write_json(out: *mut ms_buffer, value: Value) -> Result<(), CoreError> {
    let bytes = serde_json::to_vec(&value).map_err(|_| CoreError::Internal)?;
    let bytes = bytes.into_boxed_slice();
    let len = bytes.len();
    let raw = Box::into_raw(bytes).cast::<u8>();
    // SAFETY: prepare_output validated nullness; caller guarantees storage validity
    // and exclusivity. The owned allocation is transferred with its exact length.
    unsafe { ptr::write(out, ms_buffer { ptr: raw, len }) };
    Ok(())
}

/// Create an engine. Input is copied/parsed during this call; failure clears output.
///
/// # Safety
/// All non-null pointers must meet contracts/README.md validity, alignment, lifetime
/// and nonaliasing requirements. out_engine must be fresh writable pointer storage.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn ms_engine_create(
    config_json: *const u8,
    config_len: usize,
    out_engine: *mut *mut ms_engine,
) -> i32 {
    boundary(|| {
        if out_engine.is_null() {
            return Err(CoreError::InvalidArgument);
        }
        // SAFETY: caller guarantees fresh, aligned writable output storage.
        unsafe { ptr::write(out_engine, ptr::null_mut()) };
        // SAFETY: exported input validity requirements apply; helper checks limits.
        let config = unsafe { input(config_json, config_len, 65_536) }?;
        let wrapper = Box::new(ms_engine {
            inner: Engine::new(config)?,
            quarantined: false,
        });
        // SAFETY: output is exclusive, valid storage. Caller now owns one handle.
        unsafe { ptr::write(out_engine, Box::into_raw(wrapper)) };
        Ok(())
    })
}

/// Destroy a quiescent engine, or return BUSY without consuming it.
///
/// # Safety
/// handle is null or one live allocation from create. This call is exclusive of
/// all other handle access. A successful destroy invalidates the pointer forever.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn ms_engine_destroy(handle: *mut ms_engine) -> i32 {
    boundary(|| {
        if handle.is_null() {
            return Err(CoreError::InvalidHandle);
        }
        // SAFETY: caller guarantees a live, exclusive handle until this call returns.
        let wrapper = unsafe { &*handle };
        if !wrapper.quarantined && wrapper.inner.has_active_jobs() {
            return Err(CoreError::Busy);
        }
        // SAFETY: this is the original Box pointer, consumed only once. Engine's
        // Drop cancels and joins its worker; destroy must run off the UI thread.
        drop(unsafe { Box::from_raw(handle) });
        Ok(())
    })
}

/// Return version JSON owned independently of the engine.
///
/// # Safety
/// Caller provides a live exclusive handle and fresh writable out_json storage;
/// pointers must not alias. Free successful output with ms_buffer_free exactly once.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn ms_get_version(handle: *mut ms_engine, out_json: *mut ms_buffer) -> i32 {
    boundary(|| {
        // SAFETY: exported output storage requirements apply.
        unsafe { prepare_output(out_json) }?;
        // SAFETY: caller provides a live exclusive engine and nonaliasing output.
        unsafe { with_engine(handle, |engine| write_json(out_json, engine.version())) }
    })
}

/// Return capability JSON. Media processing capabilities are unavailable in CORE-001.
///
/// # Safety
/// Same pointer, ownership and exclusive access requirements as ms_get_version.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn ms_get_capabilities(
    handle: *mut ms_engine,
    out_json: *mut ms_buffer,
) -> i32 {
    boundary(|| {
        // SAFETY: exported output storage requirements apply.
        unsafe { prepare_output(out_json) }?;
        // SAFETY: caller provides a live exclusive engine and nonaliasing output.
        unsafe { with_engine(handle, |engine| write_json(out_json, engine.capabilities())) }
    })
}

/// Submit a mock request, or a real media request with an explicit backend config.
///
/// # Safety
/// handle is live and exclusive, request_len input bytes remain readable for the
/// call, and out_json is fresh writable nonaliasing storage. No NUL is required.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn ms_submit_job(
    handle: *mut ms_engine,
    request_json: *const u8,
    request_len: usize,
    out_json: *mut ms_buffer,
) -> i32 {
    boundary(|| {
        // SAFETY: exported output storage requirements apply.
        unsafe { prepare_output(out_json) }?;
        // SAFETY: exported input validity requirements apply; helper checks limits.
        let request = unsafe { input(request_json, request_len, 65_536) }?;
        // SAFETY: caller provides exclusive engine and output. Request is borrowed
        // only here; Engine stores owned job values, never this caller slice.
        unsafe {
            with_engine(handle, |engine| {
                write_json(out_json, engine.submit(request)?)
            })
        }
    })
}

/// Poll a job using a plain UTF-8 ID. Media snapshots only observe the worker.
///
/// # Safety
/// handle is live and exclusive, job_id_len bytes are valid readable memory for
/// the call, and out_json is fresh writable nonaliasing storage.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn ms_get_job_snapshot(
    handle: *mut ms_engine,
    job_id: *const u8,
    job_id_len: usize,
    out_json: *mut ms_buffer,
) -> i32 {
    boundary(|| {
        // SAFETY: exported output storage requirements apply.
        unsafe { prepare_output(out_json) }?;
        // SAFETY: exported input validity requirements apply; helper checks limits.
        let bytes = unsafe { input(job_id, job_id_len, 128) }?;
        let id = str::from_utf8(bytes).map_err(|_| CoreError::InvalidUtf8)?;
        // SAFETY: caller provides a live exclusive engine and nonaliasing output.
        unsafe { with_engine(handle, |engine| write_json(out_json, engine.snapshot(id)?)) }
    })
}

/// Request cancellation; successful return does not mean active work is cleaned up.
///
/// # Safety
/// handle is live and exclusive; job_id_len readable bytes are valid for this call.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn ms_cancel_job(
    handle: *mut ms_engine,
    job_id: *const u8,
    job_id_len: usize,
) -> i32 {
    boundary(|| {
        // SAFETY: exported input validity requirements apply; helper checks limits.
        let bytes = unsafe { input(job_id, job_id_len, 128) }?;
        let id = str::from_utf8(bytes).map_err(|_| CoreError::InvalidUtf8)?;
        // SAFETY: caller guarantees exclusive live engine access.
        unsafe { with_engine(handle, |engine| engine.cancel(id)) }
    })
}

/// Release one unchanged Rust output allocation, or the empty {NULL,0} buffer.
///
/// # Safety
/// Nonempty buffer must be exactly one live output from this library, with its
/// original ptr/len, not previously freed, and no concurrent readers/writers.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn ms_buffer_free(buffer: ms_buffer) {
    let _ = boundary(|| {
        if buffer.ptr.is_null() && buffer.len == 0 {
            return Ok(());
        }
        // SAFETY: caller must supply the original allocation and exact length.
        // Rebuilding the boxed slice restores the same Rust allocator/layout;
        // arbitrary pointers or modified lengths cannot be validated here.
        drop(unsafe { Box::from_raw(ptr::slice_from_raw_parts_mut(buffer.ptr, buffer.len)) });
        Ok(())
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Payload;
    impl Drop for Payload {
        fn drop(&mut self) {
            panic!("payload must not be dropped across the ABI");
        }
    }

    #[test]
    fn panic_payload_does_not_unwind_past_boundary() {
        assert_eq!(boundary(|| std::panic::panic_any(Payload)), -100);
    }

    #[test]
    fn engine_panic_quarantines_until_destroy() -> Result<(), CoreError> {
        let engine = Engine::new(br#"{"schema_version":0}"#)?;
        let handle = Box::into_raw(Box::new(ms_engine {
            inner: engine,
            quarantined: false,
        }));
        // SAFETY: test owns this live handle exclusively until destroy below.
        let result: Result<(), CoreError> =
            unsafe { with_engine(handle, |_| std::panic::panic_any(Payload)) };
        assert_eq!(result.map_err(|e| e.code()), Err(-100));
        let mut output = ms_buffer::empty();
        // SAFETY: handle remains live; output is fresh aligned nonaliasing storage.
        assert_eq!(unsafe { ms_get_version(handle, &mut output) }, -100);
        assert!(output.ptr.is_null());
        assert_eq!(output.len, 0);
        // SAFETY: quarantined engine is live and this call consumes it once.
        assert_eq!(unsafe { ms_engine_destroy(handle) }, 0);
        Ok(())
    }
}
