//! Fixed PCM playback proxy. No model, video timeline or public export strategy.
use crate::files::{Source, Temporary};
use crate::media::integer;
use crate::process::{Context, args};
use media_domain::MediaError;
use serde_json::{Value, json};
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;

fn invalid() -> MediaError {
    MediaError::new(
        "OUTPUT_VALIDATION_FAILED",
        "PCM preview samples or layout did not match the source content.",
    )
}

pub(crate) fn execute(
    context: &Context<'_>,
    source: &Source,
    destination: &Path,
    info: &Value,
) -> Result<Value, MediaError> {
    let stream = &info["streams"][0];
    let rate = integer(&stream["sample_rate"])?;
    let channels = integer(&stream["channels"])?;
    let count = integer(&info["sample_count"])?;
    let mut temporary = Temporary::new(destination)?;
    let candidate = (|| {
        source.unchanged()?;
        let mut command = args(&[
            "-nostdin",
            "-hide_banner",
            "-v",
            "error",
            "-xerror",
            "-protocol_whitelist",
            "file",
            "-format_whitelist",
            "mov,wav,flac,mp3",
            "-i",
        ]);
        command.push(source.path.as_os_str().to_owned());
        command.extend(args(&[
            "-map",
            "0:a:0",
            "-vn",
            "-sn",
            "-dn",
            "-map_metadata",
            "-1",
            "-af",
            &format!("atrim=end_sample={count},asetpts=PTS-STARTPTS"),
            "-ar",
            &rate.to_string(),
            "-ac",
            &channels.to_string(),
            "-c:a",
            "pcm_f32le",
            "-f",
            "wav",
            "-n",
        ]));
        command.push(temporary.path.as_os_str().to_owned());
        context.run_media(
            &context.config.ffmpeg,
            &command,
            &temporary.path,
            context.media_file_budget.ok_or_else(invalid)?,
        )?;
        validate(context, &temporary.path, rate, channels, count)?;
        let reference_hash = pcm_hash(context, &source.path, rate, channels, Some(count))?;
        let output_hash = pcm_hash(context, &temporary.path, rate, channels, None)?;
        if reference_hash != output_hash {
            return Err(invalid());
        }
        let result = json!({"schema_version":0,"mock_only":false,"file_created":true,"preview_only":true,"profile":"wav_pcm_f32_preview_v1",
            "sample_rate":rate,"channels":channels,"sample_count":count,"source_audio_origin_us":info["audio_origin_us"],"source_content_duration_us":info["duration_us"],
            "preview_validation":{"source_identity_unchanged":true,"full_decode":true,"finite_samples":true,"content_mapping_verified":true}});
        let result_bytes = serde_json::to_vec(&result).map_err(|_| invalid())?;
        if result_bytes.len() > media_domain::MAX_MEDIA_RESULT_BYTES {
            return Err(invalid());
        }
        source.unchanged()?;
        context.check()?;
        temporary.commit(destination)?;
        Ok(result)
    })();
    match candidate {
        Ok(result) => Ok(result),
        Err(error) => {
            temporary.cleanup()?;
            Err(error)
        }
    }
}

