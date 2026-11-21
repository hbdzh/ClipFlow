import AVFoundation
import Darwin
import Foundation

// Independent native-model control. Compile with the unmodified production
// StemPlaybackController.swift. No UI, fake player, or delayed-load injection.
struct CheckFailure: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

@main struct NativeAuditionReview {
    @MainActor static func main() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("qa002-audition-review-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        var pcm = Data()
        func append<T: FixedWidthInteger>(_ value: T) {
            var little = value.littleEndian
            Swift.withUnsafeBytes(of: &little) { pcm.append(contentsOf: $0) }
        }
        let frames = 88_200
        pcm.append(Data("RIFF".utf8)); append(UInt32(36 + frames * 4))
        pcm.append(Data("WAVEfmt ".utf8)); append(UInt32(16)); append(UInt16(1)); append(UInt16(2))
        append(UInt32(44_100)); append(UInt32(176_400)); append(UInt16(4)); append(UInt16(16))
        pcm.append(Data("data".utf8)); append(UInt32(frames * 4))
        pcm.append(Data(repeating: 0, count: frames * 4))
        for stem in StemPlaybackController.Stem.allCases { try pcm.write(to: root.appendingPathComponent(stem.filename)) }
        let player = StemPlaybackController()
        var checks: [[String: Any]] = []
        func check(_ name: String, _ passed: Bool, _ detail: String = "") {
            checks.append(["name": name, "passed": passed, "detail": detail])
        }
        await player.toggle(.vocals, directory: root)
        check("real_wav_play", player.playing == .vocals && !player.loading, player.issue ?? "")
        await player.toggle(.vocals, directory: root)
        check("pause_same_stem", player.playing == nil && !player.loading)
        await player.toggle(.accompaniment, directory: root)
        check("switch_real_stem", player.playing == .accompaniment && player.selected == .accompaniment, player.issue ?? "")
        try await player.stop()
        check("stop_playback", player.playing == nil && !player.loading && player.seconds == 0)
        var inFlightObserved = 0
        var allDrained = true
        for index in 0..<20 {
            let load = Task { await player.toggle(index.isMultiple(of: 2) ? .vocals : .accompaniment, directory: root) }
            await Task.yield()
            if player.loading { inFlightObserved += 1 }
            async let first: Void = player.stop()
            async let second: Void = player.stop()
            try await first; try await second
            await load.value
            allDrained = allDrained && !player.loading && player.playing == nil
        }
        check("real_async_load_observed", inFlightObserved > 0, "\(inFlightObserved)/20 cycles observed loading before stop")
        check("two_concurrent_stops_drain_real_loads", allDrained)
        await player.toggle(.vocals, directory: root)
        check("replay_after_concurrent_stops", player.playing == .vocals && !player.loading, player.issue ?? "")
        try await player.stop()
        let missing = root.appendingPathComponent("missing", isDirectory: true)
        await player.toggle(.vocals, directory: missing)
        check("missing_wav_has_error", player.playing == nil && player.issue != nil && !player.loading)
        try await player.stop()
        await player.toggle(.accompaniment, directory: root)
        check("recover_after_missing_wav", player.playing == .accompaniment && player.issue == nil, player.issue ?? "")
        try await player.stop()
        check("source_wavs_unchanged", StemPlaybackController.Stem.allCases.allSatisfy { (try? Data(contentsOf: root.appendingPathComponent($0.filename))) == pcm })
        let passed = checks.allSatisfy { $0["passed"] as? Bool == true }
        let report: [String: Any] = ["passed": passed, "layer": "native model; real AVFoundation; no GUI", "checks": checks,
            "not_run": ["AVFoundation load refusing cancellation for >5 seconds", "AI workspace retention after actual drain timeout", "VoiceOver", "keyboard and window GUI"]]
        FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]))
        FileHandle.standardOutput.write(Data("\n".utf8))
        exit(passed ? 0 : 1)
    }
}
