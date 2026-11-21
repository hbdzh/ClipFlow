//! Bounded MP4/MOV data-reference admission check, not a codec/demuxer.
//!
//! FFmpeg's enable_drefs=0 suppresses following a reference but can still read
//! same-file sample offsets. Reject external declarations before invoking it.
use media_domain::MediaError;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;

const MAX_BOXES: usize = 16_384;
const MAX_DEPTH: usize = 8;

fn invalid() -> MediaError {
    MediaError::new(
        "INVALID_MEDIA",
        "Media container reference metadata is invalid.",
    )
}

fn external() -> MediaError {
    MediaError::new(
        "UNSUPPORTED_FORMAT",
        "External media references are not supported.",
    )
}

struct Check {
    file: File,
    boxes: usize,
    simple_edits: bool,
}

impl Check {
    fn read<const N: usize>(&mut self, position: u64) -> Result<[u8; N], MediaError> {
        self.file
            .seek(SeekFrom::Start(position))
            .map_err(|_| invalid())?;
        let mut bytes = [0; N];
        self.file.read_exact(&mut bytes).map_err(|_| invalid())?;
        Ok(bytes)
    }

    fn box_at(&mut self, position: u64, end: u64) -> Result<([u8; 4], u64, u64), MediaError> {
        self.boxes = self.boxes.checked_add(1).ok_or_else(invalid)?;
        if self.boxes > MAX_BOXES || end.saturating_sub(position) < 8 {
            return Err(invalid());
        }
        let header = self.read::<8>(position)?;
        let size = u32::from_be_bytes(header[..4].try_into().map_err(|_| invalid())?);
        let kind = header[4..].try_into().map_err(|_| invalid())?;
        let (length, header_length) = match size {
            0 => (end - position, 8),
            1 => {
                if end - position < 16 {
                    return Err(invalid());
                }
                (u64::from_be_bytes(self.read::<8>(position + 8)?), 16)
            }
            _ => (u64::from(size), 8),
        };
        let next = position.checked_add(length).ok_or_else(invalid)?;
        if length < header_length || next > end {
            return Err(invalid());
        }
        Ok((kind, position + header_length, next))
    }

    fn data_references(&mut self, start: u64, end: u64) -> Result<(), MediaError> {
        if end - start < 8 {
            return Err(invalid());
        }
        let header = self.read::<8>(start)?;
        if header[..4] != [0; 4] {
            return Err(invalid());
        }
        let count = u32::from_be_bytes(header[4..].try_into().map_err(|_| invalid())?);
        if count == 0 || count as usize > MAX_BOXES {
            return Err(invalid());
        }
        let mut position = start + 8;
        for _ in 0..count {
            let (kind, body, next) = self.box_at(position, end)?;
            // Accept only the ISO self-contained URL declaration. URN, alias
            // and unknown reference kinds cannot establish same-file storage.
            if kind != *b"url " {
                return Err(external());
            }
            if next - body < 4 {
                return Err(invalid());
            }
            if self.read::<4>(body)? != [0, 0, 0, 1] {
                return Err(external());
            }
            position = next;
        }
        if position != end {
            return Err(invalid());
        }
        Ok(())
    }

    fn edit_list(&mut self, start: u64, end: u64) -> Result<bool, MediaError> {
        if end - start < 8 {
            return Err(invalid());
        }
        let header = self.read::<8>(start)?;
        let version = header[0];
        if !matches!(version, 0 | 1) || header[1..4] != [0; 3] {
            return Ok(false);
        }
        let count = u32::from_be_bytes(header[4..8].try_into().map_err(|_| invalid())?);
        if count == 0 || count > 2 {
            return Ok(false);
        }
        let width = if version == 0 { 12 } else { 20 };
        if end - start != 8 + u64::from(count) * width {
            return Err(invalid());
        }
        for index in 0..count {
            let pos = start + 8 + u64::from(index) * width;
            let (duration, media_time, rate_pos) = if version == 0 {
                let bytes = self.read::<8>(pos)?;
                (
                    u64::from(u32::from_be_bytes(
                        bytes[..4].try_into().map_err(|_| invalid())?,
                    )),
                    i64::from(i32::from_be_bytes(
                        bytes[4..].try_into().map_err(|_| invalid())?,
                    )),
                    pos + 8,
                )
            } else {
                let bytes = self.read::<16>(pos)?;
                (
                    u64::from_be_bytes(bytes[..8].try_into().map_err(|_| invalid())?),
                    i64::from_be_bytes(bytes[8..].try_into().map_err(|_| invalid())?),
                    pos + 16,
                )
            };
            if duration == 0 || self.read::<4>(rate_pos)? != [0, 1, 0, 0] {
                return Ok(false);
            }
            if count == 2 && index == 0 {
                if media_time != -1 {
                    return Ok(false);
                }
            } else if media_time < 0 {
                return Ok(false);
            }
        }
        Ok(true)
    }

