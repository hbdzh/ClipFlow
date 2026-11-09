use crate::process::{Context, args};
use crate::time::Rational;
use media_domain::MediaError;
use serde_json::{Value, json};
use std::collections::{BTreeMap, HashMap};
use std::ffi::OsString;
use std::path::Path;

#[derive(Clone)]
pub(crate) struct Frame {
    pub pts: i64,
    pub duration: i64,
    pub key: bool,
}

#[derive(Clone)]
pub(crate) struct Media {
    pub info: Value,
    pub streams: Vec<Value>,
    pub frames: Vec<Frame>,
    pub time_base: Rational,
    pub packets: Vec<Value>,
    pub video_index: i64,
    pub origin_us: i64,
    pub duration_us: i64,
    pub trim_display_metadata_supported: bool,
    pub core003_display_metadata_supported: bool,
}

fn invalid() -> MediaError {
    MediaError::new(
        "INVALID_MEDIA",
        "Media metadata or timestamps are not supported.",
    )
}

pub(crate) fn integer(value: &Value) -> Result<i64, MediaError> {
    value
        .as_i64()
        .or_else(|| value.as_str().and_then(|s| s.parse().ok()))
        .ok_or_else(invalid)
}

fn probe_json(context: &Context<'_>, path: &Path, options: &[&str]) -> Result<Value, MediaError> {
    let mut command = args(&[
        "-v",
        "error",
        "-protocol_whitelist",
        "file",
        "-format_whitelist",
        "mov",
        "-enable_drefs",
        "0",
        "-use_absolute_path",
        "0",
    ]);
    command.extend(args(options));
    command.extend(args(&["-of", "json"]));
    command.push(path.as_os_str().to_owned());
    let bytes = context
        .run(&context.config.ffprobe, &command)
        .map_err(|e| {
            if e.code == "PROCESS_FAILED" {
                MediaError::new(
                    "INVALID_MEDIA",
                    "Media container or bitstream cannot be read.",
                )
            } else {
                e
            }
        })?;
    serde_json::from_slice(&bytes).map_err(|_| invalid())
}

