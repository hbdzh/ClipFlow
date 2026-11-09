use crate::media::{Media, integer};
use media_domain::{AudioSelection, MediaError, TranscodeProfile};
use serde_json::{Value, json};

#[derive(Clone, Debug)]
pub(crate) struct Options {
    pub profile: TranscodeProfile,
    pub indices: Vec<u32>,
    pub ordinals: Vec<usize>,
    pub width: i64,
    pub height: i64,
    pub canvas_width: i64,
    pub canvas_height: i64,
    pub budget: u64,
}
impl Options {
    pub fn filter(&self) -> String {
        format!(
            "scale={}:{}:flags=bicubic+accurate_rnd,pad={}:{}:0:0:color=black,setsar=1",
            self.width, self.height, self.canvas_width, self.canvas_height
        )
    }
    pub fn summary(&self) -> Value {
        json!({"profile":self.profile.as_str(),"content_width":self.width,"content_height":self.height,"canvas_width":self.canvas_width,"canvas_height":self.canvas_height,"padding_right":self.canvas_width-self.width,"padding_bottom":self.canvas_height-self.height,"scale_filter":self.filter(),"selected_source_audio_stream_indices":self.indices,"maximum_output_file_bytes":self.budget})
    }
}
fn invalid() -> MediaError {
    MediaError::new(
        "INVALID_INPUT",
        "Selected audio stream is not a supported source audio index.",
    )
}
pub(crate) fn options(
    media: &Media,
    profile: TranscodeProfile,
    audio: &AudioSelection,
    budget: u64,
) -> Result<Options, MediaError> {
    crate::scope::supported(media)?;
    let video = media
        .streams
        .iter()
        .find(|s| s["codec_type"] == "video")
        .ok_or_else(invalid)?;
    let (width, height, canvas_width, canvas_height) = media_domain::transcode_geometry(
        integer(&video["width"])?,
        integer(&video["height"])?,
        profile,
    )
    .map_err(|_| {
        MediaError::new(
            "UNSUPPORTED_FORMAT",
            "Source dimensions do not fit the preset envelope.",
        )
    })?;
    let tracks = media
        .streams
        .iter()
        .filter(|s| s["codec_type"] == "audio")
        .collect::<Vec<_>>();
    let mut indices = vec![];
    let mut ordinals = vec![];
    for (ordinal, stream) in tracks.iter().enumerate() {
        let index = u32::try_from(integer(&stream["index"])?).map_err(|_| invalid())?;
        if matches!(audio, AudioSelection::All)
            || matches!(audio,AudioSelection::One{stream_index} if *stream_index==index)
        {
            indices.push(index);
            ordinals.push(ordinal);
        }
    }
    if matches!(audio, AudioSelection::One { .. }) && indices.is_empty() {
        return Err(invalid());
    }
    Ok(Options {
        profile,
        indices,
        ordinals,
        width,
        height,
        canvas_width,
        canvas_height,
        budget,
    })
}

pub(crate) fn preflight(
    media: &Media,
    start: i64,
    end: i64,
    options: &Options,
) -> Result<(), MediaError> {
    crate::scope::supported(media)?;
    let mut selected = media.clone();
    selected.streams.retain(|stream| {
        stream["codec_type"] == "video"
            || stream["index"]
                .as_i64()
                .is_some_and(|index| options.indices.contains(&(index as u32)))
    });
    crate::accurate::preflight(&selected, start, end)
}
#[cfg(test)]
mod tests {
    use super::*;
    fn delayed_tracks() -> Media {
        let video = json!({"index":0,"codec_type":"video","codec_name":"h264","pix_fmt":"yuv420p","width":320,"height":180,"sample_aspect_ratio":"1:1","field_order":"progressive","r_frame_rate":"30/1"});
        let delayed = json!({"index":1,"codec_type":"audio","codec_name":"aac","profile":"LC","channels":1,"sample_rate":48000,"time_base":"1/48000","start_pts":96000,"duration_ts":48000});
        let early = json!({"index":2,"codec_type":"audio","codec_name":"aac","profile":"LC","channels":1,"sample_rate":48000,"time_base":"1/48000","start_pts":0,"duration_ts":192000});
        Media {
            info: json!({}),
            streams: vec![video, delayed, early],
            frames: (0..120)
                .map(|i| crate::media::Frame {
                    pts: i * 512,
                    duration: 512,
                    key: i % 30 == 0,
                })
                .collect(),
            time_base: crate::time::Rational {
                numerator: 1,
                denominator: 15360,
            },
            packets: vec![],
            video_index: 0,
            origin_us: 0,
            duration_us: 4_000_000,
            trim_display_metadata_supported: true,
            core003_display_metadata_supported: true,
        }
    }
    #[test]
    fn unselected_audio_has_no_interval_constraint_but_whole_layout_remains_checked() {
        let media = delayed_tracks();
        let none = options(
            &media,
            TranscodeProfile::Standard,
            &AudioSelection::None,
            1_000_000,
        )
        .expect("none plan");
        preflight(&media, 0, 500_000, &none)
            .expect("unselected delayed audio must not reject silent video range");
        let one = options(
            &media,
            TranscodeProfile::Standard,
            &AudioSelection::One { stream_index: 2 },
            1_000_000,
        )
        .expect("one plan");
        preflight(&media, 0, 500_000, &one).expect("only selected early track overlap matters");
        let all = options(
            &media,
            TranscodeProfile::Standard,
            &AudioSelection::All,
            1_000_000,
        )
        .expect("all plan");
        assert_eq!(
            preflight(&media, 0, 500_000, &all)
                .expect_err("retained delayed track has no overlap")
                .code,
            "UNSUPPORTED_FORMAT"
        );
        let mut unsupported = media;
        unsupported.streams[1]["channels"] = json!(6);
        assert_eq!(
            options(
                &unsupported,
                TranscodeProfile::Standard,
                &AudioSelection::None,
                1_000_000
            )
            .expect_err("none cannot hide unsupported source layout")
            .code,
            "UNSUPPORTED_FORMAT"
        );
    }
}
