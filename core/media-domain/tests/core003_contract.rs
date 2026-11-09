//! CORE-003 additions: explicit profiles do not reinterpret legacy requests.
use media_domain::{CoreError, MediaRequest, Request, TrimStrategy, parse_config, parse_request};
use serde_json::{Value, json};

fn trim() -> Value {
    json!({"schema_version":0,"operation":"trim","source":{"uri":"file:///tmp/in.mp4"},
        "destination":{"uri":"file:///tmp/out.mp4"},"range":{"start_us":1100000,"end_us":2600000},
        "strategy":"stream_copy","stream_selection":"all_supported","allow_reencode_fallback":false})
}

fn parse(value: &Value) -> Result<media_domain::Request, CoreError> {
    parse_request(value.to_string().as_bytes())
}

#[test]
fn accurate_is_explicit_and_accepts_only_the_fixed_standard_profile() {
    let mut input = trim();
    input["strategy"] = json!("accurate_reencode");
    assert_eq!(parse(&input).unwrap_err(), CoreError::InvalidArgument);
    input["profile"] = json!("mp4_h264_standard_v1");
    assert!(matches!(
        parse(&input).unwrap(),
        Request::Media(MediaRequest::Trim {
            strategy: TrimStrategy::AccurateReencode,
            ..
        })
    ));
    for profile in [
        json!(null),
        json!(false),
        json!("mp4_h264_copy_v1"),
        json!("anything"),
        json!({"codec":"h264"}),
    ] {
        input["profile"] = profile;
        assert_eq!(parse(&input).unwrap_err(), CoreError::InvalidArgument);
    }
}

#[test]
fn legacy_copy_remains_valid_but_extended_copy_needs_its_own_profile() {
    let mut input = trim();
    assert!(matches!(
        parse(&input).unwrap(),
        Request::Media(MediaRequest::Trim {
            strategy: TrimStrategy::StreamCopy,
            ..
        })
    ));
    input["profile"] = json!("mp4_h264_copy_v1");
    assert!(matches!(
        parse(&input).unwrap(),
        Request::Media(MediaRequest::Trim {
            strategy: TrimStrategy::StreamCopyV1,
            ..
        })
    ));
    for profile in [
        json!(null),
        json!("mp4_h264_standard_v1"),
        json!(""),
        json!(7),
    ] {
        input["profile"] = profile;
        assert_eq!(parse(&input).unwrap_err(), CoreError::InvalidArgument);
    }
}

#[test]
fn accurate_rejects_fallback_custom_encoders_and_ambiguous_ranges() {
    let mut base = trim();
    base["strategy"] = json!("accurate_reencode");
    base["profile"] = json!("mp4_h264_standard_v1");
    for (key, value) in [
        ("allow_reencode_fallback", json!(true)),
        ("stream_selection", json!("video_only")),
        ("extra_args", json!(["-y"])),
        ("codec", json!("libx264")),
        ("profile", json!({"crf":23})),
        ("strategy", json!("smart_cut")),
    ] {
        let mut input = base.clone();
        input[key] = value;
        assert_eq!(parse(&input).unwrap_err(), CoreError::InvalidArgument);
    }
    for range in [
        json!({"start_us":0,"end_us":0}),
        json!({"start_us":-1,"end_us":10}),
        json!({"start_us":1.1,"end_us":10}),
        json!({"start_us":0,"end_us":10,"unit":"ms"}),
    ] {
        let mut input = base.clone();
        input["range"] = range;
        assert_eq!(parse(&input).unwrap_err(), CoreError::InvalidArgument);
    }
}

#[test]
fn accurate_deadline_is_independent_and_strictly_bounded() {
    let base =
        json!({"schema_version":0,"media_backend":{"ffmpeg":"/ffmpeg","ffprobe":"/ffprobe"}});
    let legacy = parse_config(base.to_string().as_bytes())
        .unwrap()
        .media_backend
        .unwrap();
    assert_eq!(legacy.timeout_ms, 30_000);
    assert_eq!(legacy.accurate_reencode_timeout_ms, 600_000);
    assert!(!legacy.core003_enabled);
    for timeout in [1, 600_000, 7_200_000] {
        let mut config = base.clone();
        config["media_backend"]["accurate_reencode_timeout_ms"] = json!(timeout);
        let backend = parse_config(config.to_string().as_bytes())
            .unwrap()
            .media_backend
            .unwrap();
        assert_eq!(backend.accurate_reencode_timeout_ms, timeout);
        assert_eq!(backend.timeout_ms, 30_000);
    }
    for timeout in [
        json!(0),
        json!(-1),
        json!(7_200_001),
        json!(1.2),
        json!("600000"),
        json!(null),
    ] {
        let mut config = base.clone();
        config["media_backend"]["accurate_reencode_timeout_ms"] = timeout;
        assert_eq!(
            parse_config(config.to_string().as_bytes()).unwrap_err(),
            CoreError::InvalidArgument
        );
    }
    let mut config = base;
    config["media_backend"]["timeout_ms"] = json!(300_001);
    assert_eq!(
        parse_config(config.to_string().as_bytes()).unwrap_err(),
        CoreError::InvalidArgument
    );
}

#[test]
fn core003_capability_gate_is_an_explicit_boolean_config() {
    let base =
        json!({"schema_version":0,"media_backend":{"ffmpeg":"/ffmpeg","ffprobe":"/ffprobe"}});
    for enabled in [true, false] {
        let mut config = base.clone();
        config["media_backend"]["core003_enabled"] = json!(enabled);
        let backend = parse_config(config.to_string().as_bytes())
            .unwrap()
            .media_backend
            .unwrap();
        assert_eq!(backend.core003_enabled, enabled);
    }
    for enabled in [json!(0), json!(1), json!(null), json!("true"), json!({})] {
        let mut config = base.clone();
        config["media_backend"]["core003_enabled"] = enabled;
        assert_eq!(
            parse_config(config.to_string().as_bytes()).unwrap_err(),
            CoreError::InvalidArgument
        );
    }
}
