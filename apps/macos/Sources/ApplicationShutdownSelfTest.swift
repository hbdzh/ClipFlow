import Foundation

@MainActor private final class ShutdownRelease {
    var ready = false
}

struct ApplicationShutdownSelfTestReport: Encodable {
    let scope = "Production application shutdown coordinator with controlled asynchronous cleanup closures; not media or GUI evidence"
    let passed: Bool
    let timeoutElapsedMs: Double
    let pendingAfterTimeout: Int
    let freezeCalls: Int
    let operationCalls: [Int]
    let completedAfterRelease: Bool
    let failedOperationCalls: [Int]
    let heartbeatCount: Int
    let heartbeatMaxIntervalMs: Double
    let publicEntriesFrozen: Bool
}

@MainActor
func runApplicationShutdownSelfTest() async throws -> ApplicationShutdownSelfTestReport {
    var freezeCalls = 0
    var calls = [0, 0]
    let release = ShutdownRelease()
    var heartbeatCount = 0
    var maxIntervalMs = 0.0
    var lastHeartbeat = ContinuousClock.now
    let workspace = WorkspaceModel(historyPath: URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("qa002-shutdown-\(UUID().uuidString).json"))
    let ai = AISeparationModel()
    let priorSource = ai.sourceURL
    let heartbeat = Task { @MainActor in
        while !Task.isCancelled {
            let now = ContinuousClock.now
            let delta = lastHeartbeat.duration(to: now).components
            maxIntervalMs = max(maxIntervalMs, Double(delta.seconds) * 1000 + Double(delta.attoseconds) / 1_000_000_000_000_000)
            lastHeartbeat = now; heartbeatCount += 1
            do { try await Task.sleep(for: .milliseconds(20)) } catch { break }
        }
    }
    defer { heartbeat.cancel() }
    let coordinator = ApplicationShutdown(freeze: {
        freezeCalls += 1; workspace.beginShutdown(); ai.beginShutdown()
    }, operations: [
        { calls[0] += 1 },
        { calls[1] += 1; while !release.ready { try await Task.sleep(for: .milliseconds(20)) } }
    ])
    let started = ContinuousClock.now
    var timedOut = false
    do { try await coordinator.shutdown() }
    catch { timedOut = coordinator.watchdogTimeouts == 1 }
    let elapsed = started.duration(to: .now).components
    let elapsedMs = Double(elapsed.seconds) * 1000 + Double(elapsed.attoseconds) / 1_000_000_000_000_000
    let pending = coordinator.pendingCount
    guard timedOut, elapsedMs >= 14_900, elapsedMs < 16_500,
          freezeCalls == 1, calls == [1, 1], pending == 1,
          !coordinator.completed, coordinator.issue != nil else {
        throw CheckFailure(message: "Application watchdog did not retain its pending cleanup after 15 seconds")
    }
    let unavailable = URL(fileURLWithPath: "/private/tmp/qa002-no-source-\(UUID().uuidString).mp4")
    await workspace.importURLs([unavailable])
    await workspace.export(to: unavailable)
    await ai.importSource(unavailable); await ai.start(); await ai.export(to: unavailable)
    let frozen = workspace.stopping && ai.stopping && !workspace.canImport && !workspace.canExport
        && workspace.assets.isEmpty && workspace.tasks.isEmpty && ai.sourceURL == priorSource && !ai.isRunning
    guard frozen, heartbeatCount > 100, maxIntervalMs < 1000 else {
        throw CheckFailure(message: "Application close left a public entry enabled or blocked the main actor")
    }
    release.ready = true
    try await coordinator.shutdown()
    guard coordinator.completed, coordinator.pendingCount == 0, calls == [1, 1], freezeCalls == 1 else {
        throw CheckFailure(message: "Application close did not resume the retained cleanup task")
    }
    // The controlled closures above test coordinator ownership only. Explicitly
    // close the otherwise unused production models too; no media success is faked.
    try await ai.shutdown(); try await workspace.shutdown()
    var retryCalls = [0, 0]
    let retry = ApplicationShutdown(freeze: {}, operations: [
        { retryCalls[0] += 1; if retryCalls[0] == 1 { throw CheckFailure(message: "QA controlled cleanup failure") } },
        { retryCalls[1] += 1; try await Task.sleep(for: .milliseconds(80)) }
    ])
    var rejected = false
    do { try await retry.shutdown() } catch { rejected = true }
    guard rejected, !retry.completed, retry.pendingCount == 0, retryCalls == [1, 1] else {
        throw CheckFailure(message: "One failed cleanup abandoned the other operation")
    }
    try await retry.shutdown()
    guard retry.completed, retryCalls == [2, 1] else {
        throw CheckFailure(message: "Cleanup retry repeated an already successful operation")
    }
    return ApplicationShutdownSelfTestReport(passed: true, timeoutElapsedMs: elapsedMs,
        pendingAfterTimeout: pending, freezeCalls: freezeCalls, operationCalls: calls,
        completedAfterRelease: coordinator.completed, failedOperationCalls: retryCalls,
        heartbeatCount: heartbeatCount, heartbeatMaxIntervalMs: maxIntervalMs, publicEntriesFrozen: frozen)
}
