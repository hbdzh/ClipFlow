//! The existing eight-function ABI also owns/free-controls without fake jobs.
use media_ffi::{
    ms_buffer, ms_buffer_free, ms_engine, ms_engine_create, ms_engine_destroy, ms_submit_job,
};
use std::ptr;
#[test]
fn new_controls_use_existing_owned_buffer_and_syntax_errors_clear_output() {
    let config=br#"{"schema_version":0,"media_backend":{"ffmpeg":"/missing/ffmpeg","ffprobe":"/missing/ffprobe","core003_enabled":true,"core004_enabled":true}}"#;
    let mut engine: *mut ms_engine = ptr::null_mut();
    // SAFETY: fresh exclusive aligned pointer storage and borrowed live config.
    assert_eq!(
        unsafe { ms_engine_create(config.as_ptr(), config.len(), &mut engine) },
        0
    );
    for request in [
        br#"{"schema_version":0,"operation":"queue_snapshot"}"#.as_slice(),
        br#"{"schema_version":0,"operation":"history_clear"}"#.as_slice(),
    ] {
        let mut out = ms_buffer {
            ptr: ptr::null_mut(),
            len: 0,
        };
        // SAFETY: one live exclusive handle, live request and fresh nonaliasing output.
        assert_eq!(
            unsafe { ms_submit_job(engine, request.as_ptr(), request.len(), &mut out) },
            0
        );
        assert!(!out.ptr.is_null());
        assert!(out.len > 0);
        // SAFETY: output allocation belongs to Rust and remains live until free below.
        let value: serde_json::Value =
            serde_json::from_slice(unsafe { std::slice::from_raw_parts(out.ptr, out.len) })
                .unwrap();
        assert!(value.get("job_id").is_none());
        assert_eq!(value["schema_version"], 0);
        // SAFETY: the original ptr/len allocation is returned exactly once.
        unsafe { ms_buffer_free(out) };
    }
    let invalid =
        br#"{"schema_version":0,"operation":"batch_submit","submission_id":"x","items":[]}"#;
    let mut out = ms_buffer {
        ptr: ptr::null_mut(),
        len: 0,
    };
    // SAFETY: same valid exclusive handle, fresh output, bounded live input.
    assert_eq!(
        unsafe { ms_submit_job(engine, invalid.as_ptr(), invalid.len(), &mut out) },
        -1
    );
    assert!(out.ptr.is_null());
    assert_eq!(out.len, 0);
    // SAFETY: an empty buffer may be released; all controls created no active work.
    unsafe { ms_buffer_free(out) };
    // SAFETY: consume this original live handle once, with no outstanding calls.
    assert_eq!(unsafe { ms_engine_destroy(engine) }, 0);
}
