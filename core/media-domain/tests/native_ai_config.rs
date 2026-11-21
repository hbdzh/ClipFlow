use media_domain::{CoreError, parse_config};
use serde_json::{Value, json};

fn config(ai: Value) -> Value {
    json!({"schema_version": 0, "media_backend": {
        "ffmpeg": "/ffmpeg", "ffprobe": "/ffprobe",
        "core003_enabled": true, "core004_enabled": true, "ai_backend": ai
    }})
}

fn parse(ai: Value) -> Result<media_domain::Config, CoreError> {
    parse_config(config(ai).to_string().as_bytes())
}

#[test]
fn explicit_native_and_legacy_paths_are_compatible() {
    assert!(parse(json!({"native_worker":"/worker", "native_graphs":"/graphs", "native_runtime":"/runtime", "model":"/model", "development_root":"/root"})).is_ok());
    assert!(parse(json!({"python":"/python", "worker":"/worker.py", "model":"/model", "development_root":"/root"})).is_ok());
}

#[test]
fn native_paths_must_be_complete_absolute_and_exclusive() {
    let base = json!({"native_worker":"/worker", "native_graphs":"/graphs", "native_runtime":"/runtime", "model":"/model"});
    for field in ["native_worker", "native_graphs", "native_runtime"] {
        let mut missing = base.clone();
        missing.as_object_mut().unwrap().remove(field);
        assert_eq!(parse(missing).unwrap_err(), CoreError::InvalidArgument);
        let mut relative = base.clone();
        relative[field] = json!("relative");
        assert_eq!(parse(relative).unwrap_err(), CoreError::InvalidArgument);
    }
    for (field, value) in [("python", "/python"), ("worker", "/worker.py")] {
        let mut mixed = base.clone();
        mixed[field] = json!(value);
        assert_eq!(parse(mixed).unwrap_err(), CoreError::InvalidArgument);
    }
}
