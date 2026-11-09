use media_domain::{CoreError, MediaRequest, Request, file_uri, parse_config, parse_request};
use std::path::Path;

#[test]
fn explicit_backend_has_bounded_defaults_and_strict_fields() {
    assert!(
        parse_config(br#"{"schema_version":0}"#)
            .unwrap()
            .media_backend
            .is_none()
    );
    let config = parse_config(br#"{"schema_version":0,"media_backend":{"ffmpeg":"/tools/ffmpeg","ffprobe":"/tools/ffprobe"}}"#).unwrap();
    let backend = config.media_backend.unwrap();
    assert_eq!(backend.timeout_ms, 30_000);
    assert_eq!(backend.max_output_bytes, 8 * 1024 * 1024);
    for extra in [
        "\"timeout_ms\":0",
        "\"timeout_ms\":300001",
        "\"max_output_bytes\":0",
        "\"max_output_bytes\":67108865",
        "\"filter\":\"anything\"",
    ] {
        let input = format!(
            "{{\"schema_version\":0,\"media_backend\":{{\"ffmpeg\":\"/ffmpeg\",\"ffprobe\":\"/ffprobe\",{extra}}}}}"
        );
        assert_eq!(
            parse_config(input.as_bytes()).unwrap_err(),
            CoreError::InvalidArgument
        );
    }
    for input in [
        br#"{"schema_version":0,"media_backend":null}"#.as_slice(),
        br#"{"schema_version":0,"media_backend":{"ffmpeg":"ffmpeg","ffprobe":"/ffprobe"}}"#,
        br#"{"schema_version":0,"media_backend":{"ffmpeg":"/ffmpeg"}}"#,
    ] {
        assert_eq!(parse_config(input).unwrap_err(), CoreError::InvalidArgument);
    }
}

#[test]
fn media_uri_is_local_absolute_and_utf8_without_null_or_uri_ambiguity() {
    let request = parse_request(br#"{"schema_version":0,"operation":"probe","source":{"uri":"file:///tmp/a%20%3B%24%28%29%20%E7%89%87%E6%AE%B5.mp4"}}"#).unwrap();
    assert_eq!(
        request,
        Request::Media(MediaRequest::Probe {
            source: "/tmp/a ;$() 片段.mp4".into()
        })
    );
    let uri = file_uri(Path::new("/tmp/空格 ?#%;'$().mp4")).unwrap();
    let input = serde_json::json!({"schema_version":0,"operation":"probe","source":{"uri":uri}});
    assert_eq!(
        parse_request(input.to_string().as_bytes()).unwrap(),
        Request::Media(MediaRequest::Probe {
            source: "/tmp/空格 ?#%;'$().mp4".into()
        })
    );
    for uri in [
        "https://example.test/a.mp4",
        "file://host/tmp/a.mp4",
        "file:relative",
        "file:///tmp/a?query",
        "file:///tmp/a#fragment",
        "file:///tmp/%00.mp4",
        "file:///tmp/%xx",
        "file:///tmp/%FF",
    ] {
        let input =
            serde_json::json!({"schema_version":0,"operation":"probe","source":{"uri":uri}});
        assert!(
            parse_request(input.to_string().as_bytes()).is_err(),
            "{uri}"
        );
    }
    assert!(file_uri(Path::new("relative.mp4")).is_err());
}

#[test]
fn trim_is_strict_integer_stream_copy_and_never_accepts_fallback_or_extra_flags() {
    let base = serde_json::json!({"schema_version":0,"operation":"trim","source":{"uri":"file:///tmp/in.mp4"},"destination":{"uri":"file:///tmp/out.mp4"},"range":{"start_us":1100000,"end_us":2600000},"strategy":"stream_copy","stream_selection":"all_supported","allow_reencode_fallback":false});
    assert!(matches!(
        parse_request(base.to_string().as_bytes()).unwrap(),
        Request::Media(MediaRequest::Trim {
            start_us: 1100000,
            end_us: 2600000,
            ..
        })
    ));
    for (start, end) in [(0, 0), (2, 1), (-1, 4)] {
        let mut input = base.clone();
        input["range"] = serde_json::json!({"start_us":start,"end_us":end});
        assert_eq!(
            parse_request(input.to_string().as_bytes()).unwrap_err(),
            CoreError::InvalidArgument
        );
    }
    for (field, value) in [
        ("strategy", serde_json::json!("accurate_reencode")),
        ("stream_selection", serde_json::json!("video_only")),
        ("allow_reencode_fallback", serde_json::json!(true)),
        ("extra_args", serde_json::json!(["-y"])),
    ] {
        let mut input = base.clone();
        input[field] = value;
        assert!(parse_request(input.to_string().as_bytes()).is_err());
    }
    let mut input = base.clone();
    input["range"]["start_us"] = serde_json::json!(1.1);
    assert_eq!(
        parse_request(input.to_string().as_bytes()).unwrap_err(),
        CoreError::InvalidArgument
    );
    let mut input = base;
    input["source"]["headers"] = serde_json::json!("not allowed");
    assert_eq!(
        parse_request(input.to_string().as_bytes()).unwrap_err(),
        CoreError::InvalidArgument
    );
}
