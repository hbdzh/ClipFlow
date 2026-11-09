use media_domain::{CoreError, MAX_JSON_BYTES, parse_config, parse_mock_request, validate_job_id};

#[test]
fn config_schema_and_strict_fields() {
    assert_eq!(
        parse_config(br#"{"schema_version":0}"#).unwrap().max_jobs,
        64
    );
    for max in [1, 256] {
        let json = format!("{{\"schema_version\":0,\"max_jobs\":{max}}}");
        assert_eq!(parse_config(json.as_bytes()).unwrap().max_jobs, max);
    }
    for input in [
        br#"{}"#.as_slice(),
        br#"{"schema_version":0.0}"#,
        br#"{"schema_version":true}"#,
        br#"{"schema_version":0,"max_jobs":0}"#,
        br#"{"schema_version":0,"max_jobs":257}"#,
        br#"{"schema_version":0,"max_jobs":1.5}"#,
        br#"{"schema_version":0,"unknown":1}"#,
        b"",
    ] {
        assert_eq!(parse_config(input).unwrap_err(), CoreError::InvalidArgument);
    }
    for input in [
        br#"{"schema_version":1}"#.as_slice(),
        br#"{"schema_version":-1}"#,
    ] {
        assert_eq!(
            parse_config(input).unwrap_err(),
            CoreError::UnsupportedSchema
        );
    }
}

#[test]
fn malformed_utf8_json_and_limits_are_distinct() {
    assert_eq!(parse_config(&[0xff]).unwrap_err(), CoreError::InvalidUtf8);
    assert_eq!(parse_config(b"{").unwrap_err(), CoreError::InvalidJson);
    assert_eq!(parse_config(b"[]").unwrap_err(), CoreError::InvalidJson);
    assert_eq!(
        parse_config(&vec![b' '; MAX_JSON_BYTES + 1]).unwrap_err(),
        CoreError::LimitExceeded
    );
    assert_eq!(validate_job_id("").unwrap_err(), CoreError::InvalidArgument);
    assert!(validate_job_id(&"x".repeat(128)).is_ok());
    assert_eq!(
        validate_job_id(&"x".repeat(129)).unwrap_err(),
        CoreError::LimitExceeded
    );
}

#[test]
fn only_explicit_mock_is_accepted_and_duplicates_use_last_value() {
    assert!(parse_mock_request(br#"{"schema_version":0,"operation":"mock"}"#).is_ok());
    assert!(
        parse_mock_request(br#"{"schema_version":0,"operation":"mock","outcome":"fail"}"#).is_ok()
    );
    assert!(parse_config(br#"{"schema_version":99,"schema_version":0}"#).is_ok());
    for operation in ["probe", "trim", "transcode", "unknown"] {
        let request = format!(
            "{{\"schema_version\":0,\"operation\":\"{operation}\",\"source\":\"ignored\"}}"
        );
        assert_eq!(
            parse_mock_request(request.as_bytes()).unwrap_err(),
            CoreError::UnsupportedOperation
        );
    }
    for input in [
        br#"{"schema_version":0,"operation":"mock","outcome":"other"}"#.as_slice(),
        br#"{"schema_version":0,"operation":"mock","extra":0}"#,
    ] {
        assert_eq!(
            parse_mock_request(input).unwrap_err(),
            CoreError::InvalidArgument
        );
    }
}

#[test]
fn error_codes_are_compatible() {
    let errors = [
        CoreError::InvalidArgument,
        CoreError::InvalidHandle,
        CoreError::InvalidUtf8,
        CoreError::UnsupportedSchema,
        CoreError::InvalidJson,
        CoreError::NotFound,
        CoreError::Busy,
        CoreError::UnsupportedOperation,
        CoreError::LimitExceeded,
        CoreError::Internal,
    ];
    assert_eq!(
        errors.map(CoreError::code),
        [-1, -2, -3, -4, -5, -6, -7, -8, -9, -100]
    );
}
