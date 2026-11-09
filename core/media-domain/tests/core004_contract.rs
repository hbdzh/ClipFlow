//! CORE-004 opt-in queue/selection syntax and failure cases fixed before implementation.
use media_domain::{CoreError, parse_config, parse_request};
use serde_json::{Value, json};
fn parse(v: Value) -> Result<media_domain::Request, CoreError> {
    parse_request(v.to_string().as_bytes())
}
fn item() -> Value {
    json!({"schema_version":0,"operation":"transcode","source":{"uri":"file:///tmp/a.mp4"},"destination":{"uri":"file:///tmp/new.mp4"},"profile":"mp4_h264_standard_v1","audio_selection":{"mode":"all_supported"}})
}
#[test]
fn explicit_new_operation_and_selection_are_strict() {
    assert!(parse(item()).is_ok());
    for audio in [
        json!({"mode":"none"}),
        json!({"mode":"one","stream_index":2}),
    ] {
        let mut v = item();
        v["audio_selection"] = audio;
        assert!(parse(v).is_ok());
    }
    for audio in [
        json!({"mode":"one"}),
        json!({"mode":"one","stream_index":-1}),
        json!({"mode":"all_supported","stream_index":0}),
        json!({"mode":"none","extra":true}),
        json!("all_supported"),
    ] {
        let mut v = item();
        v["audio_selection"] = audio;
        assert_eq!(parse(v).unwrap_err(), CoreError::InvalidArgument);
    }
    for field in ["codec", "ffmpeg_args", "allow_reencode_fallback"] {
        let mut v = item();
        v[field] = json!(true);
        assert_eq!(parse(v).unwrap_err(), CoreError::InvalidArgument);
    }
    let mut v = item();
    v["profile"] = json!("mp4_h264_copy_v1");
    assert_eq!(parse(v).unwrap_err(), CoreError::InvalidArgument);
}
#[test]
fn batch_requires_explicit_identity_bounded_items_and_no_nested_controls() {
    let base = json!({"schema_version":0,"operation":"batch_submit","submission_id":"batch-1","items":[item()]});
    assert!(parse(base.clone()).is_ok());
    let mut too_many = base.clone();
    too_many["items"] = json!(vec![item(); 33]);
    assert_eq!(parse(too_many).unwrap_err(), CoreError::LimitExceeded);
    for id in [
        json!(""),
        json!("x".repeat(65)),
        json!("中文"),
        json!("a b"),
        json!(null),
    ] {
        let mut v = base.clone();
        v["submission_id"] = id;
        assert_eq!(parse(v).unwrap_err(), CoreError::InvalidArgument);
    }
    for items in [
        json!([]),
        json!([{"schema_version":0,"operation":"queue_snapshot"}]),
        json!([{"schema_version":0,"operation":"probe","source":{"uri":"file:///tmp/a.mp4"}}]),
    ] {
        let mut v = base.clone();
        v["items"] = items;
        assert_eq!(parse(v).unwrap_err(), CoreError::InvalidArgument);
    }
}
#[test]
fn queue_commands_reject_unknown_or_ambiguous_fields() {
    for v in [
        json!({"schema_version":0,"operation":"queue_snapshot"}),
        json!({"schema_version":0,"operation":"history_clear"}),
        json!({"schema_version":0,"operation":"queue_reorder","job_ids":["media-1","media-2"]}),
        json!({"schema_version":0,"operation":"job_retry","job_id":"media-1","submission_id":"retry-1","new_destination":{"uri":"file:///tmp/retry.mp4"}}),
    ] {
        assert!(parse(v).is_ok());
    }
    for v in [
        json!({"schema_version":0,"operation":"queue_snapshot","paths":true}),
        json!({"schema_version":0,"operation":"queue_reorder","job_ids":["same","same"]}),
        json!({"schema_version":0,"operation":"job_retry","job_id":"x","submission_id":"retry-1"}),
    ] {
        assert_eq!(parse(v).unwrap_err(), CoreError::InvalidArgument);
    }
}
#[test]
fn explicit_flag_and_transcode_deadline_are_bounded_without_changing_legacy() {
    let base = json!({"schema_version":0,"media_backend":{"ffmpeg":"/ffmpeg","ffprobe":"/ffprobe","core003_enabled":true,"core004_enabled":true}});
    assert!(parse_config(base.to_string().as_bytes()).is_ok());
    let mut v = base.clone();
    v["media_backend"]["core003_enabled"] = json!(false);
    assert_eq!(
        parse_config(v.to_string().as_bytes()).unwrap_err(),
        CoreError::InvalidArgument
    );
    for deadline in [
        json!(0),
        json!(-1),
        json!(7200001),
        json!(1.5),
        json!("600000"),
    ] {
        let mut v = base.clone();
        v["media_backend"]["transcode_timeout_ms"] = deadline;
        assert_eq!(
            parse_config(v.to_string().as_bytes()).unwrap_err(),
            CoreError::InvalidArgument
        );
    }
}
#[test]
fn frozen_geometry_covers_padding_portrait_no_upscale_and_invalid_public_inputs() {
    use media_domain::{TranscodeProfile, transcode_geometry as geometry};
    for (w, h, profile, expected) in [
        (
            1920,
            1078,
            TranscodeProfile::Small720,
            (1280, 719, 1280, 720),
        ),
        (
            1078,
            1920,
            TranscodeProfile::Small720,
            (719, 1280, 720, 1280),
        ),
        (320, 180, TranscodeProfile::Small720, (320, 180, 320, 180)),
        (
            1920,
            1080,
            TranscodeProfile::Standard,
            (1920, 1080, 1920, 1080),
        ),
    ] {
        assert_eq!(geometry(w, h, profile).unwrap(), expected);
    }
    for (w, h) in [
        (0, 0),
        (1, 2),
        (3, 4),
        (-2, 720),
        (1920, 1920),
        (i64::MAX, 2),
    ] {
        assert_eq!(
            geometry(w, h, TranscodeProfile::Small720).unwrap_err(),
            CoreError::InvalidArgument
        );
    }
}