pub(crate) fn inspect(context: &Context<'_>, path: &Path) -> Result<Media, MediaError> {
    let raw = probe_json(context, path, &["-show_format", "-show_streams"])?;
    let raw_streams = raw["streams"].as_array().ok_or_else(invalid)?;
    if raw_streams.is_empty() {
        return Err(invalid());
    }
    if raw_streams.len() > 16 {
        return Err(MediaError::new(
            "RESOURCE_LIMIT",
            "Media stream count exceeds the bounded backend scope.",
        ));
    }
    let container = raw["format"]["format_name"].as_str().ok_or_else(invalid)?;
    if !container.split(',').any(|name| name == "mp4") {
        return Err(MediaError::new(
            "UNSUPPORTED_FORMAT",
            "This backend only accepts the MP4 MOV demuxer scope.",
        ));
    }
    let mut streams = Vec::new();
    for raw_stream in raw_streams {
        let mut stream = serde_json::Map::new();
        for key in [
            "index",
            "codec_type",
            "codec_name",
            "profile",
            "r_frame_rate",
            "avg_frame_rate",
            "field_order",
            "time_base",
            "start_pts",
            "duration_ts",
            "sample_rate",
            "channels",
            "width",
            "height",
            "pix_fmt",
            "has_b_frames",
            "sample_aspect_ratio",
            "color_range",
            "color_space",
            "color_transfer",
            "color_primaries",
            "chroma_location",
        ] {
            if let Some(value) = raw_stream.get(key) {
                if value.as_str().is_some_and(|s| s.len() > 128) {
                    return Err(invalid());
                }
                stream.insert(key.to_owned(), value.clone());
            }
        }
        if let Some(language) = raw_stream["tags"]["language"].as_str() {
            if language.len() > 32 {
                return Err(invalid());
            }
            stream.insert("language".to_owned(), json!(language));
        }
        streams.push(Value::Object(stream));
    }
    let videos = raw_streams
        .iter()
        .filter(|s| s["codec_type"] == "video")
        .collect::<Vec<_>>();
    if videos.len() != 1 {
        return Err(MediaError::new(
            "UNSUPPORTED_FORMAT",
            "This backend requires exactly one MP4 video stream.",
        ));
    }
    let video = videos[0];
    // Rotation/display matrices and explicitly signalled colour/HDR metadata
    // need their own golden output acceptance. Probe reports them, but this
    // bounded trim adapter must not silently discard or reinterpret them.
    let trim_display_metadata_supported = video["tags"].get("rotate").is_none()
        && video["side_data_list"]
            .as_array()
            .is_none_or(|data| data.is_empty())
        && [
            "color_range",
            "color_space",
            "color_transfer",
            "color_primaries",
        ]
        .iter()
        .all(|key| {
            video[*key]
                .as_str()
                .is_none_or(|value| matches!(value, "unknown" | "unspecified"))
        });
    let no_display_side_data = video["tags"].get("rotate").is_none()
        && video["side_data_list"]
            .as_array()
            .is_none_or(|data| data.is_empty());
    let no_colour = [
        "color_range",
        "color_space",
        "color_transfer",
        "color_primaries",
    ]
    .iter()
    .all(|key| {
        video[*key]
            .as_str()
            .is_none_or(|v| matches!(v, "unknown" | "unspecified"))
    });
    let bt709 = video["color_range"] == "tv"
        && video["color_space"] == "bt709"
        && video["color_transfer"] == "bt709"
        && video["color_primaries"] == "bt709";
    let core003_display_metadata_supported = no_display_side_data && (no_colour || bt709);
    let video_index = integer(&video["index"])?;
    let time_base = Rational::parse(video["time_base"].as_str().ok_or_else(invalid)?)?;
    let raw_frames = probe_json(
        context,
        path,
        &[
            "-select_streams",
            "v:0",
            "-show_frames",
            "-show_entries",
            "frame=best_effort_timestamp,pkt_duration,duration,key_frame,pict_type",
        ],
    )?;
    let raw_frames = raw_frames["frames"].as_array().ok_or_else(invalid)?;
    if raw_frames.is_empty() {
        return Err(invalid());
    }
    if raw_frames.len() > 100_000 {
        return Err(MediaError::new(
            "RESOURCE_LIMIT",
            "Video frame metadata exceeds the bounded backend scope.",
        ));
    }
    let mut frames = Vec::with_capacity(raw_frames.len());
    for raw_frame in raw_frames {
        let pts = integer(&raw_frame["best_effort_timestamp"])?;
        let duration = integer(
            raw_frame
                .get("duration")
                .or_else(|| raw_frame.get("pkt_duration"))
                .ok_or_else(invalid)?,
        )?;
        if duration <= 0 || frames.last().is_some_and(|last: &Frame| last.pts >= pts) {
            return Err(invalid());
        }
        frames.push(Frame {
            pts,
            duration,
            key: integer(&raw_frame["key_frame"])? == 1,
        });
    }
    let first = frames.first().ok_or_else(invalid)?;
    let last = frames.last().ok_or_else(invalid)?;
    let origin_us = time_base.micros(first.pts)?;
    let end_us = time_base.micros(last.pts.checked_add(last.duration).ok_or_else(invalid)?)?;
    let duration_us = end_us.checked_sub(origin_us).ok_or_else(invalid)?;
    if duration_us <= 0 {
        return Err(invalid());
    }
    let info = json!({
        "container": container,
        "video_origin_us": origin_us,
        "duration_us": duration_us,
        "raw_format_start_time": raw["format"]["start_time"].as_str(),
        "video_frame_count": frames.len(),
        "trim_display_metadata_supported":trim_display_metadata_supported,
        "core003_display_metadata_supported":core003_display_metadata_supported,
        "streams": streams,
        "limits": {"maximum_streams":16,"maximum_frames":100000,"input_protocol":"file","demuxer":"mov"}
    });
    Ok(Media {
        info,
        streams,
        frames,
        time_base,
        packets: Vec::new(),
        video_index,
        origin_us,
        duration_us,
        trim_display_metadata_supported,
        core003_display_metadata_supported,
    })
}

