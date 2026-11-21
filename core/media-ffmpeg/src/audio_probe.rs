//! Explicit CORE-003 audio-only probe; never supplies a video trim plan.
use crate::media::integer;
use crate::process::{Context, args};
use crate::time::Rational;
use media_domain::MediaError;
use serde_json::{Value, json};
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;

fn unsupported() -> MediaError {
    MediaError::new(
        "UNSUPPORTED_FORMAT",
        "Audio preview requires one supported local audio stream.",
    )
}

fn invalid() -> MediaError {
    MediaError::new(
        "INVALID_MEDIA",
        "Audio content timestamps cannot be established.",
    )
}

fn wav_layout(path: &Path, channels: i64) -> Result<(), MediaError> {
    let mut file = crate::files::open_regular_read(path)?;
    let length = file.metadata().map_err(|_| invalid())?.len();
    let mut position = 12_u64;
    for _ in 0..4096 {
        if position.checked_add(8).is_none_or(|p| p > length) {
            return Err(invalid());
        }
        file.seek(SeekFrom::Start(position))
            .map_err(|_| invalid())?;
        let mut header = [0_u8; 8];
        file.read_exact(&mut header).map_err(|_| invalid())?;
        let size = u64::from(u32::from_le_bytes(
            header[4..].try_into().map_err(|_| invalid())?,
        ));
        let body = position + 8;
        let next = body
            .checked_add(size)
            .and_then(|p| p.checked_add(size % 2))
            .ok_or_else(invalid)?;
        if next > length {
            return Err(invalid());
        }
        if &header[..4] == b"fmt " {
            if size < 16 {
                return Err(invalid());
            }
            let mut fmt = [0_u8; 40];
            let count = usize::try_from(size.min(40)).map_err(|_| invalid())?;
            file.read_exact(&mut fmt[..count]).map_err(|_| invalid())?;
            if u16::from_le_bytes([fmt[0], fmt[1]]) == 0xfffe {
                if size < 40 || u16::from_le_bytes([fmt[16], fmt[17]]) < 22 {
                    return Err(invalid());
                }
                let mask = u32::from_le_bytes(fmt[20..24].try_into().map_err(|_| invalid())?);
                if !matches!((channels, mask), (1, 0 | 4) | (2, 0 | 3)) {
                    return Err(unsupported());
                }
            }
            return Ok(());
        }
        position = next;
    }
    Err(invalid())
}

fn probe(context: &Context<'_>, path: &Path, options: &[&str]) -> Result<Value, MediaError> {
    let mut command = args(&[
        "-v",
        "error",
        "-protocol_whitelist",
        "file",
        "-format_whitelist",
        "mov,wav,flac,mp3",
        "-enable_drefs",
        "0",
        "-use_absolute_path",
        "0",
    ]);
    command.extend(args(options));
    command.extend(args(&["-of", "json"]));
    command.push(path.as_os_str().to_owned());
    let raw = context
        .run(&context.config.ffprobe, &command)
        .map_err(|e| {
            if e.code == "PROCESS_FAILED" {
                invalid()
            } else {
                e
            }
        })?;
    serde_json::from_slice(&raw).map_err(|_| invalid())
}

