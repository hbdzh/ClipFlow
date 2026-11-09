use media_domain::MediaError;
use std::fs::{self, Metadata};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

static NEXT: AtomicU64 = AtomicU64::new(1);

fn creation_error(error: &std::io::Error, message: &'static str) -> MediaError {
    // Accepted backend is macOS, where ENOSPC is 28. Unknown platforms retain
    // the conservative fallback rather than claiming portable errno semantics.
    if cfg!(target_os = "macos") && error.raw_os_error() == Some(28) {
        MediaError::new("OUT_OF_DISK", "Output storage is full.")
    } else {
        MediaError::new("PERMISSION_DENIED", message)
    }
}

#[derive(Clone)]
pub(crate) struct Source {
    pub path: PathBuf,
    metadata: Metadata,
}

impl Source {
    pub fn open(path: &Path) -> Result<Self, MediaError> {
        if !path.is_absolute() {
            return Err(MediaError::new(
                "INVALID_INPUT",
                "Source must be an absolute local file.",
            ));
        }
        let path = path
            .canonicalize()
            .map_err(|_| MediaError::new("INVALID_INPUT", "Source file cannot be opened."))?;
        let metadata = fs::metadata(&path)
            .map_err(|_| MediaError::new("INVALID_INPUT", "Source metadata cannot be read."))?;
        if !metadata.is_file() || metadata.len() == 0 {
            return Err(MediaError::new(
                "INVALID_INPUT",
                "Source must be a nonempty regular file.",
            ));
        }
        Ok(Self { path, metadata })
    }

    pub fn unchanged(&self) -> Result<(), MediaError> {
        let now = fs::metadata(&self.path).map_err(|_| {
            MediaError::new("SOURCE_CHANGED", "Source file changed during processing.")
        })?;
        if !same_file(&self.metadata, &now)
            || self.metadata.len() != now.len()
            || self.metadata.modified().ok() != now.modified().ok()
        {
            return Err(MediaError::new(
                "SOURCE_CHANGED",
                "Source file changed during processing.",
            ));
        }
        Ok(())
    }
}

#[cfg(unix)]
fn same_file(a: &Metadata, b: &Metadata) -> bool {
    use std::os::unix::fs::MetadataExt;
    a.dev() == b.dev() && a.ino() == b.ino()
}

#[cfg(not(unix))]
fn same_file(a: &Metadata, b: &Metadata) -> bool {
    // Non-Unix builds have not been accepted by CORE-002. This fallback is not
    // advertised as a replacement for platform-specific file identity checks.
    a.len() == b.len() && a.created().ok() == b.created().ok()
}

