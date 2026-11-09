//! Explicit lossy P01 trimming. No smart-cut, codec fallback, or UI decisions.
use crate::files::{self, Source, Temporary};
use crate::media::{self, Media, integer};
use crate::process::{Context, args};
use crate::quality;
use crate::scope;
use crate::time::{Rational, seconds_arg};
use media_domain::{MAX_MEDIA_RESULT_BYTES, MediaError};
use serde_json::{Value, json};
use std::path::Path;

fn invalid() -> MediaError {
    MediaError::new(
        "INVALID_INPUT",
        "Accurate interval must contain source frames inside the normalized video timeline.",
    )
}
fn validation() -> MediaError {
    MediaError::new(
        "OUTPUT_VALIDATION_FAILED",
        "Reencoded media layout, frame sequence or common source-relative timing changed.",
    )
}

pub(crate) fn encoders(context: &Context<'_>) -> Result<(), MediaError> {
    let output = context.run(
        &context.config.ffmpeg,
        &args(&["-hide_banner", "-encoders"]),
    )?;
    let text = std::str::from_utf8(&output)
        .map_err(|_| MediaError::new("DEPENDENCY_MISSING", "Encoder list is unreadable."))?;
    let has = |name: &str, kind: char| {
        text.lines().any(|line| {
            let mut fields = line.split_whitespace();
            let flags = fields.next();
            let codec = fields.next();
            flags.is_some_and(|s| s.len() == 6 && s.starts_with(kind)) && codec == Some(name)
        })
    };
    if !has("libx264", 'V') || !has("aac", 'A') {
        return Err(MediaError::new(
            "DEPENDENCY_MISSING",
            "The fixed software libx264 and AAC encoders are required; no fallback is allowed.",
        ));
    }
    Ok(())
}

struct Selection {
    start: usize,
    end: usize,
    start_pts: i64,
    end_pts: i64,
    start_us: i64,
    end_us: i64,
    source_start_us: i64,
    source_requested_end_us: i64,
}

fn compare_us(
    pts: i64,
    origin: i64,
    time: Rational,
    us: i64,
) -> Result<std::cmp::Ordering, MediaError> {
    let ticks = i128::from(pts)
        .checked_sub(i128::from(origin))
        .ok_or_else(invalid)?;
    let left = ticks
        .checked_mul(i128::from(time.numerator))
        .and_then(|n| n.checked_mul(1_000_000))
        .ok_or_else(invalid)?;
    let right = i128::from(us)
        .checked_mul(i128::from(time.denominator))
        .ok_or_else(invalid)?;
    Ok(left.cmp(&right))
}

fn select(media: &Media, start_us: i64, end_us: i64) -> Result<Selection, MediaError> {
    if start_us < 0 || end_us <= start_us || end_us > media.duration_us {
        return Err(invalid());
    }
    let origin = media.frames.first().ok_or_else(invalid)?.pts;
    let mut start = None;
    let mut end = media.frames.len();
    for (index, frame) in media.frames.iter().enumerate() {
        if compare_us(frame.pts, origin, media.time_base, start_us)?.is_ge() && start.is_none() {
            start = Some(index);
        }
        if compare_us(frame.pts, origin, media.time_base, end_us)?.is_ge() {
            end = index;
            break;
        }
    }
    let start = start.filter(|index| *index < end).ok_or_else(invalid)?;
    let first = media.frames[start].pts;
    let last = &media.frames[end - 1];
    let end_pts = last.pts.checked_add(last.duration).ok_or_else(invalid)?;
    let normalized_start = first.checked_sub(origin).ok_or_else(invalid)?;
    let normalized_end = end_pts.checked_sub(origin).ok_or_else(invalid)?;
    let source_start_us = media.time_base.micros(first)?;
    // Very remote epochs cannot be represented by FFmpeg's filter double
    // evaluation at sub-sample precision. Refuse instead of pretending exactness.
    if source_start_us.unsigned_abs() > 8_000_000_000_000 {
        return Err(MediaError::new(
            "RESOURCE_LIMIT",
            "Source timestamp epoch exceeds the validated filter precision bound.",
        ));
    }
    let source_requested_end_us = media.origin_us.checked_add(end_us).ok_or_else(invalid)?;
    Ok(Selection {
        start,
        end,
        start_pts: first,
        end_pts,
        start_us: media.time_base.micros(normalized_start)?,
        end_us: media.time_base.micros(normalized_end)?,
        source_start_us,
        source_requested_end_us,
    })
}

