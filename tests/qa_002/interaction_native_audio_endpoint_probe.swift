import Foundation
import AVFoundation

// Read-only control for three-second QA sources. This intentionally exercises
// the original system player directly, rather than the product's PCM fallback.
@main struct AudioEndpointProbe {
    @MainActor static func main() async throws {
        var rows: [[String: Any]] = []
        for path in CommandLine.arguments.dropFirst() {
            let asset = AVURLAsset(url: URL(fileURLWithPath: path)); _ = try await asset.load(.isPlayable)
            let tracks = try await asset.loadTracks(withMediaType: .audio)
            let before = try await asset.load(.duration)
            var spans: [Double] = []
            for track in tracks { for segment in try await track.load(.segments) where !segment.isEmpty { spans.append(CMTimeGetSeconds(segment.timeMapping.target.duration)) } }
            let item = AVPlayerItem(asset: asset), player = AVPlayer(playerItem: item)
            let deadline = ContinuousClock.now + .seconds(10)
            while item.status == .unknown && ContinuousClock.now < deadline { try await Task.sleep(for: .milliseconds(20)) }
            let completed = await player.seek(to: CMTime(value: 2_999_999, timescale: 1_000_000), toleranceBefore: .zero, toleranceAfter: .zero)
            rows.append(["path": path, "asset_duration_seconds": CMTimeGetSeconds(before), "segment_duration_seconds": spans, "item_duration_seconds": CMTimeGetSeconds(item.duration), "ready": item.status == .readyToPlay, "requested_content_end_seconds": 2.999999, "end_seek_completed": completed, "actual_end_seconds": CMTimeGetSeconds(player.currentTime())])
            player.replaceCurrentItem(with: nil)
        }
        let output = try JSONSerialization.data(withJSONObject: rows, options: [.sortedKeys])
        FileHandle.standardOutput.write(output); FileHandle.standardOutput.write(Data("\n".utf8))
    }
}
