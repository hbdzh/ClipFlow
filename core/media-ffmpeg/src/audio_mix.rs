//! Fixed-gain audition-compatible stem mix. No caller-supplied filters or codecs.
use crate::{
    audio_preview,
    files::{Source, Temporary},
    media::integer,
    process::{Context, args},
};
use media_domain::MediaError;
use serde_json::{Value, json};
use std::path::Path;
use std::sync::{
    Mutex, MutexGuard,
    atomic::{AtomicBool, Ordering},
};

static PUBLISH_GATE: Mutex<()> = Mutex::new(());
pub fn publish_guard() -> Result<MutexGuard<'static, ()>, MediaError> {
    PUBLISH_GATE
        .lock()
        .map_err(|_| MediaError::new("CLEANUP_FAILED", "Mix publication state is unavailable."))
}
pub(crate) fn check_dependencies(context: &Context<'_>) -> Result<(), MediaError> {
    for (option, required) in [
        ("-filters", &["volume", "amix"][..]),
        ("-encoders", &["pcm_f32le"][..]),
    ] {
        let out = context.run(&context.config.ffmpeg, &args(&["-hide_banner", option]))?;
        let text = std::str::from_utf8(&out).map_err(|_| {
            MediaError::new("DEPENDENCY_MISSING", "Required mix tools are unavailable.")
        })?;
        if required.iter().any(|name| {
            !text
                .lines()
                .any(|line| line.split_whitespace().nth(1) == Some(*name))
        }) {
            return Err(MediaError::new(
                "DEPENDENCY_MISSING",
                "Required mix tools are unavailable.",
            ));
        }
    }
    Ok(())
}
pub(crate) struct MixOptions<'a> {
    pub includes: (bool, bool),
    pub published: &'a AtomicBool,
    pub expected_parent: &'a str,
}
pub(crate) fn execute(
    context: &Context<'_>,
    vocals: &Source,
    accompaniment: &Source,
    destination: &Path,
    info: &Value,
    options: MixOptions<'_>,
) -> Result<Value, MediaError> {
    let (include_vocals, include_accompaniment) = options.includes;
    let count = integer(&info["sample_count"])?;
    let mut temporary = Temporary::new(destination)?;
    let candidate = (|| {
        vocals.unchanged()?;
        accompaniment.unchanged()?;
        let mut command = args(&[
            "-nostdin",
            "-hide_banner",
            "-v",
            "error",
            "-xerror",
            "-protocol_whitelist",
            "file",
            "-format_whitelist",
            "wav",
            "-i",
        ]);
        command.push(vocals.path.as_os_str().to_owned());
        command.extend(args(&[
            "-protocol_whitelist",
            "file",
            "-format_whitelist",
            "wav",
            "-i",
        ]));
        command.push(accompaniment.path.as_os_str().to_owned());
        let filter = format!(
            "[0:a:0]volume={}:precision=float[a];[1:a:0]volume={}:precision=float[b];[a][b]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[out]",
            if include_vocals { "0.5" } else { "0" },
            if include_accompaniment { "0.5" } else { "0" }
        );
        command.extend(args(&[
            "-filter_complex",
            &filter,
            "-map",
            "[out]",
            "-map_metadata",
            "-1",
            "-ar",
            "44100",
            "-ac",
            "2",
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
            context.media_file_budget.ok_or_else(|| {
                MediaError::new("RESOURCE_LIMIT", "Output budget is unavailable.")
            })?,
        )?;
        audio_preview::validate(context, &temporary.path, 44100, 2, count)?;
        vocals.unchanged()?;
        accompaniment.unchanged()?;
        let result = json!({"schema_version":0,"mock_only":false,"file_created":true,"profile":"wav_f32_stereo_44100_v1","sample_rate":44100,"channels":2,"sample_count":count,"include_vocals":include_vocals,"include_accompaniment":include_accompaniment});
        let _guard = publish_guard()?;
        context.check()?;
        if crate::prepared::parent_identity(destination)? != options.expected_parent {
            return Err(MediaError::new(
                "SOURCE_CHANGED",
                "Mix output directory changed during processing.",
            ));
        }
        temporary.commit(destination)?;
        options.published.store(true, Ordering::Release);
        Ok(result)
    })();
    match candidate {
        Ok(result) => Ok(result),
        Err(error) if error.code == "CLEANUP_FAILED" => {
            // The child may still be writing. Retain the private directory and
            // its lease for manual recovery; do not run Temporary::drop.
            std::mem::forget(temporary);
            Err(error)
        }
        Err(error) => {
            temporary.cleanup()?;
            Err(error)
        }
    }
}
