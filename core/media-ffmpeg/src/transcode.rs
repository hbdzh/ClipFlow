use crate::media::{Media, integer};
use media_domain::{AudioSelection, MediaError, TranscodeProfile, VideoTransform};
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
    pub transform: Option<VideoTransform>,
}
impl Options {
    pub fn output_size(&self) -> (i64, i64) {
        if self
            .transform
            .is_some_and(|t| matches!(t.rotation_degrees, 90 | 270))
        {
            (self.canvas_height, self.canvas_width)
        } else {
            (self.canvas_width, self.canvas_height)
        }
    }
    fn transform_geometry(&self, t: VideoTransform) -> (i64, i64, i64, i64, i64, i64) {
        let (w, h) = self.output_size();
        let sw = ((w * i64::from(t.scale_percent) + 99) / 100 + 1) & !1;
        let sh = ((h * i64::from(t.scale_percent) + 99) / 100 + 1) & !1;
        let pw = sw.max(w) + i64::from(t.offset_x).abs() * 2 + 4;
        let ph = sh.max(h) + i64::from(t.offset_y).abs() * 2 + 4;
        (w, h, sw, sh, pw, ph)
    }
    pub fn filter(&self) -> String {
        let scale = format!(
            "scale={}:{}:flags=bicubic+accurate_rnd",
            self.width, self.height
        );
        let pad = format!(
            "pad={}:{}:0:0:color=black,setsar=1",
            self.canvas_width, self.canvas_height
        );
        let base = if self.width % 2 != 0 || self.height % 2 != 0 {
            // A 4:2:0 pad rounds odd input dimensions down to chroma alignment,
            // turning the last source row or column black. Pad in 4:4:4 first,
            // then return to the preset's 4:2:0 output on the even canvas.
            format!("{scale},format=yuv444p,{pad},format=yuv420p")
        } else {
            format!("{scale},{pad}")
        };
        let Some(t) = self.transform else { return base };
        let rotation = match t.rotation_degrees {
            90 => ",transpose=clock",
            180 => ",hflip,vflip",
            270 => ",transpose=cclock",
            _ => "",
        };
        let (w, h, sw, sh, pw, ph) = self.transform_geometry(t);
        let px = (pw - sw) / 2;
        let py = (ph - sh) / 2;
        let cx = (pw - w) / 2 - i64::from(t.offset_x);
        let cy = (ph - h) / 2 - i64::from(t.offset_y);
        format!(
            "{base}{rotation},format=yuv444p,scale={sw}:{sh}:flags=bicubic+accurate_rnd,pad={pw}:{ph}:{px}:{py}:color=black,crop={w}:{h}:{cx}:{cy},format=yuv420p,setsar=1"
        )
    }
    pub fn summary(&self) -> Value {
        let (output_width, output_height) = self.output_size();
        let (
            content_x,
            content_y,
            content_width,
            content_height,
            padding_right,
            padding_bottom,
            working_bytes,
        ) = self.transform.map_or(
            (
                0,
                0,
                self.width,
                self.height,
                self.canvas_width - self.width,
                self.canvas_height - self.height,
                0,
            ),
            |t| {
                let (w, h, sw, sh, pw, ph) = self.transform_geometry(t);
                let left = ((w - sw) / 2 + i64::from(t.offset_x)).clamp(0, w);
                let top = ((h - sh) / 2 + i64::from(t.offset_y)).clamp(0, h);
                let right = ((w - sw) / 2 + i64::from(t.offset_x) + sw).clamp(0, w);
                let bottom = ((h - sh) / 2 + i64::from(t.offset_y) + sh).clamp(0, h);
                (
                    left,
                    top,
                    (right - left).max(0),
                    (bottom - top).max(0),
                    w - right,
                    h - bottom,
                    pw * ph * 16,
                )
            },
        );
        json!({"profile":self.profile.as_str(),"content_width":content_width,"content_height":content_height,"canvas_width":output_width,"canvas_height":output_height,"padding_right":padding_right,"padding_bottom":padding_bottom,"visible_content_rect":self.transform.map(|_|json!({"x":content_x,"y":content_y,"width":content_width,"height":content_height})),"estimated_transform_working_bytes":working_bytes,"scale_filter":self.filter(),"video_transform":self.transform.map(|t|json!({"rotation_degrees":t.rotation_degrees,"scale_percent":t.scale_percent,"offset_x":t.offset_x,"offset_y":t.offset_y})),"selected_source_audio_stream_indices":self.indices,"maximum_output_file_bytes":self.budget})
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
    transform: Option<VideoTransform>,
) -> Result<Options, MediaError> {
    if transform.is_some() && profile != TranscodeProfile::Standard {
        return Err(MediaError::new(
            "UNSUPPORTED_OPERATION",
            "Video transforms currently require the standard transcode profile.",
        ));
    }
    if transform.is_some_and(|t| {
        ![0, 90, 180, 270].contains(&t.rotation_degrees)
            || !(100..=200).contains(&t.scale_percent)
            || !(-1920..=1920).contains(&t.offset_x)
            || !(-1920..=1920).contains(&t.offset_y)
            || t.offset_x % 2 != 0
            || t.offset_y % 2 != 0
    }) {
        return Err(MediaError::new(
            "INVALID_INPUT",
            "Video transform is outside the supported bounds.",
        ));
    }
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
    let result = Options {
        profile,
        indices,
        ordinals,
        width,
        height,
        canvas_width,
        canvas_height,
        budget,
        transform,
    };
    if let Some(t) = transform {
        let (canvas_width, canvas_height, scaled_width, scaled_height, width, height) =
            result.transform_geometry(t);
        let left = (canvas_width - scaled_width) / 2 + i64::from(t.offset_x);
        let top = (canvas_height - scaled_height) / 2 + i64::from(t.offset_y);
        if left >= canvas_width
            || left + scaled_width <= 0
            || top >= canvas_height
            || top + scaled_height <= 0
        {
            return Err(MediaError::new(
                "INVALID_INPUT",
                "Video transform moves all picture content outside the canvas.",
            ));
        }
        if width
            .checked_mul(height)
            .and_then(|pixels| pixels.checked_mul(16))
            .is_none_or(|bytes| bytes > 128 * 1024 * 1024)
        {
            return Err(MediaError::new(
                "RESOURCE_LIMIT",
                "Video transform working dimensions exceed the bounded processing budget.",
            ));
        }
    }
    Ok(result)
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
    #[test]
    fn odd_content_is_padded_before_subsampling() {
        let mut options = Options {
            profile: TranscodeProfile::Small720,
            indices: vec![],
            ordinals: vec![],
            width: 719,
            height: 1280,
            canvas_width: 720,
            canvas_height: 1280,
            budget: 1,
            transform: None,
        };
        assert_eq!(
            options.filter(),
            "scale=719:1280:flags=bicubic+accurate_rnd,format=yuv444p,pad=720:1280:0:0:color=black,setsar=1,format=yuv420p"
        );
        options.width = 1280;
        options.height = 719;
        options.canvas_width = 1280;
        options.canvas_height = 720;
        assert_eq!(
            options.filter(),
            "scale=1280:719:flags=bicubic+accurate_rnd,format=yuv444p,pad=1280:720:0:0:color=black,setsar=1,format=yuv420p"
        );
        options.height = 720;
        assert_eq!(
            options.filter(),
            "scale=1280:720:flags=bicubic+accurate_rnd,pad=1280:720:0:0:color=black,setsar=1"
        );
    }
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
            None,
        )
        .expect("none plan");
        preflight(&media, 0, 500_000, &none)
            .expect("unselected delayed audio must not reject silent video range");
        let one = options(
            &media,
            TranscodeProfile::Standard,
            &AudioSelection::One { stream_index: 2 },
            1_000_000,
            None,
        )
        .expect("one plan");
        preflight(&media, 0, 500_000, &one).expect("only selected early track overlap matters");
        let all = options(
            &media,
            TranscodeProfile::Standard,
            &AudioSelection::All,
            1_000_000,
            None,
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
                1_000_000,
                None
            )
            .expect_err("none cannot hide unsupported source layout")
            .code,
            "UNSUPPORTED_FORMAT"
        );
    }
    #[test]
    fn transform_bounds_and_geometry_are_checked_before_execution() {
        let media = delayed_tracks();
        let make = |rotation_degrees, scale_percent, offset_x, offset_y| VideoTransform {
            rotation_degrees,
            scale_percent,
            offset_x,
            offset_y,
        };
        let moved = options(
            &media,
            TranscodeProfile::Standard,
            &AudioSelection::All,
            1_000_000,
            Some(make(90, 100, 20, -10)),
        )
        .expect("valid transform");
        let plan = moved.summary();
        assert_eq!(plan["canvas_width"], 180);
        assert_eq!(plan["canvas_height"], 320);
        assert_eq!(
            plan["visible_content_rect"],
            json!({"x":20,"y":0,"width":160,"height":310})
        );
        assert_eq!(plan["padding_right"], 0);
        assert_eq!(plan["padding_bottom"], 10);
        assert_eq!(
            options(
                &media,
                TranscodeProfile::Small720,
                &AudioSelection::All,
                1_000_000,
                Some(make(0, 100, 0, 0))
            )
            .unwrap_err()
            .code,
            "UNSUPPORTED_OPERATION"
        );
        assert_eq!(
            options(
                &media,
                TranscodeProfile::Standard,
                &AudioSelection::All,
                1_000_000,
                Some(make(0, 100, 1, 0))
            )
            .unwrap_err()
            .code,
            "INVALID_INPUT"
        );
        assert_eq!(
            options(
                &media,
                TranscodeProfile::Standard,
                &AudioSelection::All,
                1_000_000,
                Some(make(0, 100, 320, 0))
            )
            .unwrap_err()
            .code,
            "INVALID_INPUT"
        );
        let mut large = media;
        large.streams[0]["width"] = json!(1920);
        large.streams[0]["height"] = json!(1080);
        assert_eq!(
            options(
                &large,
                TranscodeProfile::Standard,
                &AudioSelection::All,
                1_000_000,
                Some(make(0, 200, 1920, 1000))
            )
            .unwrap_err()
            .code,
            "RESOURCE_LIMIT"
        );
    }
}
