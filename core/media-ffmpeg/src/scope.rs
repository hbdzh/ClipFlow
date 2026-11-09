//! Opt-in CORE-003 bounds. Legacy requests never call these stricter checks.
use crate::media::{Media, integer};
use crate::time::Rational;
use media_domain::MediaError;

fn unsupported() -> MediaError {
    MediaError::new(
        "UNSUPPORTED_FORMAT",
        "Media is outside the explicitly selected CORE-003 H.264/AAC CFR profile.",
    )
}

pub(crate) fn rate(media: &Media) -> Result<Rational, MediaError> {
    let video = media
        .streams
        .iter()
        .find(|s| s["codec_type"] == "video")
        .ok_or_else(unsupported)?;
    let rate = Rational::parse(video["r_frame_rate"].as_str().ok_or_else(unsupported)?)?;
    let accepted = [
        (24, 1),
        (25, 1),
        (30, 1),
        (60, 1),
        (24000, 1001),
        (30000, 1001),
    ]
    .iter()
    .any(|&(n, d)| i128::from(rate.numerator) * d == i128::from(rate.denominator) * n);
    if !accepted {
        return Err(unsupported());
    }
    Ok(rate)
}

pub(crate) fn supported(media: &Media) -> Result<Rational, MediaError> {
    if !media.core003_display_metadata_supported || media.duration_us > 600_000_000 {
        return Err(unsupported());
    }
    let video = media
        .streams
        .iter()
        .find(|s| s["codec_type"] == "video")
        .ok_or_else(unsupported)?;
    let width = integer(&video["width"])?;
    let height = integer(&video["height"])?;
    if video["codec_name"] != "h264"
        || video["pix_fmt"] != "yuv420p"
        || width < 2
        || height < 2
        || width % 2 != 0
        || height % 2 != 0
        || width.max(height) > 1920
        || width.min(height) > 1080
        || video["sample_aspect_ratio"].as_str() != Some("1:1")
        || video["field_order"]
            .as_str()
            .is_some_and(|s| !matches!(s, "progressive" | "unknown"))
    {
        return Err(unsupported());
    }
    let audio = media
        .streams
        .iter()
        .filter(|s| s["codec_type"] == "audio")
        .count();
    if audio > 2 || media.streams.len() != audio + 1 {
        return Err(unsupported());
    }
    for stream in &media.streams {
        let index = integer(&stream["index"])?;
        if !(0..16).contains(&index)
            || stream["language"].as_str().is_some_and(|s| {
                !s.bytes()
                    .all(|c| c.is_ascii_alphanumeric() || matches!(c, b'-' | b'_'))
            })
        {
            return Err(unsupported());
        }
        if stream["codec_type"] == "audio"
            && (stream["codec_name"] != "aac"
                || stream["profile"] != "LC"
                || !matches!(integer(&stream["channels"])?, 1 | 2)
                || !matches!(integer(&stream["sample_rate"])?, 44100 | 48000))
        {
            return Err(unsupported());
        }
    }
    let rate = rate(media)?;
    validate_cfr(
        &media
            .frames
            .iter()
            .map(|f| (f.pts, f.duration))
            .collect::<Vec<_>>(),
        media.time_base,
        rate,
    )?;
    Ok(rate)
}

pub(crate) fn validate_cfr(
    frames: &[(i64, i64)],
    time: Rational,
    rate: Rational,
) -> Result<(), MediaError> {
    let first = frames.first().ok_or_else(unsupported)?.0;
    // One source tick allows the necessary integer quantization of a rational
    // frame interval; comparison is checked integer arithmetic, not avg fps.
    let tick = i128::from(time.numerator)
        .checked_mul(i128::from(rate.numerator))
        .ok_or_else(unsupported)?;
    let period = i128::from(time.denominator)
        .checked_mul(i128::from(rate.denominator))
        .ok_or_else(unsupported)?;
    if period < tick || (period % tick != 0 && period / tick < 8) {
        return Err(unsupported());
    }
    let tolerance = if period % tick == 0 { 0 } else { tick };
    for (index, &(pts, duration)) in frames.iter().enumerate() {
        let actual = i128::from(pts)
            .checked_sub(i128::from(first))
            .and_then(|v| v.checked_mul(tick))
            .ok_or_else(unsupported)?;
        let expected = (index as i128)
            .checked_mul(period)
            .ok_or_else(unsupported)?;
        let duration = i128::from(duration)
            .checked_mul(tick)
            .ok_or_else(unsupported)?;
        let position_error = actual
            .checked_sub(expected)
            .and_then(i128::checked_abs)
            .ok_or_else(unsupported)?;
        let duration_error = duration
            .checked_sub(period)
            .and_then(i128::checked_abs)
            .ok_or_else(unsupported)?;
        if position_error > tolerance || duration_error > tolerance {
            return Err(unsupported());
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn cfr_uses_every_pts_and_duration_not_average_metadata() {
        let time = Rational::parse("1/30000").expect("time");
        let rate = Rational::parse("30000/1001").expect("rate");
        assert!(validate_cfr(&[(5000, 1001), (6001, 1001), (7002, 1001)], time, rate).is_ok());
        assert!(validate_cfr(&[(5000, 1001), (6101, 1001), (7002, 1001)], time, rate).is_err());
        assert!(validate_cfr(&[(5000, 1001), (6001, 900), (7002, 1001)], time, rate).is_err());
        assert!(
            validate_cfr(
                &[(0, 1), (1, 2), (3, 1)],
                Rational::parse("1/20").expect("time"),
                Rational::parse("30/1").expect("rate")
            )
            .is_err()
        );
        assert!(
            validate_cfr(
                &[(i64::MIN, i64::MAX), (i64::MAX, i64::MAX)],
                Rational {
                    numerator: i64::MAX,
                    denominator: 1
                },
                Rational {
                    numerator: i64::MAX,
                    denominator: 1
                }
            )
            .is_err()
        );
    }
}
