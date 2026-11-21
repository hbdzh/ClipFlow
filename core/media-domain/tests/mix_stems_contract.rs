use media_domain::{MediaRequest, Request, parse_config, parse_request};
use serde_json::{Value, json};

fn request() -> Value {
    json!({"schema_version":0,"operation":"mix_stems","vocals":{"uri":"file:///tmp/vocals.wav"},"accompaniment":{"uri":"file:///tmp/accompaniment.wav"},"destination":{"uri":"file:///tmp/mix.wav"},"include_vocals":true,"include_accompaniment":true})
}
#[test]
fn mix_request_requires_frozen_includes_and_local_paths() {
    let value = request();
    assert!(matches!(
        parse_request(value.to_string().as_bytes()),
        Ok(Request::Media(MediaRequest::MixStems {
            include_vocals: true,
            include_accompaniment: true,
            ..
        }))
    ));
    for (key, bad) in [
        ("include_vocals", json!("true")),
        ("destination", json!({"uri":"https://example.com/a"})),
        ("unexpected", json!(1)),
    ] {
        let mut v = value.clone();
        v[key] = bad;
        assert!(parse_request(v.to_string().as_bytes()).is_err(), "{key}");
    }
    let mut v = value.clone();
    v["include_vocals"] = json!(false);
    v["include_accompaniment"] = json!(false);
    assert!(parse_request(v.to_string().as_bytes()).is_err());
    let mut v = value;
    v.as_object_mut().unwrap().remove("include_vocals");
    assert!(parse_request(v.to_string().as_bytes()).is_err());
}
#[test]
fn mix_backend_is_strict_and_default_off() {
    let mut config = json!({"schema_version":0,"media_backend":{"ffmpeg":"/ffmpeg","ffprobe":"/ffprobe","core003_enabled":true,"core004_enabled":true}});
    let parse = |v: &Value| parse_config(v.to_string().as_bytes());
    assert!(
        !parse(&config)
            .unwrap()
            .media_backend
            .unwrap()
            .mix_stems_enabled
    );
    config["media_backend"]["mix_stems_enabled"] = json!(true);
    assert!(
        parse(&config)
            .unwrap()
            .media_backend
            .unwrap()
            .mix_stems_enabled
    );
    config["media_backend"]["core004_enabled"] = json!(false);
    assert!(parse(&config).is_err());
    config["media_backend"]["mix_stems_enabled"] = json!("true");
    assert!(parse(&config).is_err());
}
