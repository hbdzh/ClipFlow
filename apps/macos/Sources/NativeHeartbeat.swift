import AppKit
import Darwin
import Foundation

/// Opt-in local QA instrumentation. No media names, paths, commands or bookmarks
/// are recorded. The normal application does not create a sentinel or log.
@MainActor
final class NativeHeartbeat {
    enum Activity: String, Codable, Sendable { case idle, media, ai, mixed }
    enum Stage: String, Codable, Sendable {
        case historyPersist = "history_persist"
        case historyEncode = "history_encode"
        case historyMetadata = "history_metadata"
        case historyAtomicWrite = "history_atomic_write"
        case destinationBookmark = "destination_bookmark"
        case aiBookmark = "ai_bookmark"
        case filePanelPreparation = "file_panel_preparation"
        case filePanelPresentation = "file_panel_presentation"
    }
    private enum WindowBucket: String { case workspace, sheet, other, none }
    static let shared = NativeHeartbeat()
    static let sentinelName = "qa002-heartbeat.enable"
    static let sentinelToken = "MediaStudio-QA002-AC22-Heartbeat-v1\n"

    private var sampler: Task<Void, Never>?
    private var writer: HeartbeatWriter?
    private var activity: (@MainActor () -> Activity)?
    private var enabled = false
    private var writerBusy = false
    private var finalEnqueued = false
    private var session = UUID()
    private var started = ContinuousClock.now
    private var lastTick = ContinuousClock.now
    private var previousActivity = Activity.idle
    private var previousWindow = WindowBucket.none
    private var sampleCount = 0
    private var measuredCount = 0
    private var activeCount = 0
    private var droppedWrites = 0
    private var warmupMaxMs = 0.0
    private var measuredMaxMs = 0.0
    private var activeMaxMs = 0.0
    private var counts: [String: Int] = [:]
    private var slowTicks: [HeartbeatSlowTick] = []
    private var windowIntervals: [String: HeartbeatInterval] = [:]
    private var stageTimings: [String: HeartbeatStageTiming] = [:]

    /// Times only synchronous work. Return values and thrown errors pass through;
    /// disabled diagnostics do not sample a clock or retain operation data.
    func measure<T>(_ stage: Stage, _ operation: () throws -> T) rethrows -> T {
        guard enabled else { return try operation() }
        let timingSession = session
        let begin = ContinuousClock.now
        defer {
            if enabled, timingSession == session {
                let end = ContinuousClock.now
                let duration = Self.milliseconds(begin.duration(to: end))
                var timing = stageTimings[stage.rawValue] ?? HeartbeatStageTiming(count: 0, maximumMs: 0, beginMs: 0, endMs: 0)
                timing.count += 1
                if duration >= timing.maximumMs {
                    timing.maximumMs = duration
                    timing.beginMs = Self.milliseconds(started.duration(to: begin))
                    timing.endMs = Self.milliseconds(started.duration(to: end))
                }
                stageTimings[stage.rawValue] = timing
            }
        }
        return try operation()
    }