fn colour(value: &Value) -> Option<&str> {
    value
        .as_str()
        .filter(|s| !matches!(*s, "unknown" | "unspecified"))
}

fn audio_interval(stream: &Value, plan: &Selection) -> Result<(i64, i64), MediaError> {
    let time = Rational::parse(stream["time_base"].as_str().ok_or_else(validation)?)?;
    let start = time.micros(integer(&stream["start_pts"])?)?;
    let duration = time.micros(integer(&stream["duration_ts"])?)?;
    let end = start.checked_add(duration).ok_or_else(validation)?;
    let lower = start.max(plan.source_start_us);
    let upper = end.min(plan.source_requested_end_us);
    if duration <= 0 || lower >= upper {
        return Err(MediaError::new(
            "UNSUPPORTED_FORMAT",
            "A retained audio track has no supported overlap with the selected video interval.",
        ));
    }
    Ok((
        lower
            .checked_sub(plan.source_start_us)
            .ok_or_else(validation)?,
        upper
            .checked_sub(plan.source_start_us)
            .ok_or_else(validation)?,
    ))
}

fn layout(
    source: &Media,
    output: &Media,
    plan: &Selection,
) -> Result<(i64, Vec<usize>), MediaError> {
    let rate = scope::supported(output).map_err(|_| validation())?;
    let source_rate = scope::rate(source)?;
    if i128::from(rate.numerator) * i128::from(source_rate.denominator)
        != i128::from(source_rate.numerator) * i128::from(rate.denominator)
        || source.streams.len() != output.streams.len()
        || output.frames.len() != plan.end - plan.start
    {
        return Err(validation());
    }
    let mut channels = Vec::new();
    let mut max_av_error = 0_i64;
    for (before, after) in source.streams.iter().zip(&output.streams) {
        for key in ["codec_type", "codec_name", "language"] {
            if before[key] != after[key] {
                return Err(validation());
            }
        }
        if before["codec_type"] == "video" {
            for key in ["width", "height", "pix_fmt", "sample_aspect_ratio"] {
                if before[key] != after[key] {
                    return Err(validation());
                }
            }
            for key in [
                "color_range",
                "color_space",
                "color_transfer",
                "color_primaries",
            ] {
                if colour(&before[key]) != colour(&after[key]) {
                    return Err(validation());
                }
            }
        } else {
            for key in ["channels", "sample_rate", "profile"] {
                if before[key] != after[key] {
                    return Err(validation());
                }
            }
            channels
                .push(usize::try_from(integer(&before["channels"])?).map_err(|_| validation())?);
            let output_time = Rational::parse(after["time_base"].as_str().ok_or_else(validation)?)?;
            let (input_start, expected_end) = audio_interval(before, plan)?;
            let output_start = output_time.micros(integer(&after["start_pts"])?)?;
            let output_end = output_start
                .checked_add(output_time.micros(integer(&after["duration_ts"])?)?)
                .ok_or_else(validation)?;
            let video_frame = 1_000_000_i64
                .checked_mul(rate.denominator)
                .ok_or_else(validation)?
                / rate.numerator;
            let audio_frame = 1_024_000_000 / integer(&before["sample_rate"])?;
            let tolerance = 40_000.max(
                video_frame
                    .checked_add(audio_frame)
                    .ok_or_else(validation)?,
            );
            let error_start =
                i64::try_from(input_start.abs_diff(output_start)).map_err(|_| validation())?;
            let error_end =
                i64::try_from(expected_end.abs_diff(output_end)).map_err(|_| validation())?;
            if error_start > tolerance || error_end > tolerance {
                return Err(validation());
            }
            max_av_error = max_av_error.max(error_start).max(error_end);
        }
    }
    for (index, frame) in output.frames.iter().enumerate() {
        let expected = source.time_base.micros(
            source.frames[plan.start + index]
                .pts
                .checked_sub(plan.start_pts)
                .ok_or_else(validation)?,
        )?;
        let actual = output.time_base.micros(frame.pts)?;
        if actual.abs_diff(expected) > 2_000 {
            return Err(validation());
        }
    }
    Ok((max_av_error, channels))
}

