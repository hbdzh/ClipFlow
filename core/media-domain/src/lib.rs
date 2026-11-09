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
    pub accurate_reencode_timeout_ms: u64,
    pub core003_enabled: bool,
    pub core004_enabled: bool,
    pub transcode_timeout_ms: u64,
    pub max_output_bytes: usize,
}

pub const ACCURATE_REENCODE_PROFILE: &str = "mp4_h264_standard_v1";
pub const STREAM_COPY_V1_PROFILE: &str = "mp4_h264_copy_v1";

pub const SMALL_720_PROFILE: &str = "mp4_h264_720p_v1";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct MediaRange {
    pub start_us: i64,
    pub end_us: i64,
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TranscodeProfile {
    Standard,
    Small720,
}
impl TranscodeProfile {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Standard => ACCURATE_REENCODE_PROFILE,
            Self::Small720 => SMALL_720_PROFILE,
        }
    }
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AudioSelection {
    All,
    One { stream_index: u32 },
    None,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum QueueCommand {
    BatchSubmit {
        submission_id: String,
        items: Vec<MediaRequest>,
    },
    Snapshot,
    Reorder {
        job_ids: Vec<String>,
    },
    Retry {
        job_id: String,
        new_destination: PathBuf,
        submission_id: String,
    },
    ClearHistory,
}

/// Validated strategy, never a caller-supplied codec/filter argument.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TrimStrategy {
    StreamCopy,
    StreamCopyV1,
    AccurateReencode,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum MediaRequest {
    Transcode {
        source: PathBuf,
        destination: PathBuf,
        range: Option<MediaRange>,
        profile: TranscodeProfile,
        audio_selection: AudioSelection,
    },
    Probe {
        source: PathBuf,
    },
    Trim {
        source: PathBuf,
        destination: PathBuf,
        start_us: i64,
        end_us: i64,
        strategy: TrimStrategy,
    },
}

impl MediaRequest {
    pub const fn operation(&self) -> &'static str {
        match self {
            Self::Probe { .. } => "probe",
            Self::Transcode { .. } => "transcode",
            Self::Trim { .. } => "trim",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum Request {
    Mock(MockOutcome),
    Media(MediaRequest),
    Queue(QueueCommand),
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
        &[
            "ffmpeg",
            "ffprobe",
            "timeout_ms",
            "accurate_reencode_timeout_ms",
            "core003_enabled",
            "core004_enabled",
            "transcode_timeout_ms",
            "max_output_bytes",
        ],
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
    let accurate_reencode_timeout_ms = match map.get("accurate_reencode_timeout_ms") {
        None => 600_000,
        Some(value) => value.as_u64().ok_or(CoreError::InvalidArgument)?,
    };
    let core003_enabled = match map.get("core003_enabled") {
        None => false,
        Some(value) => value.as_bool().ok_or(CoreError::InvalidArgument)?,
    };
    let core004_enabled = match map.get("core004_enabled") {
        None => false,
        Some(v) => v.as_bool().ok_or(CoreError::InvalidArgument)?,
    };
    let transcode_timeout_ms = match map.get("transcode_timeout_ms") {
        None => 600_000,
        Some(v) => v.as_u64().ok_or(CoreError::InvalidArgument)?,
    };
    if core004_enabled && !core003_enabled {
        return Err(CoreError::InvalidArgument);
    }
    if !(1..=7_200_000).contains(&transcode_timeout_ms)
        || !(1..=300_000).contains(&timeout_ms)
        || !(1..=7_200_000).contains(&accurate_reencode_timeout_ms)
        || !(1024..=64 * 1024 * 1024).contains(&max_output_bytes)
    {
        return Err(CoreError::InvalidArgument);
    }
    Ok(BackendConfig {
        ffmpeg: executable("ffmpeg")?,
        ffprobe: executable("ffprobe")?,
        timeout_ms,
        accurate_reencode_timeout_ms,
        core003_enabled,
        core004_enabled,
        transcode_timeout_ms,
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
        "transcode" => parse_transcode(&map).map(Request::Media),
        "batch_submit" => {
            allowed_fields(
                &map,
                &["schema_version", "operation", "submission_id", "items"],
            )?;
            let submission_id = parse_submission_id(map.get("submission_id"))?;
            let items = map
                .get("items")
                .and_then(Value::as_array)
                .ok_or(CoreError::InvalidArgument)?;
            if items.is_empty() {
                return Err(CoreError::InvalidArgument);
            }
            if items.len() > 32 {
                return Err(CoreError::LimitExceeded);
            }
            let mut parsed = Vec::with_capacity(items.len());
            for item in items {
                let bytes = serde_json::to_vec(item).map_err(|_| CoreError::Internal)?;
                match parse_request(&bytes)? {
                    Request::Media(
                        request @ (MediaRequest::Trim { .. } | MediaRequest::Transcode { .. }),
                    ) => parsed.push(request),
                    _ => return Err(CoreError::InvalidArgument),
                }
            }
            Ok(Request::Queue(QueueCommand::BatchSubmit {
                submission_id,
                items: parsed,
            }))
        }
        "queue_snapshot" => {
            allowed_fields(&map, &["schema_version", "operation"])?;
            Ok(Request::Queue(QueueCommand::Snapshot))
        }
        "history_clear" => {
            allowed_fields(&map, &["schema_version", "operation"])?;
            Ok(Request::Queue(QueueCommand::ClearHistory))
        }
        "queue_reorder" => {
            allowed_fields(&map, &["schema_version", "operation", "job_ids"])?;
            let values = map
                .get("job_ids")
                .and_then(Value::as_array)
                .ok_or(CoreError::InvalidArgument)?;
            if values.len() > 32 {
                return Err(CoreError::InvalidArgument);
            }
            let mut job_ids = Vec::new();
            for v in values {
                let id = v.as_str().ok_or(CoreError::InvalidArgument)?;
                validate_job_id(id)?;
                if job_ids.iter().any(|old| old == id) {
                    return Err(CoreError::InvalidArgument);
                }
                job_ids.push(id.to_owned());
            }
            Ok(Request::Queue(QueueCommand::Reorder { job_ids }))
        }
        "job_retry" => {
            allowed_fields(
                &map,
                &[
                    "schema_version",
                    "operation",
                    "job_id",
                    "new_destination",
                    "submission_id",
                ],
            )?;
            let id = map
                .get("job_id")
                .and_then(Value::as_str)
                .ok_or(CoreError::InvalidArgument)?;
            validate_job_id(id)?;
            Ok(Request::Queue(QueueCommand::Retry {
                job_id: id.to_owned(),
                new_destination: uri_path(
                    map.get("new_destination")
                        .ok_or(CoreError::InvalidArgument)?,
                )?,
                submission_id: parse_submission_id(map.get("submission_id"))?,
            }))
        }
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
                    "profile",
                    "stream_selection",
                    "allow_reencode_fallback",
                ],
            )?;
            if map.get("stream_selection").and_then(Value::as_str) != Some("all_supported")
                || map.get("allow_reencode_fallback").and_then(Value::as_bool) != Some(false)
            {
                return Err(CoreError::InvalidArgument);
            }
            let strategy = match map.get("strategy").and_then(Value::as_str) {
                Some("stream_copy") => match map.get("profile") {
                    None => TrimStrategy::StreamCopy,
                    Some(Value::String(profile)) if profile == STREAM_COPY_V1_PROFILE => {
                        TrimStrategy::StreamCopyV1
                    }
                    _ => return Err(CoreError::InvalidArgument),
                },
                Some("accurate_reencode")
                    if map.get("profile").and_then(Value::as_str)
                        == Some(ACCURATE_REENCODE_PROFILE) =>
                {
                    TrimStrategy::AccurateReencode
                }
                _ => return Err(CoreError::InvalidArgument),
            };
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
                strategy,
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

fn parse_submission_id(value: Option<&Value>) -> Result<String, CoreError> {
    let id = value
        .and_then(Value::as_str)
        .ok_or(CoreError::InvalidArgument)?;
    if id.is_empty()
        || id.len() > 64
        || !id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
    {
        return Err(CoreError::InvalidArgument);
    }
    Ok(id.to_owned())
}
fn parse_transcode(map: &Map<String, Value>) -> Result<MediaRequest, CoreError> {
    allowed_fields(
        map,
        &[
            "schema_version",
            "operation",
            "source",
            "destination",
            "range",
            "profile",
            "audio_selection",
        ],
    )?;
    let profile = match map.get("profile").and_then(Value::as_str) {
        Some(ACCURATE_REENCODE_PROFILE) => TranscodeProfile::Standard,
        Some(SMALL_720_PROFILE) => TranscodeProfile::Small720,
        _ => return Err(CoreError::InvalidArgument),
    };
    let default_audio = serde_json::json!({"mode":"all_supported"});
    let audio = map
        .get("audio_selection")
        .unwrap_or(&default_audio)
        .as_object()
        .ok_or(CoreError::InvalidArgument)?;
    allowed_fields(audio, &["mode", "stream_index"])?;
    let audio_selection = match audio.get("mode").and_then(Value::as_str) {
        Some("all_supported") if !audio.contains_key("stream_index") => AudioSelection::All,
        Some("none") if !audio.contains_key("stream_index") => AudioSelection::None,
        Some("one") => AudioSelection::One {
            stream_index: audio
                .get("stream_index")
                .and_then(Value::as_u64)
                .and_then(|v| u32::try_from(v).ok())
                .ok_or(CoreError::InvalidArgument)?,
        },
        _ => return Err(CoreError::InvalidArgument),
    };
    let range = map
        .get("range")
        .map(|v| -> Result<MediaRange, CoreError> {
            let r = v.as_object().ok_or(CoreError::InvalidArgument)?;
            allowed_fields(r, &["start_us", "end_us"])?;
            let start_us = r
                .get("start_us")
                .and_then(Value::as_i64)
                .ok_or(CoreError::InvalidArgument)?;
            let end_us = r
                .get("end_us")
                .and_then(Value::as_i64)
                .ok_or(CoreError::InvalidArgument)?;
            if start_us < 0 || end_us <= start_us {
                return Err(CoreError::InvalidArgument);
            }
            Ok(MediaRange { start_us, end_us })
        })
        .transpose()?;
    Ok(MediaRequest::Transcode {
        source: uri_path(map.get("source").ok_or(CoreError::InvalidArgument)?)?,
        destination: uri_path(map.get("destination").ok_or(CoreError::InvalidArgument)?)?,
        range,
        profile,
        audio_selection,
    })
}

/// Frozen P01/P02 geometry in the supported source envelope. The tuple is
/// (content width, content height, even canvas width, even canvas height).
/// Checked integer arithmetic makes half-up rounding deterministic for clients.
pub fn transcode_geometry(
    w: i64,
    h: i64,
    p: TranscodeProfile,
) -> Result<(i64, i64, i64, i64), CoreError> {
    if w < 2 || h < 2 || w % 2 != 0 || h % 2 != 0 || w.max(h) > 1920 || w.min(h) > 1080 {
        return Err(CoreError::InvalidArgument);
    }
    if p == TranscodeProfile::Standard {
        return Ok((w, h, w, h));
    }
    let (bw, bh) = if w >= h { (1280, 720) } else { (720, 1280) };
    let (n, d) = if w <= bw && h <= bh {
        (1, 1)
    } else if bw * h <= bh * w {
        (bw, w)
    } else {
        (bh, h)
    };
    let x = ((w * n * 2 + d) / (2 * d)).max(2);
    let y = ((h * n * 2 + d) / (2 * d)).max(2);
    Ok((x, y, (x + 1) / 2 * 2, (y + 1) / 2 * 2))
}