/// None delegates video to the unchanged video inspector.
pub(crate) fn inspect(context: &Context<'_>, path: &Path) -> Result<Option<Value>, MediaError> {
    let mut header = [0; 12];
    let mut file = crate::files::open_regular_read(path)?;
    let read = file.read(&mut header).map_err(|_| invalid())?;
    let raw_audio = read >= 4
        && (header.starts_with(b"RIFF")
            || header.starts_with(b"fLaC")
            || header.starts_with(b"ID3")
            || (header[0] == 0xff && header[1] & 0xe0 == 0xe0));
    if !raw_audio {
        crate::references::reject_external(path)?;
    }
    let raw = probe(context, path, &["-show_format", "-show_streams"])?;
    let streams = raw["streams"].as_array().ok_or_else(invalid)?;
    if streams.iter().any(|s| s["codec_type"] == "video") {
        return Ok(None);
    }
    if streams.len() != 1 || streams[0]["codec_type"] != "audio" {
        return Err(unsupported());
    }
    let stream = &streams[0];
    let container = raw["format"]["format_name"].as_str().ok_or_else(invalid)?;
    if container.split(',').any(|s| s == "mp4") && !crate::references::simple_edits(path)? {
        return Err(unsupported());
    }
    let codec = stream["codec_name"].as_str().ok_or_else(invalid)?;
    let allowed = match container {
        "wav" => matches!(codec, "pcm_s16le" | "pcm_s24le" | "pcm_f32le"),
        "flac" => codec == "flac",
        "mp3" => codec == "mp3",
        c if c.split(',').any(|s| s == "mp4") => codec == "aac" && stream["profile"] == "LC",
        _ => false,
    };
    let rate = integer(&stream["sample_rate"])?;
    let channels = integer(&stream["channels"])?;
    if !allowed || !matches!(rate, 44100 | 48000) || !matches!(channels, 1 | 2) {
        return Err(unsupported());
    }
    if stream["channel_layout"]
        .as_str()
        .is_some_and(|s| s != if channels == 1 { "mono" } else { "stereo" })
    {
        return Err(unsupported());
    }
    if container == "wav" {
        wav_layout(path, channels)?;
    }
    let time = Rational::parse(stream["time_base"].as_str().ok_or_else(invalid)?)?;
    // Bound untrusted rational factors before exact i128 content arithmetic.
    if time.numerator > 1_000_000 || time.denominator > 1_000_000_000 {
        return Err(invalid());
    }
    if i128::from(time.numerator) * i128::from(rate) > i128::from(time.denominator) {
        return Err(invalid());
    }
    let frames = probe(
        context,
        path,
        &[
            "-select_streams",
            "a:0",
            "-show_frames",
            "-show_entries",
            "frame=best_effort_timestamp,nb_samples",
        ],
    )?;
    let frames = frames["frames"].as_array().ok_or_else(invalid)?;
    if frames.is_empty() || frames.len() > 100_000 {
        return Err(invalid());
    }
    let mut origin = None;
    let mut last_pts = None;
    let mut last_start = None;
    let mut end: Option<i128> = None;
    let denominator = i128::from(time.denominator) * i128::from(rate);
    for frame in frames {
        let pts = integer(&frame["best_effort_timestamp"])?;
        let samples = integer(&frame["nb_samples"])?;
        if samples <= 0 || last_pts.is_some_and(|last| pts <= last) {
            return Err(invalid());
        }
        let start = i128::from(pts) * i128::from(time.numerator) * i128::from(rate);
        let frame_end = start + i128::from(samples) * i128::from(time.denominator);
        let tick = i128::from(time.numerator) * i128::from(rate);
        if end.is_some_and(|previous| (start - previous).abs() > tick) {
            return Err(invalid());
        }
        origin.get_or_insert(start);
        end = Some(frame_end);
        last_start = Some(start);
        last_pts = Some(pts);
    }
    let origin = origin.ok_or_else(invalid)?;
    let decoded_end = end.ok_or_else(invalid)?;
    let content_end = if codec == "aac" {
        let start_pts = integer(&stream["start_pts"])?;
        let duration_ticks = integer(&stream["duration_ts"])?;
        let factor = i128::from(time.numerator) * i128::from(rate);
        let declared_start = i128::from(start_pts) * factor;
        let declared_end = (i128::from(start_pts) + i128::from(duration_ticks)) * factor;
        if duration_ticks <= 0
            || (declared_start - origin).abs() > factor
            || declared_end <= last_start.ok_or_else(invalid)?
            || declared_end > decoded_end + factor
        {
            return Err(invalid());
        }
        declared_end.min(decoded_end)
    } else {
        decoded_end
    };
    let duration = (content_end - origin) * 1_000_000 / denominator;
    if !(1_000_000..=600_000_000).contains(&duration) {
        return Err(unsupported());
    }
    let origin_us = i64::try_from(origin * 1_000_000 / denominator).map_err(|_| invalid())?;
    let mut public_stream = serde_json::Map::new();
    for key in [
        "index",
        "codec_type",
        "codec_name",
        "profile",
        "time_base",
        "start_pts",
        "duration_ts",
        "sample_rate",
        "channels",
    ] {
        if let Some(value) = stream.get(key) {
            if value.as_str().is_some_and(|s| s.len() > 128) {
                return Err(invalid());
            }
            public_stream.insert(key.to_owned(), value.clone());
        }
    }
    Ok(Some(
        json!({"media_kind":"audio", "container":container, "audio_origin_us":origin_us,
        "duration_us":i64::try_from(duration).map_err(|_| invalid())?, "streams":[public_stream],
        "sample_count":i64::try_from(((content_end-origin)*i128::from(rate)+denominator/2)/denominator).map_err(|_| invalid())?,
        "decoded_duration_us":i64::try_from((decoded_end-origin)*1_000_000/denominator).map_err(|_| invalid())?,
        "codec_tail_padding_us":i64::try_from((decoded_end-content_end)*1_000_000/denominator).map_err(|_| invalid())?,
        "limits":{"maximum_duration_us":600000000,"maximum_audio_streams":1,"input_protocol":"file","demuxers":["mov","wav","flac","mp3"]}}),
    ))
}