pub(crate) fn execute(
    context: &Context<'_>,
    source: &Source,
    destination: &Path,
    media: Media,
    start_us: i64,
    end_us: i64,
) -> Result<Value, MediaError> {
    let rate = scope::supported(&media)?;
    encoders(context)?;
    let destination = files::destination(source, destination)?;
    let plan = select(&media, start_us, end_us)?;
    for stream in media.streams.iter().filter(|s| s["codec_type"] == "audio") {
        audio_interval(stream, &plan)?;
    }
    media::full_decode(context, &source.path)?;
    source.unchanged()?;
    let mut temporary = Temporary::new(&destination)?;
    let outcome = (|| {
        let mut filter = format!(
            "[0:{}]trim=start_frame={}:end_frame={},setpts=PTS-STARTPTS[v]",
            media.video_index, plan.start, plan.end
        );
        let mut audio_ordinal = 0;
        for stream in &media.streams {
            if stream["codec_type"] == "audio" {
                let index = integer(&stream["index"])?;
                filter.push_str(&format!(";[0:{index}]atrim=start={}:end={},asetpts=PTS-({})/1000000/TB[a{audio_ordinal}]",seconds_arg(plan.source_start_us),seconds_arg(plan.source_requested_end_us),plan.source_start_us));
                audio_ordinal += 1;
            }
        }
        let mut command = args(&[
            "-hide_banner",
            "-nostdin",
            "-n",
            "-v",
            "error",
            "-xerror",
            "-threads",
            "1",
            "-filter_complex_threads",
            "1",
            "-copyts",
            "-noautorotate",
        ]);
        command.extend(quality::input(&source.path));
        command.extend(args(&["-filter_complex", &filter]));
        audio_ordinal = 0;
        for stream in &media.streams {
            let label = if stream["codec_type"] == "video" {
                "[v]".to_owned()
            } else {
                let label = format!("[a{audio_ordinal}]");
                audio_ordinal += 1;
                label
            };
            command.extend(args(&["-map", &label]));
        }
        command.extend(args(&[
            "-map_metadata",
            "-1",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-r",
            &format!("{}/{}", rate.numerator, rate.denominator),
            "-fps_mode",
            "cfr",
            "-c:a",
            "aac",
        ]));
        let video = media
            .streams
            .iter()
            .find(|s| s["codec_type"] == "video")
            .ok_or_else(invalid)?;
        if colour(&video["color_space"]) == Some("bt709") {
            command.extend(args(&[
                "-color_range",
                "tv",
                "-colorspace",
                "bt709",
                "-color_trc",
                "bt709",
                "-color_primaries",
                "bt709",
            ]));
        }
        if let Some(language) = video["language"].as_str() {
            command.extend(args(&["-metadata:s:v:0", &format!("language={language}")]));
        }
        audio_ordinal = 0;
        for stream in &media.streams {
            if stream["codec_type"] == "audio" {
                let bitrate = if integer(&stream["channels"])? == 1 {
                    "96k"
                } else {
                    "192k"
                };
                command.extend(args(&[
                    &format!("-b:a:{audio_ordinal}"),
                    bitrate,
                    &format!("-ar:a:{audio_ordinal}"),
                    &integer(&stream["sample_rate"])?.to_string(),
                ]));
                if let Some(language) = stream["language"].as_str() {
                    command.extend(args(&[
                        &format!("-metadata:s:a:{audio_ordinal}"),
                        &format!("language={language}"),
                    ]));
                }
                audio_ordinal += 1;
            }
        }
        // The default millisecond movie clock can round delayed AAC edit starts
        // by nearly 1ms per generation. Preserve the shared source mapping at
        // microsecond resolution rather than shifting audio in the validator.
        command.extend(args(&["-movie_timescale", "1000000", "-f", "mp4"]));
        command.push(temporary.path.as_os_str().to_owned());
        context.run(&context.config.ffmpeg, &command)?;
        let output = media::inspect(context, &temporary.path)?;
        let (av_error, channels) = layout(&media, &output, &plan)?;
        let packet_count = media::validate_decode_order(context, &temporary.path, &output.streams)?;
        media::full_decode(context, &temporary.path)?;
        let video_quality = quality::video(
            context,
            &source.path,
            &temporary.path,
            plan.start,
            plan.end,
            rate,
        )?;
        let audio_quality = quality::audio(
            context,
            &source.path,
            &temporary.path,
            plan.source_start_us,
            plan.source_requested_end_us,
            &channels,
        )?;
        let result = json!({"schema_version":0,"mock_only":false,"file_created":true,"strategy":"accurate_reencode","profile":"mp4_h264_standard_v1","reencoded":true,"lossless":false,
            "requested_range_us":{"start_us":start_us,"end_us":end_us},"actual_range_us":{"start_us":plan.start_us,"end_us":plan.end_us},
            "actual_source_range_us":{"start_us":plan.source_start_us,"end_us":media.time_base.micros(plan.end_pts)?},"output_media_info":output.info,
            "accurate_plan":{"source_time_base":format!("{}/{}",media.time_base.numerator,media.time_base.denominator),"source_first_frame_index":plan.start,"source_end_frame_index_exclusive":plan.end,"actual_start_pts":plan.start_pts,"actual_end_exclusive_pts":plan.end_pts,"common_output_origin_source_pts":plan.start_pts,"video_frame_selection":"source PTS>=request start and PTS<request end, checked integer rational comparison","video_encoder":"libx264","quality_crf":23,"preset":"medium","lossless":false},
            "validation":{"all_streams_retained":true,"decode_all_streams":true,"verified_video_frames":output.frames.len(),"verified_output_packet_count":packet_count,"packet_dts_monotonic":true,"decoded_presentation_pts_monotonic":true,"video_timestamp_tolerance_us":2000,"audio_mapping_maximum_error_us":av_error,"video_quality":video_quality,"audio_bandlimited_content":audio_quality,"perceptual_av_sync_verified":false,"full_colour_fidelity_verified":false,"source_identity_unchanged":true}});
        if serde_json::to_vec(&result).map_err(|_| validation())?.len() > MAX_MEDIA_RESULT_BYTES {
            return Err(MediaError::new(
                "RESOURCE_LIMIT",
                "Accurate result exceeds its retained result budget.",
            ));
        }
        source.unchanged()?;
        context.check()?;
        temporary.commit(&destination)?;
        Ok(result)
    })();
    if outcome.is_err() {
        temporary.cleanup()?;
    }
    outcome
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn audio_overlap_keeps_short_tracks_and_rejects_empty_or_extreme_timelines() {
        let plan = Selection {
            start: 0,
            end: 1,
            start_pts: 0,
            end_pts: 1,
            start_us: 0,
            end_us: 4_000_000,
            source_start_us: 0,
            source_requested_end_us: 4_000_000,
        };
        let stream = json!({"time_base":"1/48000","start_pts":0,"duration_ts":96000});
        assert_eq!(
            audio_interval(&stream, &plan).expect("short audio"),
            (0, 2_000_000)
        );
        let stream = json!({"time_base":"1/48000","start_pts":24000,"duration_ts":96000});
        assert_eq!(
            audio_interval(&stream, &plan).expect("delayed audio"),
            (500_000, 2_500_000)
        );
        let stream = json!({"time_base":"1/48000","start_pts":240000,"duration_ts":96000});
        assert!(audio_interval(&stream, &plan).is_err());
        let stream = json!({"time_base":"1/1","start_pts":i64::MAX,"duration_ts":i64::MAX});
        assert!(audio_interval(&stream, &plan).is_err());
    }
    #[test]
    fn exact_boundary_comparison_does_not_floor_fractional_source_timestamps() {
        let time = Rational::parse("1/30000").expect("time");
        assert!(
            compare_us(33_000, 0, time, 1_100_000)
                .expect("time")
                .is_eq()
        );
        assert!(
            compare_us(33_000, 0, time, 1_100_001)
                .expect("time")
                .is_lt()
        );
        assert!(compare_us(1001, 0, time, 33_366).expect("time").is_gt());
        assert!(compare_us(1001, 0, time, 33_367).expect("time").is_lt());
        assert!(
            compare_us(
                i64::MAX,
                i64::MIN,
                Rational {
                    numerator: i64::MAX,
                    denominator: 1
                },
                i64::MAX
            )
            .is_err()
        );
    }
}