    fn walk(&mut self, mut position: u64, end: u64, depth: usize) -> Result<(), MediaError> {
        if depth > MAX_DEPTH {
            return Err(invalid());
        }
        while position < end {
            let (kind, body, next) = self.box_at(position, end)?;
            match &kind {
                b"moov" | b"trak" | b"mdia" | b"minf" | b"dinf" | b"edts" => {
                    self.walk(body, next, depth + 1)?
                }
                b"dref" => self.data_references(body, next)?,
                b"elst" => {
                    self.simple_edits &= self.edit_list(body, next)?;
                }
                // Compressed movie metadata cannot establish that its enclosed
                // data references are local without another parser/decoder.
                b"rmra" | b"cmov" => return Err(external()),
                _ => (), // In particular, skip mdat payloads without allocation.
            }
            position = next;
        }
        Ok(())
    }
}

pub(crate) fn reject_external(path: &Path) -> Result<(), MediaError> {
    let file = crate::files::open_regular_read(path)?;
    let length = file.metadata().map_err(|_| invalid())?.len();
    if length < 8 {
        return Err(invalid());
    }
    Check {
        file,
        boxes: 0,
        simple_edits: true,
    }
    .walk(0, length, 0)
}

pub(crate) fn simple_edits(path: &Path) -> Result<bool, MediaError> {
    let file = crate::files::open_regular_read(path)?;
    let length = file.metadata().map_err(|_| invalid())?.len();
    if length < 8 {
        return Err(invalid());
    }
    let mut check = Check {
        file,
        boxes: 0,
        simple_edits: true,
    };
    check.walk(0, length, 0)?;
    Ok(check.simple_edits)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};
    static NEXT: AtomicUsize = AtomicUsize::new(0);

    fn atom(kind: &[u8; 4], body: &[u8]) -> Vec<u8> {
        let mut bytes = ((body.len() + 8) as u32).to_be_bytes().to_vec();
        bytes.extend(kind);
        bytes.extend(body);
        bytes
    }
    fn reference(flags: [u8; 4]) -> Vec<u8> {
        let mut body = vec![0; 4];
        body.extend(1_u32.to_be_bytes());
        body.extend(atom(b"url ", &flags));
        let mut bytes = atom(b"dref", &body);
        for kind in [b"dinf", b"minf", b"mdia", b"trak", b"moov"] {
            bytes = atom(kind, &bytes);
        }
        bytes
    }
    fn check(bytes: &[u8]) -> Result<(), MediaError> {
        let path = std::env::temp_dir().join(format!(
            "ms-reference-test-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        let mut file = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&path)
            .expect("new fixture");
        std::io::Write::write_all(&mut file, bytes).expect("fixture bytes");
        drop(file);
        let result = reject_external(&path);
        std::fs::remove_file(path).expect("fixture cleanup");
        result
    }
    #[test]
    fn only_one_unit_rate_content_edit_is_admitted() {
        let check_edits = |entries: &[(u32, i32, i16)]| {
            let mut body = vec![0; 4];
            body.extend((entries.len() as u32).to_be_bytes());
            for &(duration, media_time, rate) in entries {
                body.extend(duration.to_be_bytes());
                body.extend(media_time.to_be_bytes());
                body.extend(rate.to_be_bytes());
                body.extend(0_u16.to_be_bytes());
            }
            let bytes = atom(
                b"moov",
                &atom(b"trak", &atom(b"edts", &atom(b"elst", &body))),
            );
            let path = std::env::temp_dir().join(format!(
                "ms-edit-test-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            let mut file = std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&path)
                .expect("new edit fixture");
            std::io::Write::write_all(&mut file, &bytes).expect("edit bytes");
            drop(file);
            let result = simple_edits(&path).expect("valid edit syntax");
            std::fs::remove_file(path).expect("edit fixture cleanup");
            result
        };
        assert!(check_edits(&[(1000, 1024, 1)]));
        assert!(check_edits(&[(5000, -1, 1), (1000, 1024, 1)]));
        assert!(!check_edits(&[(1000, 0, 0)]));
        assert!(!check_edits(&[(1000, 0, 2)]));
        assert!(!check_edits(&[(1000, 0, 1), (1000, 0, 1)]));
        assert!(!check_edits(&[(0, 0, 1)]));
        assert!(!check_edits(&[(1000, -2, 1)]));
    }

    #[test]
    fn self_contained_is_accepted_and_external_reference_is_rejected() {
        assert!(check(&reference([0, 0, 0, 1])).is_ok());
        assert_eq!(
            check(&reference([0, 0, 0, 0])).expect_err("external").code,
            "UNSUPPORTED_FORMAT"
        );
    }
    #[test]
    fn invalid_sizes_counts_and_depth_fail_closed() {
        assert!(check(&[0; 7]).is_err());
        let mut oversized = atom(b"moov", &[]);
        oversized[..4].copy_from_slice(&u32::MAX.to_be_bytes());
        assert!(check(&oversized).is_err());
        assert!(check(&atom(b"dref", &[0; 8])).is_err());
        let mut deep = atom(b"free", &[]);
        for _ in 0..10 {
            deep = atom(b"moov", &deep);
        }
        assert!(check(&deep).is_err());
        let mut extended = 1_u32.to_be_bytes().to_vec();
        extended.extend(b"free");
        extended.extend(u64::MAX.to_be_bytes());
        assert!(check(&extended).is_err());
        let mut body = vec![0; 4];
        body.extend(2_u32.to_be_bytes());
        body.extend(atom(b"url ", &[0, 0, 0, 1]));
        assert!(check(&atom(b"dref", &body)).is_err());
    }

    #[test]
    fn unknown_and_compressed_references_fail_closed() {
        let mut body = vec![0; 4];
        body.extend(1_u32.to_be_bytes());
        body.extend(atom(b"alis", &[0, 0, 0, 1]));
        assert_eq!(
            check(&atom(b"dref", &body)).expect_err("alias").code,
            "UNSUPPORTED_FORMAT"
        );
        assert_eq!(
            check(&atom(b"moov", &atom(b"cmov", &[])))
                .expect_err("compressed")
                .code,
            "UNSUPPORTED_FORMAT"
        );
    }
    #[test]
    fn extended_size_and_zero_sized_mdat_are_supported() {
        let mut extended = 1_u32.to_be_bytes().to_vec();
        extended.extend(b"free");
        extended.extend(16_u64.to_be_bytes());
        extended.extend(reference([0, 0, 0, 1]));
        extended.extend([0; 4]);
        extended.extend(b"mdat");
        extended.extend([0; 32]);
        assert!(check(&extended).is_ok());
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn fifo_source_is_rejected_without_blocking_open() {
        let path = std::env::temp_dir().join(format!(
            "ms-reference-fifo-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        assert!(
            std::process::Command::new("/usr/bin/mkfifo")
                .arg(&path)
                .status()
                .expect("mkfifo")
                .success()
        );
        let start = std::time::Instant::now();
        let result = reject_external(&path);
        std::fs::remove_file(path).expect("fifo cleanup");
        assert!(start.elapsed() < std::time::Duration::from_secs(1));
        assert_eq!(
            result.expect_err("FIFO is not regular media").code,
            "INVALID_INPUT"
        );
    }
}