pub(crate) fn packet_info(context: &Context<'_>, path: &Path) -> Result<Vec<Value>, MediaError> {
    let raw = probe_json(
        context,
        path,
        &[
            "-show_packets",
            "-show_data_hash",
            "sha256",
            "-show_entries",
            "packet=stream_index,pts,dts,duration,flags,data_hash",
        ],
    )?;
    let packets = raw["packets"].as_array().ok_or_else(invalid)?.clone();
    let mut last_dts = BTreeMap::new();
    for packet in &packets {
        let stream = integer(&packet["stream_index"])?;
        let dts = integer(&packet["dts"])?;
        if last_dts
            .insert(stream, dts)
            .is_some_and(|previous| previous >= dts)
        {
            return Err(MediaError::new(
                "OUTPUT_VALIDATION_FAILED",
                "Packet decode timestamps are not strictly increasing per stream.",
            ));
        }
    }
    Ok(packets)
}

/// Reencoding changes compressed payloads; only decode-order metadata is needed
/// here. Compact records avoid filling the bounded pipe with unused SHA hashes.
pub(crate) fn validate_decode_order(
    context: &Context<'_>,
    path: &Path,
    streams: &[Value],
) -> Result<usize, MediaError> {
    let mut command = args(&[
        "-v",
        "error",
        "-protocol_whitelist",
        "file",
        "-format_whitelist",
        "mov",
        "-enable_drefs",
        "0",
        "-use_absolute_path",
        "0",
        "-show_packets",
        "-show_entries",
        "packet=stream_index,dts",
        "-of",
        "compact=p=0:nk=0",
    ]);
    command.push(path.as_os_str().to_owned());
    let output = context.run(&context.config.ffprobe, &command)?;
    let text = std::str::from_utf8(&output).map_err(|_| invalid())?;
    let expected = streams
        .iter()
        .map(|s| integer(&s["index"]))
        .collect::<Result<Vec<_>, _>>()?;
    decode_order_text(text, &expected)
}

fn decode_order_text(text: &str, streams: &[i64]) -> Result<usize, MediaError> {
    let failed = || {
        MediaError::new(
            "OUTPUT_VALIDATION_FAILED",
            "Output packet decode timestamps are missing or not increasing per stream.",
        )
    };
    let mut last = BTreeMap::new();
    let mut count = 0_usize;
    for line in text.lines().filter(|line| !line.is_empty()) {
        let fields = line
            .split('|')
            .filter_map(|v| v.split_once('='))
            .collect::<BTreeMap<_, _>>();
        let index = fields
            .get("stream_index")
            .and_then(|v| v.parse::<i64>().ok())
            .ok_or_else(failed)?;
        let dts = fields
            .get("dts")
            .and_then(|v| v.parse::<i64>().ok())
            .ok_or_else(failed)?;
        if !streams.contains(&index)
            || last
                .insert(index, dts)
                .is_some_and(|previous| previous >= dts)
        {
            return Err(failed());
        }
        count = count.checked_add(1).ok_or_else(failed)?;
    }
    if streams.iter().any(|s| !last.contains_key(s)) {
        return Err(failed());
    }
    Ok(count)
}

pub(crate) fn frame_hashes(context: &Context<'_>, path: &Path) -> Result<Vec<String>, MediaError> {
    let mut command = args(&[
        "-hide_banner",
        "-nostdin",
        "-v",
        "error",
        "-xerror",
        "-threads",
        "1",
        "-protocol_whitelist",
        "file",
        "-format_whitelist",
        "mov",
        "-enable_drefs",
        "0",
        "-use_absolute_path",
        "0",
        "-copyts",
        "-i",
    ]);
    command.push(path.as_os_str().to_owned());
    command.extend(args(&[
        "-map",
        "0:v:0",
        "-fps_mode",
        "passthrough",
        "-f",
        "framehash",
        "-hash",
        "sha256",
        "-",
    ]));
    let output = context.run(&context.config.ffmpeg, &command)?;
    let text = std::str::from_utf8(&output).map_err(|_| invalid())?;
    let mut frames = Vec::new();
    for line in text
        .lines()
        .filter(|line| !line.is_empty() && !line.starts_with('#'))
    {
        let hash = line.rsplit(',').next().ok_or_else(invalid)?.trim();
        if hash.len() != 64 || !hash.bytes().all(|c| c.is_ascii_hexdigit()) {
            return Err(invalid());
        }
        frames.push(hash.to_owned());
    }
    if frames.is_empty() {
        return Err(invalid());
    }
    Ok(frames)
}

