import AVFoundation
import CoreMedia
import Foundation

struct CheckFailure: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

@main struct StemMixAudioTest {
    @MainActor static func main() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("mediastudio-mix-test-" + UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let format = AVAudioFormat(standardFormatWithSampleRate: 44100, channels: 2)!
        for (stem, impulse) in [("vocals", 4410), ("accompaniment", 22050)] {
            let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 44100)!
            buffer.frameLength = 44100
            for channel in 0..<2 {
                for i in 0..<44100 { buffer.floatChannelData![channel][i] = i == impulse ? 0.8 : 0 }
            }
            let file = try AVAudioFile(forWriting: root.appendingPathComponent(stem + ".wav"), settings: format.settings, commonFormat: .pcmFormatFloat32, interleaved: false)
            try file.write(from: buffer)
        }
        let controller = StemPlaybackController()
        await controller.loadWaveforms(directory: root)
        guard controller.waveforms.count == 2, controller.waveforms[.vocals]?[51] ?? 0 > 0.79,
              controller.waveforms[.accompaniment]?[256] ?? 0 > 0.79 else { throw CheckFailure(message: "waveform peaks do not match fixtures") }
        await controller.toggleMix(directory: root)
        guard let player = Mirror(reflecting: controller).children.first(where: { $0.label == "player" })?.value as? AVPlayer,
              let item = player.currentItem else { throw CheckFailure(message: "mix item unavailable: \(controller.issue ?? "unknown")") }
        func check(_ label: String, _ vocal: Float, _ accompaniment: Float) throws {
            let reader = try AVAssetReader(asset: item.asset)
            let tracks = item.asset.tracks(withMediaType: .audio)
            let output = AVAssetReaderAudioMixOutput(audioTracks: tracks, audioSettings: [
                AVFormatIDKey: kAudioFormatLinearPCM, AVLinearPCMIsFloatKey: true,
                AVLinearPCMBitDepthKey: 32, AVLinearPCMIsNonInterleaved: false,
                AVSampleRateKey: 44100, AVNumberOfChannelsKey: 2
            ])
            output.audioMix = item.audioMix
            guard reader.canAdd(output) else { throw CheckFailure(message: "cannot read audio mix") }
            reader.add(output)
            guard reader.startReading() else { throw reader.error ?? CheckFailure(message: "reader could not start") }
            var samples: [Float] = []
            while let buffer = output.copyNextSampleBuffer() {
                guard let data = CMSampleBufferGetDataBuffer(buffer) else { throw CheckFailure(message: "missing sample buffer") }
                let length = CMBlockBufferGetDataLength(data)
                var bytes = [UInt8](repeating: 0, count: length)
                guard CMBlockBufferCopyDataBytes(data, atOffset: 0, dataLength: length, destination: &bytes) == kCMBlockBufferNoErr else { throw CheckFailure(message: "sample copy failed") }
                bytes.withUnsafeBytes { raw in samples.append(contentsOf: raw.bindMemory(to: Float.self)) }
            }
            guard reader.status == .completed else { throw reader.error ?? CheckFailure(message: "reader failed") }
            guard samples.count == 88200 else { throw CheckFailure(message: "\(label): unexpected sample count \(samples.count)") }
            for (index, actual) in samples.enumerated() {
                let expected: Float = index / 2 == 4410 ? vocal : index / 2 == 22050 ? accompaniment : 0
                guard abs(actual - expected) < 0.0001 else { throw CheckFailure(message: "\(label): frame \(index / 2) channel \(index % 2): \(actual), expected \(expected)") }
            }
            print("\(label): 44100 stereo frames checked")
        }
        try check("both", 0.4, 0.4)
        controller.setSoloed(true, for: .vocals)
        try check("vocals solo", 0.4, 0)
        controller.setMuted(true, for: .vocals)
        try check("muted solo", 0, 0)
        controller.setSoloed(false, for: .vocals)
        try check("vocals muted", 0, 0.4)
        controller.setMuted(false, for: .vocals)
        try check("unmuted", 0.4, 0.4)
        let source = root.appendingPathComponent("vocals.wav")
        await controller.loadSourceWaveform(url: source)
        guard controller.sourceWaveform.count == 512 else { throw CheckFailure(message: "source waveform unavailable") }
        await controller.toggleSource(url: source)
        guard controller.isSourcePlaying, !controller.isMixPlaying else { throw CheckFailure(message: "switch to source failed: \(controller.issue ?? "unknown")") }
        await controller.toggle(.accompaniment, directory: root)
        guard controller.playing == .accompaniment, !controller.isSourcePlaying else { throw CheckFailure(message: "switch to stem failed: \(controller.issue ?? "unknown")") }
        await controller.toggleMix(directory: root)
        guard controller.isMixPlaying, controller.playing == nil else { throw CheckFailure(message: "switch back to mix failed: \(controller.issue ?? "unknown")") }
        try await controller.stop()
        guard controller.waveforms.isEmpty, controller.sourceWaveform.isEmpty, !controller.isMixPlaying else { throw CheckFailure(message: "stop did not clear audition") }
        await controller.loadWaveforms(directory: root)
        await controller.loadSourceWaveform(url: source)
        guard controller.waveforms.count == 2, controller.sourceWaveform.count == 512 else { throw CheckFailure(message: "waveforms failed to reload after stop") }
        try await controller.stop()
        print("mix and waveform reload checks passed")
    }
}
