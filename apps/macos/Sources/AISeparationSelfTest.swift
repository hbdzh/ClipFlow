import AppKit
import AVFoundation
import Foundation

struct AIPlaybackCheck: Encodable {
    let playable: Bool
    let reachedPlaying: Bool
    let advancedSeconds: Double
}

struct AISeparationSelfTestReport: Encodable {
    let passed: Bool
    let imported: Bool
    let separationSucceeded: Bool
    let vocalsPlayback: AIPlaybackCheck
    let accompanimentPlayback: AIPlaybackCheck
    let exportSucceeded: Bool
    let cancellationRequestedWhileRunning: Bool
    let cancellationStatus: String
    let cancelledWithoutResult: Bool
    let separationIssue: String?
    let separationFailure: JobFailure?
    let issue: String?
}

@MainActor
private func checkPlayback(_ url: URL) async -> AIPlaybackCheck {
    let asset = AVURLAsset(url: url)
    let playable = (try? await asset.load(.isPlayable)) ?? false
    guard playable else { return AIPlaybackCheck(playable: false, reachedPlaying: false, advancedSeconds: 0) }
    let player = AVPlayer(playerItem: AVPlayerItem(asset: asset))
    let start = player.currentTime().seconds
    player.play()
    let deadline = ContinuousClock.now + .seconds(4)
    var reachedPlaying = false
    var advanced = 0.0
    while ContinuousClock.now < deadline {
        reachedPlaying = reachedPlaying || player.timeControlStatus == .playing
        let now = player.currentTime().seconds
        if now.isFinite && start.isFinite { advanced = max(advanced, now - start) }
        if reachedPlaying && advanced > 0.1 { break }
        try? await Task.sleep(for: .milliseconds(100))
    }
    player.pause()
    return AIPlaybackCheck(playable: playable, reachedPlaying: reachedPlaying, advancedSeconds: advanced)
}

@MainActor
func runAISeparationSelfTest() async throws -> AISeparationSelfTestReport {
    _ = NSApplication.shared
    let args = CommandLine.arguments
    func argument(_ name: String) -> URL? {
        guard let i = args.firstIndex(of: name), i + 1 < args.count,
              args[i + 1].hasPrefix("/") else { return nil }
        return URL(fileURLWithPath: args[i + 1])
    }
    guard let source = argument("--ai-source"), let longSource = argument("--ai-cancel-source"),
          let destination = argument("--ai-export") else {
        throw CheckFailure(message: "AI self-test requires absolute --ai-source, --ai-cancel-source and --ai-export")
    }
    let model = AISeparationModel()
    guard model.configurationReady else { throw CheckFailure(message: "AI self-test components are not configured") }
    await model.importSource(source)
    let imported = model.sourceURL == source
    await model.start()
    let result = model.resultDirectory
    let succeeded = result != nil && model.issue == nil
    let separationIssue = model.issue
    let separationFailure = model.lastCoreFailure
    let emptyPlayback = AIPlaybackCheck(playable: false, reachedPlaying: false, advancedSeconds: 0)
    let vocals = if let result { await checkPlayback(result.appendingPathComponent("vocals.wav")) } else { emptyPlayback }
    let accompaniment = if let result { await checkPlayback(result.appendingPathComponent("accompaniment.wav")) } else { emptyPlayback }
    if succeeded { await model.export(to: destination) }
    let exported = model.status == "两条音轨已导出。" && model.issue == nil
    await model.importSource(longSource)
    let task = Task { await model.start() }
    let deadline = ContinuousClock.now + .seconds(30)
    while model.activeJobState != "running" && ContinuousClock.now < deadline {
        if model.issue != nil { break }
        try await Task.sleep(for: .milliseconds(20))
    }
    let running = model.activeJobState == "running"
    if running { await model.cancel() }
    await task.value
    let cancellationStatus = model.status
    let cancelled = cancellationStatus == "已取消。" && model.resultDirectory == nil
    let issue = model.issue
    try await model.shutdown()
    return AISeparationSelfTestReport(passed: imported && succeeded && exported && running && cancelled,
        imported: imported, separationSucceeded: succeeded, vocalsPlayback: vocals,
        accompanimentPlayback: accompaniment, exportSucceeded: exported,
        cancellationRequestedWhileRunning: running, cancellationStatus: cancellationStatus,
        cancelledWithoutResult: cancelled, separationIssue: separationIssue,
        separationFailure: separationFailure, issue: issue)
}