pub(crate) fn validate_hashes(
    source: &[String],
    output: &[String],
    expected_start: usize,
    expected_end: usize,
) -> Result<(), MediaError> {
    if expected_end <= expected_start
        || expected_end > source.len()
        || output != &source[expected_start..expected_end]
    {
        return Err(MediaError::new(
            "SEEK_NOT_EXACT",
            "Stream-copy output did not preserve the complete planned source-frame sequence.",
        ));
    }
    Ok(())
}

pub(crate) fn validate_packets(source: &[Value], output: &[Value]) -> Result<(), MediaError> {
    let mut hashes: HashMap<(i64, &str), usize> = HashMap::new();
    for packet in source {
        let key = (
            integer(&packet["stream_index"])?,
            packet["data_hash"].as_str().ok_or_else(invalid)?,
        );
        *hashes.entry(key).or_default() += 1;
    }
    for packet in output {
        let key = (
            integer(&packet["stream_index"])?,
            packet["data_hash"].as_str().ok_or_else(invalid)?,
        );
        let Some(count) = hashes.get_mut(&key) else {
            return Err(MediaError::new(
                "OUTPUT_VALIDATION_FAILED",
                "Output packet payload is not part of the corresponding source stream.",
            ));
        };
        if *count == 0 {
            return Err(MediaError::new(
                "OUTPUT_VALIDATION_FAILED",
                "Output repeated a source packet payload.",
            ));
        }
        *count -= 1;
    }
    Ok(())
}

// Packet payload membership alone cannot prove audio sync: require each output
// stream to be a contiguous source packet run with one shared mux timestamp shift.
pub(crate) fn validate_packet_timing(
    source_streams: &[Value],
    source: &[Value],
    output_streams: &[Value],
    output: &[Value],
    start_video_pts: i64,
) -> Result<(), MediaError> {
    let failed = || {
        MediaError::new(
            "OUTPUT_VALIDATION_FAILED",
            "Output packet order or source-relative timestamp continuity changed.",
        )
    };
    let time = |stream: &Value| Rational::parse(stream["time_base"].as_str().ok_or_else(failed)?);
    let video = source_streams
        .iter()
        .find(|s| s["codec_type"] == "video")
        .ok_or_else(failed)?;
    let video_index = integer(&video["index"])?;
    let first_source = source
        .iter()
        .find(|p| {
            integer(&p["stream_index"]).ok() == Some(video_index)
                && integer(&p["pts"]).ok() == Some(start_video_pts)
        })
        .ok_or_else(failed)?;
    let first_output = output
        .iter()
        .find(|p| integer(&p["stream_index"]).ok() == Some(video_index))
        .ok_or_else(failed)?;
    let output_video = output_streams
        .iter()
        .find(|s| integer(&s["index"]).ok() == Some(video_index))
        .ok_or_else(failed)?;
    let shared_shift = i128::from(time(output_video)?.micros(integer(&first_output["pts"])?)?)
        - i128::from(time(video)?.micros(integer(&first_source["pts"])?)?);
    for stream in source_streams {
        let index = integer(&stream["index"])?;
        let out_stream = output_streams
            .iter()
            .find(|s| integer(&s["index"]).ok() == Some(index))
            .ok_or_else(failed)?;
        let source_time = time(stream)?;
        let output_time = time(out_stream)?;
        let before = source
            .iter()
            .filter(|p| integer(&p["stream_index"]).ok() == Some(index))
            .collect::<Vec<_>>();
        let after = output
            .iter()
            .filter(|p| integer(&p["stream_index"]).ok() == Some(index))
            .collect::<Vec<_>>();
        if after.is_empty() || after.len() > before.len() {
            return Err(failed());
        }
        let mut found = false;
        for offset in 0..=before.len() - after.len() {
            if before[offset]["data_hash"] != after[0]["data_hash"] {
                continue;
            }
            let shift = i128::from(output_time.micros(integer(&after[0]["pts"])?)?)
                - i128::from(source_time.micros(integer(&before[offset]["pts"])?)?);
            if (shift - shared_shift).abs() > 2_000 {
                continue;
            }
            let mut valid = true;
            for (old, new) in before[offset..offset + after.len()].iter().zip(&after) {
                if old["data_hash"] != new["data_hash"] {
                    valid = false;
                    break;
                }
                for field in ["pts", "dts"] {
                    let shift = i128::from(output_time.micros(integer(&new[field])?)?)
                        - i128::from(source_time.micros(integer(&old[field])?)?);
                    if (shift - shared_shift).abs() > 2_000 {
                        valid = false;
                        break;
                    }
                }
                if !valid {
                    break;
                }
            }
            if valid {
                found = true;
                break;
            }
        }
        if !found {
            return Err(failed());
        }
    }
    Ok(())
}