    func startIfEnabled(activity: @escaping @MainActor () -> Activity = { .idle }) {
        guard !enabled, sampler == nil else { return }
        let directory = FileManager.default.temporaryDirectory.resolvingSymlinksInPath()
        let sentinel = directory.appendingPathComponent(Self.sentinelName)
        guard Self.validSentinel(sentinel) else { return }
        session = UUID()
        let output = directory.appendingPathComponent("qa002-heartbeat-" + session.uuidString + ".jsonl")
        let fd = output.withUnsafeFileSystemRepresentation { path in
            path.map { Darwin.open($0, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, mode_t(0o600)) } ?? -1
        }
        guard fd >= 0 else { return }
        var metadata = stat()
        guard fstat(fd, &metadata) == 0, metadata.st_uid == geteuid(),
              metadata.st_mode & mode_t(S_IFMT) == mode_t(S_IFREG), metadata.st_nlink == 1 else {
            Darwin.close(fd); return
        }
        writer = HeartbeatWriter(fd: fd)
        self.activity = activity
        enabled = true; finalEnqueued = false; writerBusy = false
        sampleCount = 0; measuredCount = 0; activeCount = 0; droppedWrites = 0
        warmupMaxMs = 0; measuredMaxMs = 0; activeMaxMs = 0; counts = [:]
        slowTicks = []; windowIntervals = [:]; stageTimings = [:]
        started = .now; lastTick = started; previousActivity = .idle; previousWindow = windowBucket()
        enqueue(kind: "started", elapsedMs: 0)
        sampler = Task { @MainActor [weak self] in
            for _ in 0..<36_000 { // At most one hour and 3,602 bounded summaries.
                do { try await Task.sleep(for: .milliseconds(100)) } catch { return }
                guard let self, self.enabled else { return }
                let now = ContinuousClock.now
                let delta = Self.milliseconds(self.lastTick.duration(to: now))
                let elapsed = Self.milliseconds(self.started.duration(to: now))
                let intervalBegin = Self.milliseconds(self.started.duration(to: self.lastTick))
                self.lastTick = now
                let work = self.activity?() ?? .idle
                let window = self.windowBucket()
                if delta > 250 {
                    self.slowTicks.append(HeartbeatSlowTick(beginMs: intervalBegin, endMs: elapsed, deltaMs: delta,
                        previousActivity: self.previousActivity.rawValue, currentActivity: work.rawValue,
                        previousWindow: self.previousWindow.rawValue, currentWindow: window.rawValue,
                        warmup: elapsed < 2_000))
                    if self.slowTicks.count > 4 { self.slowTicks.removeFirst(self.slowTicks.count - 4) }
                }
                self.sampleCount += 1; self.counts[work.rawValue, default: 0] += 1
                if elapsed < 2_000 { self.warmupMaxMs = max(self.warmupMaxMs, delta) }
                else {
                    self.measuredCount += 1
                    self.measuredMaxMs = max(self.measuredMaxMs, delta)
                    // A transition belongs to both endpoints; never remove it
                    // from the original global or active maximum.
                    for bucket in Set([self.previousWindow.rawValue, window.rawValue]) {
                        if delta >= (self.windowIntervals[bucket]?.maximumMs ?? 0) {
                            self.windowIntervals[bucket] = HeartbeatInterval(maximumMs: delta, beginMs: intervalBegin, endMs: elapsed)
                        }
                    }
                    if work != .idle || self.previousActivity != .idle {
                        self.activeCount += 1; self.activeMaxMs = max(self.activeMaxMs, delta)
                    }
                }
                self.previousActivity = work
                self.previousWindow = window
                if self.sampleCount % 10 == 0 { self.enqueue(kind: "sample", elapsedMs: elapsed) }
            }
            self?.finishSampling(reason: "limit")
        }
    }

    /// Cancels only the observer. Diagnostic file I/O never delays application
    /// shutdown: its final bounded write/close is best-effort on a separate actor.
    func stop() async {
        guard enabled else { return }
        let stoppingSession = session
        let task = sampler; sampler = nil
        task?.cancel(); await task?.value
        guard session == stoppingSession else { return }
        finishSampling(reason: "stopped")
    }

    private func finishSampling(reason: String) {
        guard enabled, !finalEnqueued else { return }
        enabled = false; finalEnqueued = true; activity = nil; sampler = nil
        guard let writer else { return }
        let record = snapshot(kind: reason, elapsedMs: Self.milliseconds(started.duration(to: .now)))
        self.writer = nil
        Task { await writer.append(record); await writer.finish() }
    }
    private func enqueue(kind: String, elapsedMs: Double) {
        guard let writer else { return }
        guard !writerBusy else { droppedWrites += 1; return }
        writerBusy = true
        let writerSession = session
        let record = snapshot(kind: kind, elapsedMs: elapsedMs)
        Task { [weak self] in
            await writer.append(record)
            if self?.session == writerSession { self?.writerBusy = false }
        }
    }
    private func snapshot(kind: String, elapsedMs: Double) -> HeartbeatRecord {
        HeartbeatRecord(kind: kind, session: session.uuidString, elapsedMs: elapsedMs,
                        sampleCount: sampleCount, measuredCount: measuredCount, activeCount: activeCount,
                        warmupMaxMs: warmupMaxMs, measuredMaxMs: measuredMaxMs, activeMaxMs: activeMaxMs,
                        activityCounts: counts, skippedWrites: droppedWrites,
                        windowGeometry: windowGeometry(), slowTicks: slowTicks,
                        windowIntervalMaxima: windowIntervals, stageTimings: stageTimings)
    }
    private func windowBucket() -> WindowBucket {
        guard let application = NSApp, let window = application.keyWindow ?? application.mainWindow else { return .none }
        if window.sheetParent != nil { return .sheet }
        return window.title == AppBrand.name ? .workspace : .other
    }
    // Read-only point dimensions, never window titles, paths or screen names.
    private func windowGeometry() -> HeartbeatWindowGeometry? {
        guard let window = NSApp?.keyWindow ?? NSApp?.mainWindow else { return nil }
        let frame = window.frame.size
        let content = window.contentLayoutRect.size
        let screen = window.screen
        return HeartbeatWindowGeometry(frameWidthPoints: frame.width, frameHeightPoints: frame.height,
            contentWidthPoints: content.width, contentHeightPoints: content.height,
            screenWidthPoints: screen.map { Double($0.frame.width) }, screenHeightPoints: screen.map { Double($0.frame.height) },
            visibleWidthPoints: screen.map { Double($0.visibleFrame.width) }, visibleHeightPoints: screen.map { Double($0.visibleFrame.height) },
            backingScaleFactor: window.backingScaleFactor)
    }
    private static func milliseconds(_ duration: Duration) -> Double {
        let value = duration.components
        return Double(value.seconds) * 1_000 + Double(value.attoseconds) / 1_000_000_000_000_000
    }
    private static func validSentinel(_ url: URL) -> Bool {
        let fd = url.withUnsafeFileSystemRepresentation { path in
            path.map { Darwin.open($0, O_RDONLY | O_NOFOLLOW | O_NONBLOCK) } ?? -1
        }
        guard fd >= 0 else { return false }
        defer { Darwin.close(fd) }
        var metadata = stat()
        guard fstat(fd, &metadata) == 0, metadata.st_uid == geteuid(), metadata.st_nlink == 1,
              metadata.st_mode & mode_t(S_IFMT) == mode_t(S_IFREG), metadata.st_mode & 0o777 == 0o600,
              metadata.st_size > 0, metadata.st_size <= 128 else { return false }
        var bytes = [UInt8](repeating: 0, count: 128)
        let size = bytes.withUnsafeMutableBytes { Darwin.read(fd, $0.baseAddress, $0.count) }
        guard size == Self.sentinelToken.utf8.count else { return false }
        return bytes.prefix(size).elementsEqual(Self.sentinelToken.utf8)
    }
}

