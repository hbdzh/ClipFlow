import AVFoundation
import Foundation

/// Bounded, streaming peak extraction for display. The samples and peak
/// normalization never modify the output audio or determine export semantics.
enum StemWaveforms {
    static func read(urls: [URL]) throws -> [[Float]] {
        var result: [[Float]] = []
        for url in urls {
            try Task.checkCancellation()
            result.append(try peaks(url: url))
        }
        return result
    }

    private static func peaks(url: URL) throws -> [Float] {
        let file = try AVAudioFile(forReading: url)
        let format = file.processingFormat
        let length = file.length
        guard format.commonFormat == .pcmFormatFloat32, !format.isInterleaved,
              format.channelCount > 0, format.channelCount <= 2,
              format.sampleRate.isFinite, format.sampleRate > 0, format.sampleRate <= 192_000,
              length > 0, Double(length) <= format.sampleRate * 6 * 3600,
              let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 8192) else {
            throw CheckFailure(message: "音轨格式或时长不支持生成波形。")
        }
        let count = 512
        var values = [Float](repeating: 0, count: count)
        var position: Int64 = 0
        let deadline = ContinuousClock.now + .seconds(60)
        while position < length {
            try Task.checkCancellation()
            guard ContinuousClock.now < deadline else { throw CheckFailure(message: "音轨波形读取超时。") }
            try file.read(into: buffer, frameCount: AVAudioFrameCount(min(Int64(buffer.frameCapacity), length - position)))
            let frames = Int(buffer.frameLength)
            guard frames > 0, let channels = buffer.floatChannelData else {
                throw CheckFailure(message: "音轨波形读取未完成。")
            }
            for frame in 0..<frames {
                let bin = min(count - 1, Int(((position + Int64(frame)) * Int64(count)) / length))
                for channel in 0..<Int(format.channelCount) {
                    let sample = channels[channel][frame]
                    guard sample.isFinite else { throw CheckFailure(message: "音轨包含无效采样，无法显示波形。") }
                    values[bin] = max(values[bin], min(abs(sample), 1))
                }
            }
            position += Int64(frames)
        }
        return values
    }
}
