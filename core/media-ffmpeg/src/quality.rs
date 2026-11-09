//! Bounded decoded-content checks. These are not perceptual listening claims.
use crate::process::{Context, args};
use crate::time::Rational;
use crate::time::seconds_arg;
use media_domain::MediaError;
use serde_json::{Value, json};
use std::path::Path;

fn failed() -> MediaError {
    MediaError::new(
        "OUTPUT_VALIDATION_FAILED",
        "Decoded output content does not match its selected source reference.",
    )
}

pub(crate) fn input(path: &Path) -> Vec<std::ffi::OsString> {
    let mut command = args(&[
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
    command
}

pub(crate) fn video(
    context: &Context<'_>,
    source: &Path,
    output: &Path,
    start: usize,
    end: usize,
    rate: Rational,
) -> Result<Value, MediaError> {
    let mut command = args(&[
        "-hide_banner",
        "-nostdin",
        "-v",
        "error",
        "-xerror",
        "-threads",
        "1",
        "-filter_complex_threads",
        "1",
    ]);
    command.extend(input(source));
    command.extend(input(output));
    // Layout validation has already proven counts and each output frame's
    // source-relative presentation time. Compare those same ordinal pairs on
    // an exact one-tick-per-frame clock: AVTB rounding can otherwise omit the
    // final pair for a fractional source origin (e.g. frame 10 at 30fps).
    let filter = format!(
        "[0:v:0]trim=start_frame={start}:end_frame={end},settb={den}/{num},setpts=N[r];[1:v:0]settb={den}/{num},setpts=N[o];[r][o]psnr=stats_file=-:shortest=1:repeatlast=0[v]",
        den = rate.denominator,
        num = rate.numerator
    );
    command.extend(args(&[
        "-filter_complex",
        &filter,
        "-map",
        "[v]",
        "-f",
        "null",
        "-",
    ]));
    let output = context.run(&context.config.ffmpeg, &command)?;
    let scores = psnr(&output, end.checked_sub(start).ok_or_else(failed)?)?;
    let values = scores.map(|n| {
        if n.is_infinite() {
            json!("inf")
        } else {
            json!(n)
        }
    });
    Ok(
        json!({"compared_frames":end-start,"reference":"complete selected source frames, same yuv420p planes; no arbitrary image alignment","plane_psnr_db":{"y":values[0],"u":values[1],"v":values[2]},"minimum_plane_psnr_db":30,"colour_patch_fixture_checks":"independent QA; not a universal colour fidelity claim"}),
    )
}

pub(crate) fn psnr(bytes: &[u8], expected: usize) -> Result<[f64; 3], MediaError> {
    let text = std::str::from_utf8(bytes).map_err(|_| failed())?;
    let mut sums = [0.0; 3];
    let mut frames = 0;
    for line in text.lines().filter(|s| !s.is_empty()) {
        let values = line
            .split_whitespace()
            .filter_map(|p| p.split_once(':'))
            .collect::<std::collections::BTreeMap<_, _>>();
        frames += 1;
        if values.get("n").and_then(|s| s.parse::<usize>().ok()) != Some(frames) {
            return Err(failed());
        }
        for (index, key) in ["mse_y", "mse_u", "mse_v"].iter().enumerate() {
            let number = values
                .get(key)
                .and_then(|s| s.parse::<f64>().ok())
                .ok_or_else(failed)?;
            if !number.is_finite() || number < 0.0 {
                return Err(failed());
            }
            sums[index] += number;
        }
    }
    if frames != expected || frames == 0 {
        return Err(failed());
    }
    let scores = sums.map(|sum| {
        if sum == 0.0 {
            f64::INFINITY
        } else {
            10.0 * (65025.0 / (sum / frames as f64)).log10()
        }
    });
    if scores.iter().any(|n| n.is_nan() || *n < 30.0) {
        return Err(failed());
    }
    Ok(scores)
}

struct PcmWindow {
    start_us: i64,
    end_us: i64,
    samples: usize,
    track: usize,
}

fn pcm(context: &Context<'_>, path: &Path, window: &PcmWindow) -> Result<Vec<u8>, MediaError> {
    let mut command = args(&[
        "-hide_banner",
        "-nostdin",
        "-v",
        "error",
        "-xerror",
        "-threads",
        "1",
        "-copyts",
    ]);
    command.extend(input(path));
    let filter = format!(
        "atrim=start={}:end={},asetpts=PTS-({})/1000000/TB,aresample=8000:async=1:first_pts=0,apad=whole_dur={},atrim=end_sample={}",
        seconds_arg(window.start_us),
        seconds_arg(window.end_us),
        window.start_us,
        seconds_arg(window.end_us - window.start_us),
        window.samples
    );
    command.extend(args(&[
        "-map",
        &format!("0:a:{}", window.track),
        "-af",
        &filter,
        "-c:a",
        "pcm_f32le",
        "-f",
        "f32le",
        "-",
    ]));
    context.run(&context.config.ffmpeg, &command)
}

pub(crate) fn audio(
    context: &Context<'_>,
    source: &Path,
    output: &Path,
    start_us: i64,
    end_us: i64,
    channels: &[usize],
) -> Result<Value, MediaError> {
    let duration = end_us
        .checked_sub(start_us)
        .filter(|v| *v > 0)
        .ok_or_else(failed)?;
    let windows = audio_windows(duration)?;
    let mut measurements = Vec::new();
    for (track, &count) in channels.iter().enumerate() {
        if windows.is_empty() {
            return Err(failed());
        }
        for &(offset, end, samples) in &windows {
            context.check()?;
            let before = pcm(
                context,
                source,
                &PcmWindow {
                    start_us: start_us.checked_add(offset).ok_or_else(failed)?,
                    end_us: start_us.checked_add(end).ok_or_else(failed)?,
                    samples,
                    track,
                },
            )?;
            let after = pcm(
                context,
                output,
                &PcmWindow {
                    start_us: offset,
                    end_us: end,
                    samples,
                    track,
                },
            )?;
            let metrics = pcm_compare(&before, &after, count, samples)?;
            measurements
                .push(json!({"track":track,"start_us":offset,"end_us":end,"channels":metrics}));
        }
    }
    Ok(
        json!({"method":"complete timeline quantized once to nearest 8kHz sample; <=480000 comparison-sample windows, each channel independently; PTS mapping only, no correlation-based shifting","comparison_sample_rate":8000,"maximum_window_samples":480000,"time_quantization_us":125,"all_tracks_compared":channels.len(),"relative_rms_maximum":0.35,"cosine_minimum":0.90,"reference_silence_rms":0.000001,"silence_output_rms_maximum":0.00001,"windows":measurements,"full_frequency_or_perceptual_fidelity_verified":false}),
    )
}

fn audio_windows(duration: i64) -> Result<Vec<(i64, i64, usize)>, MediaError> {
    let total = usize::try_from(
        (i128::from(duration)
            .checked_mul(8000)
            .and_then(|v| v.checked_add(500_000))
            .ok_or_else(failed)?)
            / 1_000_000,
    )
    .map_err(|_| failed())?;
    let mut offset = 0_usize;
    let mut result = Vec::new();
    while offset < total {
        let end = offset.checked_add(480_000).ok_or_else(failed)?.min(total);
        let start_us = i64::try_from((offset as i128) * 125).map_err(|_| failed())?;
        let end_us = if end == total {
            duration
        } else {
            i64::try_from((end as i128) * 125).map_err(|_| failed())?
        };
        result.push((start_us, end_us, end - offset));
        offset = end;
    }
    Ok(result)
}

pub(crate) fn pcm_compare(
    before: &[u8],
    after: &[u8],
    channels: usize,
    samples: usize,
) -> Result<Vec<Value>, MediaError> {
    let expected = samples
        .checked_mul(channels)
        .and_then(|n| n.checked_mul(4))
        .ok_or_else(failed)?;
    if !matches!(channels, 1 | 2)
        || samples == 0
        || before.len() != expected
        || after.len() != expected
    {
        return Err(failed());
    }
    let mut sums = vec![[0.0_f64; 4]; channels];
    for (index, (a, b)) in before
        .as_chunks::<4>()
        .0
        .iter()
        .zip(after.as_chunks::<4>().0.iter())
        .enumerate()
    {
        let a = f64::from(f32::from_le_bytes(*a));
        let b = f64::from(f32::from_le_bytes(*b));
        if !a.is_finite() || !b.is_finite() {
            return Err(failed());
        }
        let sum = &mut sums[index % channels];
        sum[0] += a * a;
        sum[1] += b * b;
        sum[2] += a * b;
        sum[3] += (a - b) * (a - b);
    }
    let mut result = Vec::new();
    for sum in sums {
        let reference_rms = (sum[0] / samples as f64).sqrt();
        let output_rms = (sum[1] / samples as f64).sqrt();
        if reference_rms < 0.000001 {
            if output_rms > 0.00001 {
                return Err(failed());
            }
            result.push(json!({"reference_rms":reference_rms,"output_rms":output_rms,"silent_reference":true}));
        } else {
            let relative = (sum[3] / sum[0]).sqrt();
            let cosine = if sum[1] == 0.0 {
                0.0
            } else {
                sum[2] / (sum[0] * sum[1]).sqrt()
            };
            if !relative.is_finite() || !cosine.is_finite() || relative > 0.35 || cosine < 0.90 {
                return Err(failed());
            }
            result.push(json!({"reference_rms":reference_rms,"output_rms":output_rms,"relative_rms":relative,"cosine_similarity":cosine,"silent_reference":false}));
        }
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn sample_windows_quantize_total_once_and_do_not_create_empty_microsecond_tails() {
        assert_eq!(
            audio_windows(60_000_001).expect("windows"),
            vec![(0, 60_000_001, 480_000)]
        );
        assert_eq!(
            audio_windows(60_000_125).expect("windows"),
            vec![(0, 60_000_000, 480_000), (60_000_000, 60_000_125, 1)]
        );
        assert_eq!(
            audio_windows(1).expect("windows"),
            Vec::<(i64, i64, usize)>::new()
        );
    }
    #[test]
    fn psnr_rejects_chroma_corruption_missing_and_duplicate_frames() {
        let good = b"n:1 mse_y:1 mse_u:1 mse_v:1\nn:2 mse_y:2 mse_u:2 mse_v:2\n";
        assert!(psnr(good, 2).is_ok());
        assert!(psnr(good, 3).is_err());
        assert!(psnr(b"n:1 mse_y:0 mse_u:500 mse_v:500\n", 1).is_err());
        assert!(
            psnr(
                b"n:1 mse_y:1 mse_u:1 mse_v:1\nn:1 mse_y:1 mse_u:1 mse_v:1\n",
                2
            )
            .is_err()
        );
        assert!(psnr(b"n:1 mse_y:NaN mse_u:1 mse_v:1\n", 1).is_err());
    }
    #[test]
    fn pcm_compares_channels_independently_and_never_shifts() {
        let bytes = |numbers: &[f32]| {
            numbers
                .iter()
                .flat_map(|n| n.to_le_bytes())
                .collect::<Vec<_>>()
        };
        let reference = bytes(&[0.5, 0.1, -0.5, 0.1, 0.5, -0.1, -0.5, -0.1]);
        assert!(pcm_compare(&reference, &reference, 2, 4).is_ok());
        assert!(
            pcm_compare(
                &reference,
                &bytes(&[0.1, 0.5, 0.1, -0.5, -0.1, 0.5, -0.1, -0.5]),
                2,
                4
            )
            .is_err()
        );
        assert!(pcm_compare(&reference, &[0; 32], 2, 4).is_err());
        assert!(pcm_compare(&[0; 32], &[0; 32], 2, 4).is_ok());
        assert!(pcm_compare(&reference, &reference[..28], 2, 4).is_err());
    }
}