pub(crate) fn destination(source: &Source, path: &Path) -> Result<PathBuf, MediaError> {
    if !path.is_absolute() || path.extension().and_then(|s| s.to_str()) != Some("mp4") {
        return Err(MediaError::new(
            "INVALID_INPUT",
            "Destination must be a new absolute MP4 file.",
        ));
    }
    match fs::symlink_metadata(path) {
        Ok(_) => {
            return Err(MediaError::new(
                "OUTPUT_EXISTS",
                "Destination already exists and will not be replaced.",
            ));
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => (),
        Err(_) => {
            return Err(MediaError::new(
                "PERMISSION_DENIED",
                "Destination cannot be inspected.",
            ));
        }
    }
    let parent = path
        .parent()
        .ok_or_else(|| MediaError::new("INVALID_INPUT", "Destination has no parent directory."))?;
    let parent = parent.canonicalize().map_err(|_| {
        MediaError::new("PERMISSION_DENIED", "Destination directory is unavailable.")
    })?;
    if !parent.is_dir() {
        return Err(MediaError::new(
            "PERMISSION_DENIED",
            "Destination parent is not a directory.",
        ));
    }
    let name = path
        .file_name()
        .ok_or_else(|| MediaError::new("INVALID_INPUT", "Destination name is invalid."))?;
    let resolved = parent.join(name);
    if resolved == source.path {
        return Err(MediaError::new(
            "INVALID_INPUT",
            "Destination cannot be the source file.",
        ));
    }
    Ok(resolved)
}

pub(crate) struct Temporary {
    directory: PathBuf,
    pub path: PathBuf,
    active: bool,
}

impl Temporary {
    pub fn new(destination: &Path) -> Result<Self, MediaError> {
        let parent = destination
            .parent()
            .ok_or_else(|| MediaError::new("INVALID_INPUT", "Destination parent is missing."))?;
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| MediaError::new("INTERNAL", "System clock is unavailable."))?
            .as_nanos();
        for _ in 0..16 {
            let id = NEXT.fetch_add(1, Ordering::Relaxed);
            let directory =
                parent.join(format!(".mediastudio-{}-{stamp}-{id}", std::process::id()));
            let mut builder = fs::DirBuilder::new();
            #[cfg(unix)]
            {
                use std::os::unix::fs::DirBuilderExt;
                builder.mode(0o700);
            }
            match builder.create(&directory) {
                Ok(()) => {
                    return Ok(Self {
                        path: directory.join("output.mp4"),
                        directory,
                        active: true,
                    });
                }
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(error) => {
                    return Err(creation_error(
                        &error,
                        "A private output directory cannot be created.",
                    ));
                }
            }
        }
        Err(MediaError::new(
            "PROCESS_FAILED",
            "A unique private output directory cannot be created.",
        ))
    }

    pub fn cleanup(&mut self) -> Result<(), MediaError> {
        if !self.active {
            return Ok(());
        }
        match fs::remove_file(&self.path) {
            Ok(()) => (),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => (),
            Err(_) => {
                return Err(MediaError::new(
                    "CLEANUP_FAILED",
                    "Temporary media output could not be removed.",
                ));
            }
        }
        fs::remove_dir(&self.directory).map_err(|_| {
            MediaError::new(
                "CLEANUP_FAILED",
                "Private output directory could not be removed.",
            )
        })?;
        self.active = false;
        Ok(())
    }

    pub fn commit(&mut self, destination: &Path) -> Result<(), MediaError> {
        let output = fs::symlink_metadata(&self.path).map_err(|_| {
            MediaError::new("OUTPUT_VALIDATION_FAILED", "Validated output is missing.")
        })?;
        if !output.is_file() || output.file_type().is_symlink() || output.len() == 0 {
            return Err(MediaError::new(
                "OUTPUT_VALIDATION_FAILED",
                "Validated output is not a regular media file.",
            ));
        }
        // hard_link creates the final name only when it does not exist. Unlike
        // rename, this cannot silently replace a concurrent output/source alias.
        fs::hard_link(&self.path, destination).map_err(|error| {
            if error.kind() == std::io::ErrorKind::AlreadyExists {
                MediaError::new(
                    "OUTPUT_EXISTS",
                    "Destination appeared during export and was not replaced.",
                )
            } else {
                creation_error(
                    &error,
                    "Validated output cannot be committed without overwriting.",
                )
            }
        })?;
        if let Err(error) = self.cleanup() {
            // The only final name created by us refers to this exact inode.
            // Do not remove an unrelated name that a concurrent actor replaced.
            if let Ok(current) = fs::symlink_metadata(destination)
                && same_file(&output, &current)
            {
                let _ = fs::remove_file(destination);
            }
            return Err(error);
        }
        Ok(())
    }
}

impl Drop for Temporary {
    fn drop(&mut self) {
        // Explicit callers report cleanup errors. The non-panicking destructor is
        // only an unwind fallback, and removes no unknown directory contents.
        let _ = self.cleanup();
    }
}

impl Source {
    pub(crate) fn identity(&self) -> String {
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            format!("{}:{}", self.metadata.dev(), self.metadata.ino())
        }
        #[cfg(not(unix))]
        {
            format!("{:?}", self.path)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn macos_disk_full_creation_error_is_not_reported_as_permission_denied() {
        let error = std::io::Error::from_raw_os_error(28);
        assert_eq!(
            creation_error(&error, "fallback").code,
            if cfg!(target_os = "macos") {
                "OUT_OF_DISK"
            } else {
                "PERMISSION_DENIED"
            }
        );
        let error = std::io::Error::from(std::io::ErrorKind::PermissionDenied);
        assert_eq!(creation_error(&error, "fallback").code, "PERMISSION_DENIED");
    }

    #[test]
    fn destination_does_not_replace_existing_source_or_outputs() {
        let dir = std::env::temp_dir().join(format!(
            "media-core-path-test-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&dir).expect("test directory");
        let source_path = dir.join("source.mp4");
        fs::write(&source_path, b"owned source").expect("test source");
        let source = Source::open(&source_path).expect("source");
        assert!(destination(&source, &source_path).is_err());
        let alias = dir.join("hardlink.mp4");
        fs::hard_link(&source_path, &alias).expect("hardlink");
        assert!(destination(&source, &alias).is_err());
        assert_eq!(
            fs::read(&source_path).expect("read source"),
            b"owned source"
        );
        fs::remove_dir_all(dir).expect("owned cleanup");
    }
}
