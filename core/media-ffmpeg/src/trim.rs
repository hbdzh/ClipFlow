use crate::files::{self, Source, Temporary};
use crate::media::{self, Media, integer};
use crate::process::{Context, args};
use crate::time::{Rational, seconds_arg};
use media_domain::{MAX_MEDIA_RESULT_BYTES, MediaError};
use serde_json::{Value, json};
use std::collections::BTreeMap;
use std::path::Path;

struct Plan {
    start_index: usize,
    end_index: usize,
    start_pts: i64,
    end_pts: i64,
    start_us: i64,
    end_us: i64,
    durations_us: Vec<Option<i64>>,
}

fn seek_error() -> MediaError {
    MediaError::new(
        "SEEK_NOT_EXACT",
        "A complete keyframe-enclosing stream-copy interval cannot be verified.",
    )
}

fn supported(media: &Media) -> Result<(), MediaError> {
    if !media.trim_display_metadata_supported {
        return Err(MediaError::new(
            "UNSUPPORTED_FORMAT",
            "Rotation, display side data and explicit colour metadata require separate stream-copy acceptance.",
        ));
    }
    for stream in &media.streams {
        let allowed = (stream["codec_type"] == "video" && stream["codec_name"] == "h264")
            || (stream["codec_type"] == "audio" && stream["codec_name"] == "aac");
        if !allowed {
            return Err(MediaError::new(
                "UNSUPPORTED_FORMAT",
                "Stream copy currently supports one H.264 video and all AAC audio streams in MP4; other streams are not dropped.",
            ));
        }
    }
    Ok(())
}

fn plan(media: &Media, start_us: i64, end_us: i64) -> Result<Plan, MediaError> {
    if start_us < 0 || end_us <= start_us || end_us > media.duration_us {
        return Err(MediaError::new(
            "INVALID_INPUT",
            "Requested interval must be inside normalized video duration.",
        ));
    }
    let requested_start = media
        .origin_us
        .checked_add(start_us)
        .ok_or_else(seek_error)?;
    let requested_end = media.origin_us.checked_add(end_us).ok_or_else(seek_error)?;
    let mut start_index = None;
    for (index, frame) in media.frames.iter().enumerate() {
        if frame.key && media.time_base.micros(frame.pts)? <= requested_start {
            start_index = Some(index);
        }
    }
    let start_index = start_index.ok_or_else(seek_error)?;
    let mut end_index = media.frames.len();
    for (index, frame) in media.frames.iter().enumerate().skip(start_index + 1) {
        if frame.key && media.time_base.micros(frame.pts)? >= requested_end {
            end_index = index;
            break;
        }
    }
    let start_pts = media.frames[start_index].pts;
    let end_pts = if end_index == media.frames.len() {
        let last = media.frames.last().ok_or_else(seek_error)?;
        last.pts.checked_add(last.duration).ok_or_else(seek_error)?
    } else {
        media.frames[end_index].pts
    };
    let mut durations_us = Vec::new();
    if end_index == media.frames.len() {
        // Reading to EOF avoids inventing a cutoff that can orphan terminal B frames.
        durations_us.push(None);
    } else {
        let boundary = media
            .packets
            .iter()
            .find(|packet| {
                integer(&packet["stream_index"]).ok() == Some(media.video_index)
                    && integer(&packet["pts"]).ok() == Some(end_pts)
                    && packet["flags"]
                        .as_str()
                        .is_some_and(|flags| flags.contains('K'))
            })
            .ok_or_else(seek_error)?;
        let boundary_dts = integer(&boundary["dts"])?;
        let ticks = boundary_dts.checked_sub(start_pts).ok_or_else(seek_error)?;
        let cutoff = media.time_base.micros(ticks)?;
        // Candidates are derived from the actual next keyframe's decode timestamp,
        // never a fixture-specific t value. Microsecond cutoff is exclusive.
        if cutoff > 1 {
            durations_us.push(Some(cutoff - 1));
        }
        if cutoff > 0 {
            durations_us.push(Some(cutoff));
        }
        let preceding = media
            .packets
            .iter()
            .filter(|packet| integer(&packet["stream_index"]).ok() == Some(media.video_index))
            .filter_map(|packet| integer(&packet["dts"]).ok())
            .filter(|dts| *dts < boundary_dts)
            .max();
        if let Some(preceding) = preceding {
            let midpoint = preceding
                .checked_add(boundary_dts.checked_sub(preceding).ok_or_else(seek_error)? / 2)
                .ok_or_else(seek_error)?;
            if let Some(ticks) = midpoint.checked_sub(start_pts) {
                let value = media.time_base.micros(ticks)?;
                if value > 0 && !durations_us.contains(&Some(value)) {
                    durations_us.push(Some(value));
                }
            }
        }
    }
    if durations_us.is_empty() {
        return Err(seek_error());
    }
    Ok(Plan {
        start_index,
        end_index,
        start_pts,
        end_pts,
        start_us: media.time_base.micros(start_pts)?,
        end_us: media.time_base.micros(end_pts)?,
        durations_us,
    })
}

