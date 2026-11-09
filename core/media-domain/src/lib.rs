#![forbid(unsafe_code)]

//! Shared validation and typed job inputs. No filesystem or process access.

use serde_json::{Map, Value};
use std::path::{Path, PathBuf};

pub const MAX_JSON_BYTES: usize = 65_536;
pub const MAX_JOB_ID_BYTES: usize = 128;
pub const MAX_MEDIA_RESULT_BYTES: usize = 128 * 1024;

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

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Config {
    pub max_jobs: usize,
    pub media_backend: Option<BackendConfig>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BackendConfig {
    pub ffmpeg: PathBuf,
    pub ffprobe: PathBuf,
    pub timeout_ms: u64,
    pub max_output_bytes: usize,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum MediaRequest {
    Probe {
        source: PathBuf,
    },
    Trim {
        source: PathBuf,
        destination: PathBuf,
        start_us: i64,
        end_us: i64,
    },
}

impl MediaRequest {
    pub const fn operation(&self) -> &'static str {
        match self {
            Self::Probe { .. } => "probe",
            Self::Trim { .. } => "trim",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum Request {
    Mock(MockOutcome),
    Media(MediaRequest),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MediaError {
    pub code: &'static str,
    pub message: String,
}

impl MediaError {
    pub fn new(code: &'static str, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }
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
    allowed_fields(&map, &["schema_version", "max_jobs", "media_backend"])?;
    let max_jobs = match map.get("max_jobs") {
        None => 64,
        Some(value) => value.as_u64().ok_or(CoreError::InvalidArgument)?,
    };
    if !(1..=256).contains(&max_jobs) {
        return Err(CoreError::InvalidArgument);
    }
    Ok(Config {
        max_jobs: max_jobs as usize,
        media_backend: map.get("media_backend").map(parse_backend).transpose()?,
    })
}

fn parse_backend(value: &Value) -> Result<BackendConfig, CoreError> {
    let map = value.as_object().ok_or(CoreError::InvalidArgument)?;
    allowed_fields(
        map,
        &["ffmpeg", "ffprobe", "timeout_ms", "max_output_bytes"],
    )?;
    let executable = |name: &str| -> Result<PathBuf, CoreError> {
        let text = map
            .get(name)
            .and_then(Value::as_str)
            .ok_or(CoreError::InvalidArgument)?;
        let path = PathBuf::from(text);
        if text.is_empty() || text.contains('\0') || !path.is_absolute() {
            return Err(CoreError::InvalidArgument);
        }
        Ok(path)
    };
    let timeout_ms = match map.get("timeout_ms") {
        None => 30_000,
        Some(value) => value.as_u64().ok_or(CoreError::InvalidArgument)?,
    };
    let max_output_bytes = match map.get("max_output_bytes") {
        None => 8 * 1024 * 1024,
        Some(value) => value.as_u64().ok_or(CoreError::InvalidArgument)?,
    };
    if !(1..=300_000).contains(&timeout_ms)
        || !(1024..=64 * 1024 * 1024).contains(&max_output_bytes)
    {
        return Err(CoreError::InvalidArgument);
    }
    Ok(BackendConfig {
        ffmpeg: executable("ffmpeg")?,
        ffprobe: executable("ffprobe")?,
        timeout_ms,
        max_output_bytes: max_output_bytes as usize,
    })
}

fn uri_path(value: &Value) -> Result<PathBuf, CoreError> {
    let map = value.as_object().ok_or(CoreError::InvalidArgument)?;
    allowed_fields(map, &["uri"])?;
    let uri = map
        .get("uri")
        .and_then(Value::as_str)
        .ok_or(CoreError::InvalidArgument)?;
    if !uri.starts_with("file:///") || uri.contains(['?', '#']) || uri.bytes().any(|b| b < 0x20) {
        return Err(CoreError::InvalidArgument);
    }
    let source = uri
        .strip_prefix("file://")
        .ok_or(CoreError::InvalidArgument)?
        .as_bytes();
    let mut decoded = Vec::with_capacity(source.len());
    let mut index = 0;
    while index < source.len() {
        if source[index] == b'%' {
            let hi = source
                .get(index + 1)
                .and_then(|b| char::from(*b).to_digit(16))
                .ok_or(CoreError::InvalidArgument)?;
            let lo = source
                .get(index + 2)
                .and_then(|b| char::from(*b).to_digit(16))
                .ok_or(CoreError::InvalidArgument)?;
            decoded.push((hi * 16 + lo) as u8);
            index += 3;
        } else {
            decoded.push(source[index]);
            index += 1;
        }
    }
    let text = String::from_utf8(decoded).map_err(|_| CoreError::InvalidUtf8)?;
    let path = PathBuf::from(&text);
    if text.contains('\0') || !path.is_absolute() {
        return Err(CoreError::InvalidArgument);
    }
    Ok(path)
}

pub fn file_uri(path: &Path) -> Result<String, CoreError> {
    let text = path.to_str().ok_or(CoreError::InvalidUtf8)?;
    if !path.is_absolute() || text.contains('\0') {
        return Err(CoreError::InvalidArgument);
    }
    let mut encoded = String::from("file://");
    const HEX: &[u8; 16] = b"0123456789ABCDEF";
    for byte in text.bytes() {
        if byte.is_ascii_alphanumeric() || b"/-._~".contains(&byte) {
            encoded.push(char::from(byte));
        } else {
            encoded.push('%');
            encoded.push(char::from(HEX[(byte >> 4) as usize]));
            encoded.push(char::from(HEX[(byte & 15) as usize]));
        }
    }
    Ok(encoded)
}

pub fn parse_request(input: &[u8]) -> Result<Request, CoreError> {
    let map = object(input)?;
    match map
        .get("operation")
        .and_then(Value::as_str)
        .ok_or(CoreError::InvalidArgument)?
    {
        "mock" => parse_mock_request(input).map(Request::Mock),
        "probe" => {
            allowed_fields(&map, &["schema_version", "operation", "source"])?;
            Ok(Request::Media(MediaRequest::Probe {
                source: uri_path(map.get("source").ok_or(CoreError::InvalidArgument)?)?,
            }))
        }
        "trim" => {
            allowed_fields(
                &map,
                &[
                    "schema_version",
                    "operation",
                    "source",
                    "destination",
                    "range",
                    "strategy",
                    "stream_selection",
                    "allow_reencode_fallback",
                ],
            )?;
            if map.get("strategy").and_then(Value::as_str) != Some("stream_copy")
                || map.get("stream_selection").and_then(Value::as_str) != Some("all_supported")
                || map.get("allow_reencode_fallback").and_then(Value::as_bool) != Some(false)
            {
                return Err(CoreError::InvalidArgument);
            }
            let range = map
                .get("range")
                .and_then(Value::as_object)
                .ok_or(CoreError::InvalidArgument)?;
            allowed_fields(range, &["start_us", "end_us"])?;
            let start_us = range
                .get("start_us")
                .and_then(Value::as_i64)
                .ok_or(CoreError::InvalidArgument)?;
            let end_us = range
                .get("end_us")
                .and_then(Value::as_i64)
                .ok_or(CoreError::InvalidArgument)?;
            if start_us < 0 || end_us <= start_us {
                return Err(CoreError::InvalidArgument);
            }
            Ok(Request::Media(MediaRequest::Trim {
                source: uri_path(map.get("source").ok_or(CoreError::InvalidArgument)?)?,
                destination: uri_path(map.get("destination").ok_or(CoreError::InvalidArgument)?)?,
                start_us,
                end_us,
            }))
        }
        _ => Err(CoreError::UnsupportedOperation),
    }
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
