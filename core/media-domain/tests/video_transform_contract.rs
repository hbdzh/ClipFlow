use media_domain::{CoreError, MediaRequest, Request, VideoTransform, parse_request};
use serde_json::{Value, json};

fn request() -> Value {
    json!({"schema_version":0,"operation":"transcode","source":{"uri":"file:///tmp/a.mp4"},"destination":{"uri":"file:///tmp/b.mp4"},"profile":"mp4_h264_standard_v1","video_transform":{"rotation_degrees":90,"scale_percent":150,"offset_x":20,"offset_y":-10}})
}
fn parse(value: &Value) -> Result<Request, CoreError> {
    parse_request(value.to_string().as_bytes())
}
#[test]
fn strict_transform_contract_and_legacy_compatibility() {
    let value = request();
    assert!(matches!(
        parse(&value),
        Ok(Request::Media(MediaRequest::Transcode {
            video_transform: Some(VideoTransform {
                rotation_degrees: 90,
                scale_percent: 150,
                offset_x: 20,
                offset_y: -10
            }),
            ..
        }))
    ));
    let mut legacy = value.clone();
    legacy.as_object_mut().unwrap().remove("video_transform");
    assert!(matches!(
        parse(&legacy),
        Ok(Request::Media(MediaRequest::Transcode {
            video_transform: None,
            ..
        }))
    ));
    for (key, invalid) in [
        ("rotation_degrees", json!(45)),
        ("scale_percent", json!(201)),
        ("offset_x", json!(1)),
        ("offset_y", json!(1922)),
        ("unknown", json!(0)),
    ] {
        let mut v = value.clone();
        v["video_transform"][key] = invalid;
        assert!(parse(&v).is_err(), "{key}");
    }
    let mut missing = value;
    missing["video_transform"]
        .as_object_mut()
        .unwrap()
        .remove("offset_x");
    assert!(parse(&missing).is_err());
}
#[test]
fn backend_opt_in_requires_existing_core_capabilities() {
    let mut config = json!({"schema_version":0,"media_backend":{"ffmpeg":"/ffmpeg","ffprobe":"/ffprobe","core003_enabled":true,"core004_enabled":true}});
    let parse = |v: &Value| media_domain::parse_config(v.to_string().as_bytes());
    assert!(
        !parse(&config)
            .unwrap()
            .media_backend
            .unwrap()
            .video_transform_enabled
    );
    config["media_backend"]["video_transform_enabled"] = json!(true);
    assert!(
        parse(&config)
            .unwrap()
            .media_backend
            .unwrap()
            .video_transform_enabled
    );
    config["media_backend"]["core004_enabled"] = json!(false);
    assert!(parse(&config).is_err());
    config["media_backend"]["video_transform_enabled"] = json!("true");
    assert!(parse(&config).is_err());
}
