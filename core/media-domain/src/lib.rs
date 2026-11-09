#![forbid(unsafe_code)]

//! Shared validation and typed mock-job inputs. No filesystem or process access.

use serde_json::{Map, Value};

pub const MAX_JSON_BYTES: usize = 65_536;
pub const MAX_JOB_ID_BYTES: usize = 128;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CoreError {
    InvalidArgument,
    InvalidHandle,
    InvalidUtf8,
    UnsupportedSchema,
    InvalidJson,
    NotFound,
    Busy,
    UnsupportedOperation,
    LimitExceeded,
    Internal,
}

impl CoreError {
    pub const fn code(self) -> i32 {
        match self {
            Self::InvalidArgument => -1,
            Self::InvalidHandle => -2,
            Self::InvalidUtf8 => -3,
            Self::UnsupportedSchema => -4,
            Self::InvalidJson => -5,
            Self::NotFound => -6,
            Self::Busy => -7,
            Self::UnsupportedOperation => -8,
            Self::LimitExceeded => -9,
            Self::Internal => -100,
        }
    }
}

impl std::fmt::Display for CoreError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "Core error {}", self.code())
    }
}

impl std::error::Error for CoreError {}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Config {
    pub max_jobs: usize,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum MockOutcome {
    Succeed,
    Fail,
}

/// Validate byte lengths before UTF-8/JSON parsing. Duplicate keys use the last
/// value, as specified by the CORE-001 contract and serde_json::Value semantics.
fn object(input: &[u8]) -> Result<Map<String, Value>, CoreError> {
    if input.is_empty() {
        return Err(CoreError::InvalidArgument);
    }
    if input.len() > MAX_JSON_BYTES {
        return Err(CoreError::LimitExceeded);
    }
    let text = std::str::from_utf8(input).map_err(|_| CoreError::InvalidUtf8)?;
    let parsed: Value = serde_json::from_str(text).map_err(|_| CoreError::InvalidJson)?;
    let Value::Object(map) = parsed else {
        return Err(CoreError::InvalidJson);
    };
    let schema = map
        .get("schema_version")
        .ok_or(CoreError::InvalidArgument)?;
    if schema.as_i64().is_none() && schema.as_u64().is_none() {
        return Err(CoreError::InvalidArgument);
    }
    if schema.as_u64() != Some(0) {
        return Err(CoreError::UnsupportedSchema);
    }
    Ok(map)
}

fn allowed_fields(map: &Map<String, Value>, names: &[&str]) -> Result<(), CoreError> {
    if map.keys().any(|key| !names.contains(&key.as_str())) {
        return Err(CoreError::InvalidArgument);
    }
    Ok(())
}

pub fn parse_config(input: &[u8]) -> Result<Config, CoreError> {
    let map = object(input)?;
    allowed_fields(&map, &["schema_version", "max_jobs"])?;
    let max_jobs = match map.get("max_jobs") {
        None => 64,
        Some(value) => value.as_u64().ok_or(CoreError::InvalidArgument)?,
    };
    if !(1..=256).contains(&max_jobs) {
        return Err(CoreError::InvalidArgument);
    }
    Ok(Config {
        max_jobs: max_jobs as usize,
    })
}

pub fn parse_mock_request(input: &[u8]) -> Result<MockOutcome, CoreError> {
    let map = object(input)?;
    let operation = map
        .get("operation")
        .and_then(Value::as_str)
        .ok_or(CoreError::InvalidArgument)?;
    // A real media request is explicitly unsupported, even if it contains fields
    // that the mock operation does not accept. No paths are inspected or used.
    if operation != "mock" {
        return Err(CoreError::UnsupportedOperation);
    }
    allowed_fields(&map, &["schema_version", "operation", "outcome"])?;
    match map.get("outcome") {
        None => Ok(MockOutcome::Succeed),
        Some(Value::String(value)) if value == "succeed" => Ok(MockOutcome::Succeed),
        Some(Value::String(value)) if value == "fail" => Ok(MockOutcome::Fail),
        _ => Err(CoreError::InvalidArgument),
    }
}

pub fn validate_job_id(id: &str) -> Result<(), CoreError> {
    if id.is_empty() {
        return Err(CoreError::InvalidArgument);
    }
    if id.len() > MAX_JOB_ID_BYTES {
        return Err(CoreError::LimitExceeded);
    }
    Ok(())
}