fn av_end_delta(streams: &[Value], packets: &[Value]) -> Result<i64, MediaError> {
    let mut ends = BTreeMap::<i64, i64>::new();
    for packet in packets {
        let index = integer(&packet["stream_index"])?;
        let stream = streams
            .iter()
            .find(|stream| integer(&stream["index"]).ok() == Some(index))
            .ok_or_else(seek_error)?;
        let time = Rational::parse(stream["time_base"].as_str().ok_or_else(seek_error)?)?;
        let end = integer(&packet["pts"])?
            .checked_add(integer(&packet["duration"])?)
            .ok_or_else(seek_error)?;
        let end = time.micros(end)?;
        ends.entry(index)
            .and_modify(|previous| *previous = (*previous).max(end))
            .or_insert(end);
    }
    let video = streams
        .iter()
        .find(|stream| stream["codec_type"] == "video")
        .ok_or_else(seek_error)?;
    let video_end = *ends
        .get(&integer(&video["index"])?)
        .ok_or_else(seek_error)?;
    let mut maximum = 0_i64;
    for stream in streams
        .iter()
        .filter(|stream| stream["codec_type"] == "audio")
    {
        let audio_end = *ends
            .get(&integer(&stream["index"])?)
            .ok_or_else(seek_error)?;
        maximum = maximum.max(
            video_end
                .abs_diff(audio_end)
                .try_into()
                .map_err(|_| seek_error())?,
        );
    }
    if maximum > 150_000 {
        return Err(MediaError::new(
            "OUTPUT_VALIDATION_FAILED",
            "Audio/video stream end-time difference exceeds the bounded backend tolerance.",
        ));
    }
    Ok(maximum)
}

