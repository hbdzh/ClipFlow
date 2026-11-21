import AVFoundation
import Foundation

struct CheckFailure: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

@main struct WaveformPeakTest {
    static func main() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("mediastudio-waveform-test-" + UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let format = AVAudioFormat(standardFormatWithSampleRate: 44100, channels: 2)!
        let count: AVAudioFrameCount = 44100
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: count), let samples = buffer.floatChannelData else {
            fatalError("Unable to create test buffer")
        }
        buffer.frameLength = count
        for channel in 0..<2 {
            for i in 0..<Int(count) { samples[channel][i] = i < Int(count) / 2 ? 0 : 0.25 }
        }
        let url = root.appendingPathComponent("known.wav")
        do {
            let file = try AVAudioFile(forWriting: url, settings: format.settings, commonFormat: .pcmFormatFloat32, interleaved: false)
            try file.write(from: buffer)
        }
        let result = try StemWaveforms.read(urls: [url])
        guard let peaks = result.first, peaks.count == 512,
              peaks.prefix(250).allSatisfy({ $0 == 0 }),
              peaks.suffix(250).allSatisfy({ abs($0 - 0.25) < 0.0001 }) else { fatalError("Waveform bins differ from known samples") }
        do {
            _ = try StemWaveforms.read(urls: [root.appendingPathComponent("missing.wav")])
            fatalError("Missing input was accepted")
        } catch { }
        print("waveform peak checks passed")
    }
}