pub(crate) fn validate(
    context: &Context<'_>,
    path: &Path,
    rate: i64,
    channels: i64,
    count: i64,
) -> Result<(), MediaError> {
    let mut file = crate::files::open_regular_read(path)?;
    let length = file.metadata().map_err(|_| invalid())?.len();
    let mut head = [0_u8; 12];
    file.read_exact(&mut head).map_err(|_| invalid())?;
    if &head[..4] != b"RIFF" || &head[8..] != b"WAVE" {
        return Err(invalid());
    }
    let expected = u64::try_from(count)
        .map_err(|_| invalid())?
        .checked_mul(u64::try_from(channels).map_err(|_| invalid())?)
        .and_then(|n| n.checked_mul(4))
        .ok_or_else(invalid)?;
    if length > expected.checked_add(4096).ok_or_else(invalid)? {
        return Err(invalid());
    }
    let mut position = 12_u64;
    let mut found_format = false;
    for _ in 0..128 {
        context.check()?;
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
        let next = position
            .checked_add(8)
            .and_then(|p| p.checked_add(size))
            .and_then(|p| p.checked_add(size % 2))
            .ok_or_else(invalid)?;
        if next > length {
            return Err(invalid());
        }
        if &header[..4] == b"fmt " {
            if !(16..=64).contains(&size) {
                return Err(invalid());
            }
            let mut fmt = vec![0_u8; usize::try_from(size).map_err(|_| invalid())?];
            file.read_exact(&mut fmt).map_err(|_| invalid())?;
            let tag = u16::from_le_bytes([fmt[0], fmt[1]]);
            let float = tag == 3
                || (tag == 0xfffe
                    && size >= 40
                    && fmt[24..40] == [3, 0, 0, 0, 0, 0, 16, 0, 128, 0, 0, 170, 0, 56, 155, 113]);
            if !float
                || i64::from(u16::from_le_bytes([fmt[2], fmt[3]])) != channels
                || i64::from(u32::from_le_bytes(
                    fmt[4..8].try_into().map_err(|_| invalid())?,
                )) != rate
                || u16::from_le_bytes([fmt[14], fmt[15]]) != 32
            {
                return Err(invalid());
            }
            let block = i64::from(u16::from_le_bytes([fmt[12], fmt[13]]));
            let bytes_per_second = i64::from(u32::from_le_bytes(
                fmt[8..12].try_into().map_err(|_| invalid())?,
            ));
            if block != channels * 4 || bytes_per_second != rate * block {
                return Err(invalid());
            }
            if tag == 0xfffe {
                let cb = u16::from_le_bytes([fmt[16], fmt[17]]);
                let bits = u16::from_le_bytes([fmt[18], fmt[19]]);
                let mask = u32::from_le_bytes(fmt[20..24].try_into().map_err(|_| invalid())?);
                if cb < 22 || bits != 32 || !matches!((channels, mask), (1, 0 | 4) | (2, 0 | 3)) {
                    return Err(invalid());
                }
            }
            found_format = true;
        } else if &header[..4] == b"data" {
            if !found_format || size != expected {
                return Err(invalid());
            }
            let mut remaining = size;
            let mut chunk = [0_u8; 65536];
            while remaining > 0 {
                context.check()?;
                let n =
                    usize::try_from(remaining.min(chunk.len() as u64)).map_err(|_| invalid())?;
                file.read_exact(&mut chunk[..n]).map_err(|_| invalid())?;
                if chunk[..n]
                    .as_chunks::<4>()
                    .0
                    .iter()
                    .any(|s| !f32::from_le_bytes([s[0], s[1], s[2], s[3]]).is_finite())
                {
                    return Err(invalid());
                }
                remaining -= n as u64;
            }
            return Ok(());
        }
        position = next;
    }
    Err(invalid())
}

fn pcm_hash(
    context: &Context<'_>,
    path: &Path,
    rate: i64,
    channels: i64,
    count: Option<i64>,
) -> Result<Vec<u8>, MediaError> {
    let mut command = args(&[
        "-nostdin",
        "-v",
        "error",
        "-xerror",
        "-protocol_whitelist",
        "file",
        "-format_whitelist",
        "mov,wav,flac,mp3",
        "-i",
    ]);
    command.push(path.as_os_str().to_owned());
    command.extend(args(&["-map", "0:a:0", "-vn", "-sn", "-dn"]));
    if let Some(count) = count {
        command.extend(args(&[
            "-af",
            &format!("atrim=end_sample={count},asetpts=PTS-STARTPTS"),
        ]));
    }
    command.extend(args(&[
        "-ar",
        &rate.to_string(),
        "-ac",
        &channels.to_string(),
        "-c:a",
        "pcm_f32le",
        "-f",
        "hash",
        "-hash",
        "sha256",
        "-",
    ]));
    let value = context.run(&context.config.ffmpeg, &command)?;
    if value.len() > 256 || !value.starts_with(b"SHA256=") {
        return Err(invalid());
    }
    Ok(value)
}