pub(crate) fn execute(
    context: &Context<'_>,
    source: &Source,
    destination: &Path,
    mut media: Media,
    start_us: i64,
    end_us: i64,
) -> Result<Value, MediaError> {
    supported(&media)?;
    let destination = files::destination(source, destination)?;
    media.packets = media::packet_info(context, &source.path)?;
    let plan = plan(&media, start_us, end_us)?;
    let source_hashes = media::frame_hashes(context, &source.path)?;
    if source_hashes.len() != media.frames.len() {
        return Err(seek_error());
    }
    // Validate source audio as well. A parseable container is not proof that the
    // source streams decode completely and can be safely handed off as a result.
    media::full_decode(context, &source.path)?;
    let mut last_error = seek_error();
    for (attempt, duration) in plan.durations_us.iter().enumerate() {
        context.check()?;
        let mut temporary = Temporary::new(&destination)?;
        let candidate = (|| {
            let mut command = args(&[
                "-hide_banner",
                "-nostdin",
                "-n",
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
                "-seek_timestamp",
                "1",
                "-ss",
                &seconds_arg(plan.start_us),
                "-i",
            ]);
            command.push(source.path.as_os_str().to_owned());
            if let Some(duration) = duration {
                command.extend(args(&["-t", &seconds_arg(*duration)]));
            }
            command.extend(args(&[
                "-map",
                "0",
                "-c",
                "copy",
                "-avoid_negative_ts",
                "make_zero",
                "-map_metadata",
                "-1",
                "-f",
                "mp4",
            ]));
            command.push(temporary.path.as_os_str().to_owned());
            context.run(&context.config.ffmpeg, &command)?;
            let output = media::inspect(context, &temporary.path)?;
            let source_layout = media
                .streams
                .iter()
                .map(|s| (&s["codec_type"], &s["codec_name"], &s["language"]))
                .collect::<Vec<_>>();
            let output_layout = output
                .streams
                .iter()
                .map(|s| (&s["codec_type"], &s["codec_name"], &s["language"]))
                .collect::<Vec<_>>();
            if source_layout != output_layout {
                return Err(MediaError::new(
                    "OUTPUT_VALIDATION_FAILED",
                    "Output stream layout changed.",
                ));
            }
            let hashes = media::frame_hashes(context, &temporary.path)?;
            media::validate_hashes(&source_hashes, &hashes, plan.start_index, plan.end_index)?;
            let output_packets = media::packet_info(context, &temporary.path)?;
            media::validate_packets(&media.packets, &output_packets)?;
            media::validate_packet_timing(
                &media.streams,
                &media.packets,
                &output.streams,
                &output_packets,
                plan.start_pts,
            )?;
            media::full_decode(context, &temporary.path)?;
            let av_delta = av_end_delta(&output.streams, &output_packets)?;
            let result = json!({
                "schema_version": 0,"mock_only":false,"file_created":true,
                "requested_range_us":{"start_us":start_us,"end_us":end_us},
                "actual_range_us":{"start_us":plan.start_us-media.origin_us,"end_us":plan.end_us-media.origin_us},
                "actual_source_range_us":{"start_us":plan.start_us,"end_us":plan.end_us},
                "output_media_info":output.info,
                "stream_copy_plan":{
                    "alignment":"enclose_keyframes","source_time_base":format!("{}/{}",media.time_base.numerator,media.time_base.denominator),
                    "video_origin_pts":media.frames[0].pts,"actual_start_pts":plan.start_pts,"actual_end_exclusive_pts":plan.end_pts,
                    "input_seek_timestamp":true,"execution_duration_us":duration,"read_to_eof":duration.is_none(),
                    "attempts":attempt+1,"maximum_attempts":plan.durations_us.len(),"microsecond_rounding":"integer division toward zero; exclusive DTS cutoff candidates validated against every source frame"
                },
                "validation":{"all_streams_retained":true,"complete_source_frame_sequence":true,"verified_video_frames":hashes.len(),
                    "compressed_packet_payloads_from_source":true,"decode_all_streams":true,"packet_dts_monotonic":true,
                    "all_stream_packet_sequences_contiguous":true,"source_relative_packet_timestamps":true,"packet_timestamp_tolerance_us":2000,
                    "audio_video_end_difference_us":av_delta,"perceptual_av_sync_verified":false,"source_identity_unchanged":true}
            });
            let serialized = serde_json::to_vec(&result)
                .map_err(|_| MediaError::new("INTERNAL", "Result cannot be serialized."))?;
            if serialized.len() > MAX_MEDIA_RESULT_BYTES {
                return Err(MediaError::new(
                    "RESOURCE_LIMIT",
                    "Media result exceeded its retained-result bound.",
                ));
            }
            source.unchanged()?;
            context.check()?;
            temporary.commit(&destination)?;
            // No fallible serialization/check/cancellation after publication: a late
            // cancel cannot turn an already committed successful result into cancelled.
            Ok(result)
        })();
        match candidate {
            Ok(result) => return Ok(result),
            Err(error) => {
                temporary.cleanup()?;
                if ![
                    "SEEK_NOT_EXACT",
                    "OUTPUT_VALIDATION_FAILED",
                    "PROCESS_FAILED",
                ]
                .contains(&error.code)
                {
                    return Err(error);
                }
                last_error = error;
            }
        }
    }
    Err(last_error)
}
