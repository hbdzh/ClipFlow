use media_domain::{CoreError, QueueCommand, Request, parse_request};
use serde_json::{Value, json};

fn item() -> Value {
    json!({"schema_version":0,"operation":"transcode","source":{"uri":"file:///tmp/source.mp4"},"destination":{"uri":"file:///tmp/output.mp4"},"profile":"mp4_h264_standard_v1"})
}

fn parse(value: Value) -> Result<Request, CoreError> {
    parse_request(value.to_string().as_bytes())
}

#[test]
fn storage_preview_is_a_distinct_strict_non_submission_command() {
    let value = json!({"schema_version":0,"operation":"storage_preview","items":[item()]});
    assert!(
        matches!(parse(value.clone()).unwrap(), Request::Queue(QueueCommand::StoragePreview { items }) if items.len() == 1)
    );
    for field in ["submission_id", "job_ids", "ready", "extra"] {
        let mut invalid = value.clone();
        invalid[field] = json!("unexpected");
        assert_eq!(parse(invalid).unwrap_err(), CoreError::InvalidArgument);
    }
    for items in [
        json!([]),
        json!([{"schema_version":0,"operation":"probe","source":{"uri":"file:///tmp/source.mp4"}}]),
        json!([{"schema_version":0,"operation":"storage_preview","items":[]}]),
    ] {
        assert_eq!(
            parse(json!({"schema_version":0,"operation":"storage_preview","items":items}))
                .unwrap_err(),
            CoreError::InvalidArgument
        );
    }
    assert_eq!(
        parse(json!({"schema_version":0,"operation":"storage_preview","items":vec![item();33]}))
            .unwrap_err(),
        CoreError::LimitExceeded
    );
}