private struct HeartbeatRecord: Encodable, Sendable {
    let schemaVersion = 1
    let periodMs = 100
    let warmupMs = 2_000
    let kind: String
    let session: String
    let elapsedMs: Double
    let sampleCount: Int
    let measuredCount: Int
    let activeCount: Int
    let warmupMaxMs: Double
    let measuredMaxMs: Double
    let activeMaxMs: Double
    let activityCounts: [String: Int]
    let skippedWrites: Int
    let windowGeometry: HeartbeatWindowGeometry?
    let diagnosticVersion = 1
    let slowTickThresholdMs = 250
    let slowTicks: [HeartbeatSlowTick]
    let windowIntervalMaxima: [String: HeartbeatInterval]
    let stageTimings: [String: HeartbeatStageTiming]
}

private struct HeartbeatSlowTick: Encodable, Sendable {
    let beginMs: Double
    let endMs: Double
    let deltaMs: Double
    let previousActivity: String
    let currentActivity: String
    let previousWindow: String
    let currentWindow: String
    let warmup: Bool
}
private struct HeartbeatInterval: Encodable, Sendable {
    let maximumMs: Double
    let beginMs: Double
    let endMs: Double
}
private struct HeartbeatStageTiming: Encodable, Sendable {
    var count: Int
    var maximumMs: Double
    var beginMs: Double
    var endMs: Double
}

private struct HeartbeatWindowGeometry: Encodable, Sendable {
    let frameWidthPoints: Double
    let frameHeightPoints: Double
    let contentWidthPoints: Double
    let contentHeightPoints: Double
    let screenWidthPoints: Double?
    let screenHeightPoints: Double?
    let visibleWidthPoints: Double?
    let visibleHeightPoints: Double?
    let backingScaleFactor: Double
}

private actor HeartbeatWriter {
    private var fd: Int32
    private var rows = 0
    init(fd: Int32) { self.fd = fd }
    func append(_ record: HeartbeatRecord) {
        guard fd >= 0, rows < 3_602 else { return }
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        encoder.keyEncodingStrategy = .convertToSnakeCase
        guard var bytes = try? encoder.encode(record), bytes.count <= 4_096 else { return }
        bytes.append(0x0A)
        let complete = bytes.withUnsafeBytes { buffer -> Bool in
            guard let base = buffer.baseAddress else { return false }
            var offset = 0
            while offset < buffer.count {
                let count = Darwin.write(fd, base.advanced(by: offset), buffer.count - offset)
                if count < 0 && errno == EINTR { continue }
                guard count > 0 else { return false }
                offset += count
            }
            return true
        }
        rows += 1
        if !complete { Darwin.close(fd); fd = -1 }
    }
    func finish() { if fd >= 0 { Darwin.close(fd); fd = -1 } }
    deinit { if fd >= 0 { Darwin.close(fd) } }
}