pub(crate) fn full_decode(context: &Context<'_>, path: &Path) -> Result<(), MediaError> {
    let mut command: Vec<OsString> = args(&[
        "-hide_banner",
        "-nostdin",
        "-v",
        "error",
        "-xerror",
        "-threads",
        "1",
        "-protocol_whitelist",
        "file",
        "-format_whitelist",
        "mov",
        "-enable_drefs",
        "0",
        "-use_absolute_path",
        "0",
        "-i",
    ]);
    command.push(path.as_os_str().to_owned());
    command.extend(args(&["-map", "0:v?", "-map", "0:a?", "-f", "null", "-"]));
    context.run(&context.config.ffmpeg, &command)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn compact_decode_order_rejects_duplicate_missing_and_unknown_streams() {
        assert_eq!(
            decode_order_text(
                "stream_index=0|dts=-2\nstream_index=1|dts=-1024\nstream_index=0|dts=0\n",
                &[0, 1]
            )
            .expect("valid DTS"),
            3
        );
        assert!(decode_order_text("stream_index=0|dts=0\nstream_index=0|dts=0\n", &[0]).is_err());
        assert!(decode_order_text("stream_index=0|dts=0\n", &[0, 1]).is_err());
        assert!(decode_order_text("stream_index=4|dts=0\n", &[0]).is_err());
        assert!(decode_order_text("stream_index=0|dts=N/A\n", &[0]).is_err());
    }

    #[test]
    fn continuity_rejects_decodable_b_frame_holes() {
        let source = (0..100).map(|n| n.to_string()).collect::<Vec<_>>();
        let mut incorrect = source[30..79].to_vec();
        incorrect.push(source[81].clone());
        assert!(validate_hashes(&source, &incorrect, 30, 82).is_err());
        assert!(validate_hashes(&source, &source[30..90], 30, 90).is_ok());
    }

    #[test]
    fn packet_timing_rejects_audio_shift_and_missing_middle_packet() {
        let streams = vec![
            json!({"index":0,"codec_type":"video","time_base":"1/1000"}),
            json!({"index":1,"codec_type":"audio","time_base":"1/1000"}),
        ];
        let packet = |stream, pts, hash| json!({"stream_index":stream,"pts":pts,"dts":pts,"duration":20,"data_hash":hash});
        let source = vec![
            packet(0, 1000, "v"),
            packet(1, 1000, "a"),
            packet(1, 1020, "b"),
            packet(1, 1040, "c"),
        ];
        let good = vec![
            packet(0, 0, "v"),
            packet(1, 0, "a"),
            packet(1, 20, "b"),
            packet(1, 40, "c"),
        ];
        assert!(validate_packet_timing(&streams, &source, &streams, &good, 1000).is_ok());
        let shifted = vec![
            packet(0, 0, "v"),
            packet(1, 100, "a"),
            packet(1, 120, "b"),
            packet(1, 140, "c"),
        ];
        assert!(validate_packet_timing(&streams, &source, &streams, &shifted, 1000).is_err());
        let missing = vec![packet(0, 0, "v"), packet(1, 0, "a"), packet(1, 40, "c")];
        assert!(validate_packet_timing(&streams, &source, &streams, &missing, 1000).is_err());
    }
}
