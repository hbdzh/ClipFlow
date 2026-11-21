//! Fixed UMX-HQ CPU worker. Core owns process lifetime and publication.
use hound::{SampleFormat, WavReader};
use ort::{environment::Environment, session::Session, value::Tensor};
use rustfft::{Fft, FftPlanner, num_complex::Complex};
use sha2::{Digest, Sha256};
use std::{
    collections::HashMap,
    error::Error,
    fs::{self, File, OpenOptions},
    io::{Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    sync::Arc,
};

type Res<T> = Result<T, Box<dyn Error>>;
const FFT: usize = 4096;
const HOP: usize = 1024;
const BINS: usize = FFT / 2 + 1;
const BLOCK: usize = 256;
const MODEL_SHA: &str = "f27742bb52b24cb039614dc76d0764e279df3eb2a9d6daa82c6daee3369ec747";
// Fixed native protocol: emitted only for an actual ENOSPC in the IO error chain.
const OUT_OF_DISK_EXIT: i32 = 73;
const GRAPHS: [(&str, &str); 8] = [
    (
        "pre.onnx",
        "b4603f4f23a7d8aad8b06e4dda1f6ea6eb06aee2e83c95783bbdfbcc3073d34f",
    ),
    (
        "post.onnx",
        "72b52f71994551e8eac3c1cb4df3f7804e9e979c3ab44c5e0c756c7a0dfc5d8f",
    ),
    (
        "lstm-0-forward.onnx",
        "583a0a2ef0408c700362521dbee0be1b36733be86212fb596adc894d949bc3a6",
    ),
    (
        "lstm-0-reverse.onnx",
        "7bc6d9ae3bb2f96e33360ae78d1cfc3a878f96ca24e31e5bf9e5363c32adcee0",
    ),
    (
        "lstm-1-forward.onnx",
        "c414a8b7cc58ae481952374940181c0fa615483c60a7e860ee0e9e9f453fd42c",
    ),
    (
        "lstm-1-reverse.onnx",
        "ad66bcf6a2e263bd0982041081907e94715f21f69924c83f33c8768a722eb54a",
    ),
    (
        "lstm-2-forward.onnx",
        "7ae517cd0382773a20712273a7fa8fb9a604d0d5b22f9ba49bcd99246c298586",
    ),
    (
        "lstm-2-reverse.onnx",
        "1fb657beec6b3a8cd79acb85685bff4d40df21a1caa36240666fd080b41146ff",
    ),
];
fn verified_bytes(path: &Path, expected: &str) -> Res<Vec<u8>> {
    let bytes = fs::read(path)?;
    if format!("{:x}", Sha256::digest(&bytes)) != expected {
        return Err("invalid model or graph hash".into());
    }
    Ok(bytes)
}
fn session(env: &Environment, graphs: &Path, name: &str) -> Res<Session> {
    let hash = GRAPHS
        .iter()
        .find(|(n, _)| *n == name)
        .ok_or("unknown graph")?
        .1;
    let bytes = verified_bytes(&graphs.join(name), hash)?;
    Ok(Session::builder(env)?
        .with_intra_threads(2)?
        .with_inter_threads(1)?
        .commit_from_memory(&bytes)?)
}
fn tempfile(stage: &Path, index: usize, frames: usize) -> Res<File> {
    use std::os::unix::fs::OpenOptionsExt;
    let path = stage.join(format!(".feature-{index}"));
    let f = OpenOptions::new()
        .read(true)
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&path)?;
    fs::remove_file(path)?;
    f.set_len((frames * 512 * 4) as u64)?;
    Ok(f)
}
fn read_floats(f: &mut File, first: usize, count: usize) -> Res<Vec<f32>> {
    f.seek(SeekFrom::Start((first * 512 * 4) as u64))?;
    let mut b = vec![0u8; count * 512 * 4];
    f.read_exact(&mut b)?;
    Ok(b.as_chunks::<4>()
        .0
        .iter()
        .map(|x| f32::from_le_bytes(*x))
        .collect())
}
fn write_floats(f: &mut File, first: usize, values: &[f32]) -> Res<()> {
    f.seek(SeekFrom::Start((first * 512 * 4) as u64))?;
    let mut b = Vec::with_capacity(values.len() * 4);
    for x in values {
        b.extend_from_slice(&x.to_le_bytes());
    }
    f.write_all(&b)?;
    Ok(())
}
type Reader = WavReader<std::io::BufReader<File>>;
fn output(stage: &Path, name: &str, frames: usize) -> Res<std::io::BufWriter<File>> {
    use std::os::unix::fs::OpenOptionsExt;
    let file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(stage.join(name))?;
    let mut w = std::io::BufWriter::new(file);
    let size: u32 = (frames * 8).try_into()?;
    w.write_all(b"RIFF")?;
    w.write_all(&(size + 36).to_le_bytes())?;
    w.write_all(b"WAVEfmt ")?;
    w.write_all(&16u32.to_le_bytes())?;
    w.write_all(&3u16.to_le_bytes())?;
    w.write_all(&2u16.to_le_bytes())?;
    w.write_all(&44100u32.to_le_bytes())?;
    w.write_all(&(44100u32 * 8).to_le_bytes())?;
    w.write_all(&8u16.to_le_bytes())?;
    w.write_all(&32u16.to_le_bytes())?;
    w.write_all(b"data")?;
    w.write_all(&size.to_le_bytes())?;
    Ok(w)
}
fn samples(reader: &mut Reader, start: usize, count: usize) -> Res<Vec<f32>> {
    reader.seek(start.try_into()?)?;
    let values = reader
        .samples::<f32>()
        .take(count * 2)
        .collect::<Result<Vec<_>, _>>()?;
    if values.len() != count * 2 || values.iter().any(|x| !x.is_finite() || x.abs() > 2.0) {
        return Err("invalid audio".into());
    }
    Ok(values)
}
fn spectrum(
    reader: &mut Reader,
    n: usize,
    first: usize,
    count: usize,
    fft: &Arc<dyn Fft<f32>>,
    window: &[f32],
) -> Res<(Vec<Complex<f32>>, Vec<f32>)> {
    let begin = (first * HOP).saturating_sub(FFT / 2);
    let end = ((first + count - 1) * HOP + FFT / 2).min(n);
    let input = samples(reader, begin, end - begin)?;
    let mut bins = vec![Complex::new(0.0, 0.0); 2 * BINS * count];
    let mut mag = vec![0f32; bins.len()];
    for c in 0..2 {
        for frame in 0..count {
            let mut work = vec![Complex::new(0.0, 0.0); FFT];
            for i in 0..FFT {
                let mut pos = ((first + frame) * HOP + i) as isize - (FFT / 2) as isize;
                if pos < 0 {
                    pos = -pos;
                } else if pos >= n as isize {
                    pos = 2 * (n as isize) - 2 - pos;
                }
                work[i].re = input[((pos as usize - begin) * 2) + c] * window[i];
            }
            fft.process(&mut work);
            for (k, value) in work.iter().take(BINS).enumerate() {
                let idx = (c * BINS + k) * count + frame;
                bins[idx] = *value;
                mag[idx] = value.norm();
            }
        }
    }
    Ok((bins, mag))
}
fn execute(args: &HashMap<String, PathBuf>) -> Res<usize> {
    let model = &args["--model"];
    let graphs = &args["--graphs"];
    let runtime = &args["--runtime"];
    let source = &args["--input"];
    let stage = &args["--staging"];
    verified_bytes(model, MODEL_SHA)?;
    if fs::read_dir(stage)?.next().is_some() {
        return Err("staging not empty".into());
    }
    let mut reader = WavReader::open(source)?;
    let spec = reader.spec();
    let n = reader.duration() as usize;
    if spec.channels != 2
        || spec.sample_rate != 44100
        || spec.bits_per_sample != 32
        || spec.sample_format != SampleFormat::Float
        || !(2049..=44100 * 600).contains(&n)
    {
        return Err("unsupported input".into());
    }
    for first in (0..n).step_by(44100) {
        samples(&mut reader, first, (n - first).min(44100))?;
    }
    let total = n / HOP + 1;
    let mut encoded = tempfile(stage, 0, total)?;
    let mut planner = FftPlanner::<f32>::new();
    let forward = planner.plan_fft_forward(FFT);
    let inverse = planner.plan_fft_inverse(FFT);
    let window: Vec<f32> = (0..FFT)
        .map(|i| (0.5 - 0.5 * (std::f64::consts::TAU * i as f64 / FFT as f64).cos()) as f32)
        .collect();
    let env = ort::init_from(runtime)?.build()?;
    let mut pre = session(&env, graphs, "pre.onnx")?;
    for first in (0..total).step_by(BLOCK) {
        let count = (total - first).min(BLOCK);
        let (_, mag) = spectrum(&mut reader, n, first, count, &forward, &window)?;
        let input = Tensor::from_array(([1, 2, BINS, count], mag))?;
        let output = pre.run(ort::inputs!["magnitude" => input])?;
        let (_, values) = output["/Tanh_output_0"].try_extract_tensor::<f32>()?;
        if values.len() != count * 512 {
            return Err("unexpected pre output".into());
        }
        write_floats(&mut encoded, first, values)?;
    }
    let mut prior = encoded.try_clone()?;
    for layer in 0..3 {
        let mut both = tempfile(stage, layer + 1, total)?;
        for direction in ["forward", "reverse"] {
            let mut run = session(&env, graphs, &format!("lstm-{layer}-{direction}.onnx"))?;
            let starts: Vec<usize> = (0..total).step_by(BLOCK).collect();
            let starts: Box<dyn Iterator<Item = usize>> = if direction == "reverse" {
                Box::new(starts.into_iter().rev())
            } else {
                Box::new(starts.into_iter())
            };
            let mut h = vec![0f32; 256];
            let mut c = h.clone();
            for first in starts {
                let count = (total - first).min(BLOCK);
                let input = read_floats(&mut prior, first, count)?;
                let output=run.run(ort::inputs!["X" => Tensor::from_array(([count,1,512],input))?, "H" => Tensor::from_array(([1,1,256],h))?, "C" => Tensor::from_array(([1,1,256],c))?])?;
                let (_, y) = output["Y"].try_extract_tensor::<f32>()?;
                h = output["Hout"].try_extract_tensor::<f32>()?.1.to_vec();
                c = output["Cout"].try_extract_tensor::<f32>()?.1.to_vec();
                if y.len() != count * 256 || h.len() != 256 || c.len() != 256 {
                    return Err("unexpected recurrent output".into());
                }
                let mut out = read_floats(&mut both, first, count)?;
                let offset = if direction == "forward" { 0 } else { 256 };
                for t in 0..count {
                    out[t * 512 + offset..t * 512 + offset + 256]
                        .copy_from_slice(&y[t * 256..(t + 1) * 256]);
                }
                write_floats(&mut both, first, &out)?;
            }
        }
        prior = both;
    }
    let mut post = session(&env, graphs, "post.onnx")?;
    let mut vox = output(stage, "vocals.wav", n)?;
    let mut acc = output(stage, "accompaniment.wav", n)?;
    let mut pending = vec![0f32; 2 * (FFT - HOP)];
    let mut pending_norm = vec![0f32; FFT - HOP];
    let mut emitted = 0;
    for first in (0..total).step_by(BLOCK) {
        let count = (total - first).min(BLOCK);
        let (z, mag) = spectrum(&mut reader, n, first, count, &forward, &window)?;
        let enc = read_floats(&mut encoded, first, count)?;
        let lstm = read_floats(&mut prior, first, count)?;
        let mut transposed = vec![0f32; mag.len()];
        for t in 0..count {
            for ch in 0..2 {
                for k in 0..BINS {
                    transposed[(t * 2 + ch) * BINS + k] = mag[(ch * BINS + k) * count + t];
                }
            }
        }
        let output=post.run(ort::inputs!["/Tanh_output_0" => Tensor::from_array(([count,1,512],enc))?, "/lstm/Reshape_2_output_0" => Tensor::from_array(([count,1,512],lstm))?, "/Transpose_output_0" => Tensor::from_array(([count,1,2,BINS],transposed))?])?;
        let (_, estimate) = output["vocals_magnitude"].try_extract_tensor::<f32>()?;
        if estimate.len() != mag.len() || estimate.iter().any(|x| !x.is_finite()) {
            return Err("invalid model output".into());
        }
        let len = count * HOP + FFT - HOP;
        let mut accum = vec![0f32; 2 * len];
        let mut norm = vec![0f32; len];
        norm[..FFT - HOP].copy_from_slice(&pending_norm);
        for ch in 0..2 {
            accum[ch * len..ch * len + FFT - HOP]
                .copy_from_slice(&pending[ch * (FFT - HOP)..(ch + 1) * (FFT - HOP)]);
        }
        for t in 0..count {
            let pos = t * HOP;
            for i in 0..FFT {
                norm[pos + i] += window[i] * window[i];
            }
            for ch in 0..2 {
                let mut work = vec![Complex::new(0.0, 0.0); FFT];
                for (k, value) in work.iter_mut().take(BINS).enumerate() {
                    let idx = (ch * BINS + k) * count + t;
                    let mask = (estimate[idx] / mag[idx].max(1e-8)).clamp(0.0, 1.0);
                    *value = z[idx] * mask;
                }
                for k in 1..FFT / 2 {
                    work[FFT - k] = work[k].conj();
                }
                inverse.process(&mut work);
                for i in 0..FFT {
                    accum[ch * len + pos + i] += work[i].re / FFT as f32 * window[i];
                }
            }
        }
        let take = count * HOP;
        let base = first * HOP;
        let end = if first + count == total { len } else { take };
        let lo = base.max(FFT / 2);
        let hi = (base + end).min(FFT / 2 + n);
        if hi > lo {
            let original = samples(&mut reader, emitted, hi - lo)?;
            for j in 0..hi - lo {
                let i = lo - base + j;
                for ch in 0..2 {
                    let v = accum[ch * len + i] / norm[i].max(1e-8);
                    if !v.is_finite() {
                        return Err("non-finite result".into());
                    }
                    vox.write_all(&v.to_le_bytes())?;
                    acc.write_all(&(original[j * 2 + ch] - v).to_le_bytes())?;
                }
            }
            emitted += hi - lo;
        }
        pending_norm.copy_from_slice(&norm[take..]);
        for ch in 0..2 {
            pending[ch * (FFT - HOP)..(ch + 1) * (FFT - HOP)]
                .copy_from_slice(&accum[ch * len + take..(ch + 1) * len]);
        }
    }
    if emitted != n {
        return Err("incorrect output frames".into());
    }
    vox.flush()?;
    acc.flush()?;
    Ok(n)
}
fn is_out_of_disk(error: &(dyn Error + 'static)) -> bool {
    let mut current = Some(error);
    while let Some(value) = current {
        if value
            .downcast_ref::<std::io::Error>()
            .is_some_and(|io| io.raw_os_error() == Some(28))
        {
            return true;
        }
        current = value.source();
    }
    false
}

fn main() {
    let mut args = HashMap::new();
    let values: Vec<String> = std::env::args().skip(1).collect();
    if values.len() != 10 {
        eprintln!("invalid arguments");
        std::process::exit(1);
    }
    for pair in values.as_chunks::<2>().0 {
        let path = PathBuf::from(&pair[1]);
        if !["--model", "--graphs", "--runtime", "--input", "--staging"].contains(&pair[0].as_str())
            || !path.is_absolute()
            || args.insert(pair[0].clone(), path).is_some()
        {
            eprintln!("invalid arguments");
            std::process::exit(1);
        }
    }
    match execute(&args) {
        Ok(n) => println!(
            "{{\"schema_version\":1,\"model_id\":\"open-unmix-umxhq-vocals-onnx-e06097d3\",\"frames\":{n},\"sample_rate\":44100,\"channels\":2,\"outputs\":[\"vocals.wav\",\"accompaniment.wav\"]}}"
        ),
        Err(error) => {
            let full = is_out_of_disk(error.as_ref());
            let category = if full {
                "OUT_OF_DISK"
            } else {
                "PROCESS_FAILED"
            };
            println!("{{\"schema_version\":1,\"error\":\"{category}\"}}");
            std::process::exit(if full { OUT_OF_DISK_EXIT } else { 1 });
        }
    }
}

#[cfg(test)]
mod error_tests {
    use super::*;

    #[test]
    fn only_actual_enospc_is_classified_as_storage_exhaustion() {
        assert!(is_out_of_disk(&std::io::Error::from_raw_os_error(28)));
        assert!(!is_out_of_disk(&std::io::Error::from_raw_os_error(13)));
        assert!(!is_out_of_disk(&std::io::Error::other(
            "OUT_OF_DISK errno 28"
        )));
    }
}
