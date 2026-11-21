import AppKit
import Foundation

private struct WorkspaceScenario: Decodable {
    let schemaVersion: Int
    let historyPath: String
    let actions: [WorkspaceAction]
}
private struct WorkspaceAction: Decodable {
    let action: String
    var paths: [String]?
    var index: Int?
    var startUs: Int64?
    var endUs: Int64?
    var value: String?
    var destination: String?
    var wait: Bool?
    var positionUs: Int64?
    var milliseconds: Int?
    var ffmpeg: String?
    var ffprobe: String?
    var path: String?
    var contains: String?
    var indices: [Int]?
    var destinations: [String]?
    var streamIndex: Int?
    var submissionWait: Bool?
}
struct WorkspaceAssetEvidence: Encodable, Sendable {
    let id: UUID
    let sourceName: String
    let state: String
    let range: MediaRange?
    let startText: String
    let endText: String
    let mode: ExportStrategy
    let mediaInfo: MediaInfo?
    let error: String?
    let audioSelection: AudioSelection
    let includedInBatch: Bool
}
struct WorkspaceTaskEvidence: Encodable, Sendable {
    let id: UUID
    let sourceAssetId: UUID
    let sourceName: String
    let destinationName: String
    let mode: ExportStrategy
    let requestedRange: MediaRange
    let coreJobId: String?
    let state: String
    let actualRange: MediaRange?
    let error: JobFailure?
    let result: JobResult?
    let interrupted: Bool
    let audioSelection: AudioSelection?
    let sessionId: UUID?
}
struct WorkspaceStepEvidence: Encodable, Sendable {
    let action: String
    let phase: String
    let assets: [WorkspaceAssetEvidence]
    let selectedAssetId: UUID?
    let activeExport: ActiveExport?
    let tasks: [WorkspaceTaskEvidence]
    let core: JobSnapshot?
    let trace: [JobSnapshot]
    let preview: PreviewDiagnostics
    let bridgeDiagnostics: BridgeDiagnostics
    let accessDiagnostics: AccessDiagnostics
    let error: String?
    let closed: Bool
    let shutdownDiagnostics: ShutdownDiagnostics
    let actionWallTimeMs: Double?
    let canImport: Bool
    let canExport: Bool
    let queue: QueueSnapshot?
    let submissionErrors: [QueueRejection]
    let submitting: Bool
    let pendingSubmissionToken: String?
    let storagePreview: StoragePreviewResponse?
    let awaitingStorageConfirmation: Bool
}
struct WorkspaceSelfTestReport: Encodable, Sendable {
    let schemaVersion = 1
    let scope = "Production WorkspaceModel/CoreBridge/AVFoundation; MAC-002 compatibility or MAC-003 Core queue scenario driver, no simulated job or media success"
    let passed: Bool
    let steps: [WorkspaceStepEvidence]
    let heartbeatCount: Int
    let heartbeatMaxIntervalMs: Double
    let bridgeDiagnostics: BridgeDiagnostics
    let accessDiagnostics: AccessDiagnostics
    let closed: Bool
    let expectedCleanupFailure: Bool
}
@MainActor
private func workspaceEvidence(_ model: WorkspaceModel, action: String, cachedDiagnostics: Bool = false, actionWallTimeMs: Double? = nil) async -> WorkspaceStepEvidence {
    let bridgeDiagnostics = (cachedDiagnostics || model.isSubmitting) ? model.cachedBridgeDiagnostics : await model.diagnostics()
    return WorkspaceStepEvidence(action: action, phase: model.phase, assets: model.assets.map { asset in
        let range: MediaRange?
        if let start = parseTimeText(asset.startText), let end = parseTimeText(asset.endText), end > start { range = MediaRange(startUs: start, endUs: end) } else { range = nil }
        return WorkspaceAssetEvidence(id: asset.id, sourceName: asset.url.lastPathComponent, state: asset.state, range: range,
            startText: asset.startText, endText: asset.endText, mode: asset.mode, mediaInfo: asset.info, error: asset.failure, audioSelection: asset.audioSelection, includedInBatch: asset.includedInBatch)
    }, selectedAssetId: model.selectedAssetId, activeExport: model.activeExport, tasks: model.tasks.map { task in
        WorkspaceTaskEvidence(id: task.id, sourceAssetId: task.sourceAssetId, sourceName: task.sourceName, destinationName: task.destinationName,
            mode: task.mode, requestedRange: task.requestedRange, coreJobId: task.coreJobId, state: task.state,
            actualRange: task.actualRange, error: task.failure, result: task.result, interrupted: task.interrupted, audioSelection: task.audioSelection, sessionId: task.sessionId)
    }, core: model.snapshot, trace: model.trace, preview: model.preview.diagnostics, bridgeDiagnostics: bridgeDiagnostics,
       accessDiagnostics: model.accessDiagnostics, error: model.issue, closed: model.closed,
       shutdownDiagnostics: model.shutdownDiagnostics, actionWallTimeMs: actionWallTimeMs, canImport: model.canImport, canExport: model.canExport, queue: model.queueSnapshot, submissionErrors: model.submissionErrors, submitting: model.isSubmitting, pendingSubmissionToken: model.pendingQueue?.token,
       storagePreview: model.storagePreview?.response, awaitingStorageConfirmation: model.pendingQueueNeedsConfirmation)
}
@MainActor
func runWorkspaceSelfTest() async throws -> WorkspaceSelfTestReport {
    _ = NSApplication.shared
    let args = CommandLine.arguments
    guard let index = args.firstIndex(of: "--scenario"), index + 1 < args.count, args[index + 1].hasPrefix("/") else {
        throw CheckFailure(message: "--workspace-self-test requires --scenario /absolute/scenario.json")
    }
    let scenarioURL = URL(fileURLWithPath: args[index + 1])
    let size = try scenarioURL.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
    guard size <= 1_000_000 else { throw CheckFailure(message: "Scenario exceeds 1MB bound") }
    let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
    let scenario = try decoder.decode(WorkspaceScenario.self, from: Data(contentsOf: scenarioURL))
    guard scenario.schemaVersion == 1, scenario.historyPath.hasPrefix("/"), scenario.actions.count <= 500 else {
        throw CheckFailure(message: "Scenario schema/version/action bound invalid")
    }
    let historyURL = URL(fileURLWithPath: scenario.historyPath)
    var model = WorkspaceModel(historyPath: historyURL, queueEnabled: CommandLine.arguments.contains("--queue-self-test"))
    var heartbeatCount = 0
    var maxInterval = 0.0
    var lastHeartbeat = ContinuousClock.now
    let heartbeat = Task { @MainActor in
        while !Task.isCancelled {
            let now = ContinuousClock.now
            let duration = lastHeartbeat.duration(to: now).components
            maxInterval = max(maxInterval, Double(duration.seconds) * 1000 + Double(duration.attoseconds) / 1_000_000_000_000_000)
            lastHeartbeat = now; heartbeatCount += 1
            do { try await Task.sleep(for: .milliseconds(20)) } catch { break }
        }
    }
    var pendingExport: Task<Void, Never>?
    var pendingImport: Task<Void, Never>?
    var pendingSelections: [Task<Void, Never>] = []
    var steps: [WorkspaceStepEvidence] = []
    var totalBridge = BridgeDiagnostics()
    var totalAccess = AccessDiagnostics()
    var expectedCleanupFailure = false
    func absoluteURL(_ path: String?) throws -> URL {
        guard let path, path.hasPrefix("/") else { throw CheckFailure(message: "Action requires an absolute file path") }
        return URL(fileURLWithPath: path)
    }
    func aggregate(_ access: AccessDiagnostics) {
        totalAccess.starts += access.starts; totalAccess.granted += access.granted
        totalAccess.stops += access.stops; totalAccess.activeGranted += access.activeGranted; totalAccess.heldLeases += access.heldLeases
    }
    do {
        for action in scenario.actions {
            let actionStarted = ContinuousClock.now
            var useCachedDiagnostics = false
            var actionWallTimeMs: Double?
            switch action.action {
            case "import":
                guard let paths = action.paths, paths.count <= 1000, pendingImport == nil else { throw CheckFailure(message: "Invalid/concurrent import action") }
                let urls = try paths.map { try absoluteURL($0) }
                if action.wait == false { let current = model; pendingImport = Task { await current.importURLs(urls) }; await Task.yield() }
                else { await model.importURLs(urls) }
            case "select", "remove":
                guard let index = action.index, model.assets.indices.contains(index) else { throw CheckFailure(message: "Asset index outside current list") }
                let id = model.assets[index].id
                if action.action == "select" {
                    if action.wait == false { let current = model; pendingSelections.append(Task { await current.selectAsset(id: id) }); await Task.yield() }
                    else { await model.selectAsset(id: id) }
                } else { await model.removeAsset(id: id) }
            case "range":
                guard let start = action.startUs, let end = action.endUs else { throw CheckFailure(message: "Range action requires start_us/end_us") }
                model.startText = timeText(start); model.endText = timeText(end)
            case "mode":
                guard let raw = action.value, let mode = ExportStrategy(rawValue: raw), mode != .legacyStreamCopy else { throw CheckFailure(message: "Workspace supports streamCopyV1 or accurateReencode") }
                model.strategy = mode
            case "submission-boundary-observation":
                let marker = try absoluteURL(action.path)
                let boundary = action.value ?? "before-connect"
                let delay = min(5000, max(0, action.milliseconds ?? 500))
                model.queueSubmissionObserver = { stage in
                    guard stage == boundary else { return }
                    try? Data(stage.utf8).write(to: marker, options: .atomic)
                    try? await Task.sleep(for: .milliseconds(delay))
                }
            case "preset":
                guard let profile = action.value else { throw CheckFailure(message: "preset requires profile") }
                if profile == "mp4_h264_standard_v1" { model.strategy = .transcodeStandard }
                else if profile == "mp4_h264_720p_v1" { model.strategy = .transcode720p }
                else { throw CheckFailure(message: "Unknown fixed preset") }
            case "audio":
                switch action.value {
                case "all_supported": model.audioSelection = .all
                case "none": model.audioSelection = .none
                case "one": guard let index = action.streamIndex else { throw CheckFailure(message: "one requires stream_index") }; model.audioSelection = .one(index: index)
                default: throw CheckFailure(message: "Unknown audio choice")
                }
            case "storage-preview":
                guard let indices = action.indices, let paths = action.destinations,
                      indices.allSatisfy({ model.assets.indices.contains($0) }) else { throw CheckFailure(message: "storage-preview indices invalid") }
                model.saveSelectedEdits()
                let drafts = indices.compactMap { model.queueDraft(asset: model.assets[$0]) }
                guard drafts.count == indices.count else { throw CheckFailure(message: "storage-preview drafts invalid") }
                await model.submitQueue(drafts: drafts, destinations: try paths.map { try absoluteURL($0) }, previewSpace: true)
            case "storage-confirm": await model.confirmStoragePreview()
            case "storage-cancel": model.cancelStoragePreview()
            case "batch":
                guard let indices = action.indices, let paths = action.destinations,
                      indices.allSatisfy({ model.assets.indices.contains($0) }) else { throw CheckFailure(message: "batch indices invalid") }
                let ids = indices.map { model.assets[$0].id }
                let destinations = try paths.map { try absoluteURL($0) }
                if action.submissionWait == false {
                    guard pendingExport == nil else { throw CheckFailure(message: "Pending submission already exists") }
                    let current = model
                    pendingExport = Task { await current.submitBatch(assetIDs: ids, destinations: destinations) }
                    await Task.yield(); useCachedDiagnostics = true
                } else {
                    await model.submitBatch(assetIDs: ids, destinations: destinations)
                    if action.wait != false { await model.awaitQueue() }
                }
            case "cancel-pending": model.queueCancelRequested = true; useCachedDiagnostics = true
            case "await-submission": await pendingExport?.value; pendingExport = nil
            case "queue-refresh": await model.refreshQueue()
            case "await-queue": await model.awaitQueue()
            case "reorder":
                if action.value == "reverse" { await model.reorderQueue(taskIDs: model.queuedTasks.reversed().map(\.id)) }
                else {
                    guard let indices = action.indices, indices.allSatisfy({ model.tasks.indices.contains($0) }) else { throw CheckFailure(message: "reorder indices invalid") }
                    await model.reorderQueue(taskIDs: indices.map { model.tasks[$0].id })
                }
            case "cancel-task", "retry":
                let task: WorkspaceTask?
                if let value = action.value { task = model.tasks.first { $0.destinationName == value } }
                else if let index = action.index, model.tasks.indices.contains(index) { task = model.tasks[index] }
                else { task = nil }
                guard let task else { throw CheckFailure(message: "Task not found") }
                if action.action == "cancel-task" { await model.cancelQueue(taskID: task.id) }
                else { await model.retryTask(task.id, destination: try absoluteURL(action.destination)); if action.wait != false { await model.awaitQueue() } }
            case "export":
                guard pendingExport == nil else { throw CheckFailure(message: "Wait for prior export before driving another") }
                let url = try absoluteURL(action.destination)
                if action.wait == false {
                    let current = model
                    var finished = false
                    pendingExport = Task { await current.export(to: url); finished = true }
                    let deadline = ContinuousClock.now + .seconds(20)
                    while current.activeExport?.coreJobId == nil
                        && !(current.queueEnabled && current.tasks.contains(where: { !$0.terminal && $0.sessionId == current.queueSessionId }))
                        && !finished && ContinuousClock.now < deadline { try await Task.sleep(for: .milliseconds(5)) }
                } else { await model.export(to: url) }
            case "wait":
                if model.queueEnabled { await model.awaitQueue() }
                await pendingExport?.value; pendingExport = nil; await pendingImport?.value; pendingImport = nil
                if model.queueEnabled { await model.awaitQueue() }
                for selection in pendingSelections { await selection.value }; pendingSelections.removeAll()
            case "cancel": await model.cancel()
            case "cancel-and-wait":
                await model.cancel()
                if model.queueEnabled { await model.awaitQueue() }
                await pendingExport?.value; pendingExport = nil
                await pendingImport?.value; pendingImport = nil
            case "clear-history": if model.queueEnabled { await model.clearQueueHistory() } else { model.clearHistory() }
            case "play": model.preview.togglePlayback(); try await Task.sleep(for: .milliseconds(min(5000, max(0, action.milliseconds ?? 150)))); model.preview.pause()
            case "seek": try await model.preview.seek(normalizedUs: action.positionUs ?? 0)
            case "preview-frame": try await model.preview.writePreviewFrame(to: absoluteURL(action.destination), normalizedUs: action.positionUs ?? 0)
            case "sleep": try await Task.sleep(for: .milliseconds(min(5000, max(0, action.milliseconds ?? 20))))
            case "configure":
                if let ffmpeg = action.ffmpeg { model.ffmpegPath = ffmpeg }
                if let ffprobe = action.ffprobe { model.ffprobePath = ffprobe }
                await model.connect()
            case "wait-observation":
                let url = try absoluteURL(action.path)
                let deadline = ContinuousClock.now + .seconds(20)
                while ContinuousClock.now < deadline {
                    if let size = try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize, size <= 1_000_000,
                       let text = try? String(contentsOf: url, encoding: .utf8), text.contains(action.contains ?? "") { break }
                    try await Task.sleep(for: .milliseconds(20))
                }
                guard let text = try? String(contentsOf: url, encoding: .utf8), text.contains(action.contains ?? "") else { throw CheckFailure(message: "Observation deadline exceeded") }
            case "connect": await model.connect()
            case "shutdown-expect-timeout":
                let started = ContinuousClock.now
                do {
                    try await model.shutdown()
                    throw CheckFailure(message: "Expected global shutdown watchdog did not expire")
                } catch {
                    guard model.shutdownDiagnostics.watchdogTimeouts > 0, model.shutdownDiagnostics.cleanupPending,
                          !model.closed else { throw error }
                }
                let elapsed = started.duration(to: .now).components
                actionWallTimeMs = Double(elapsed.seconds) * 1000 + Double(elapsed.attoseconds) / 1_000_000_000_000_000
                useCachedDiagnostics = true
            case "shutdown-expect-cleanup-failure":
                do {
                    try await model.shutdown()
                    throw CheckFailure(message: "Expected cleanup failure was not observed")
                } catch {
                    guard model.unconfirmedCleanup, !model.closed,
                          model.shutdownDiagnostics.cleanupFailed,
                          model.accessDiagnostics.heldLeases > 0,
                          !model.canImport, !model.canExport else { throw error }
                }
                // Fault-only terminal observation: the live quarantined owner and
                // authorization intentionally remain held until this test process
                // exits. This is not a successful application close.
                expectedCleanupFailure = true
            case "restart", "shutdown", "await-shutdown":
                try await model.shutdown(); await pendingExport?.value; pendingExport = nil; await pendingImport?.value; pendingImport = nil
                for selection in pendingSelections { await selection.value }; pendingSelections.removeAll()
                if action.action == "restart" {
                    steps.append(await workspaceEvidence(model, action: "before-restart"))
                    totalBridge.accumulate(await model.diagnostics()); aggregate(model.accessDiagnostics)
                    model = WorkspaceModel(historyPath: historyURL, queueEnabled: CommandLine.arguments.contains("--queue-self-test"))
                }
            default: throw CheckFailure(message: "Unknown scenario action \(action.action)")
            }
            if actionWallTimeMs == nil {
                let elapsed = actionStarted.duration(to: .now).components
                actionWallTimeMs = Double(elapsed.seconds) * 1000 + Double(elapsed.attoseconds) / 1_000_000_000_000_000
            }
            steps.append(await workspaceEvidence(model, action: action.action, cachedDiagnostics: useCachedDiagnostics, actionWallTimeMs: actionWallTimeMs))
            if expectedCleanupFailure { break }
        }
        if !model.closed && !expectedCleanupFailure { try await model.shutdown() }
        await pendingExport?.value; await pendingImport?.value
        for selection in pendingSelections { await selection.value }; pendingSelections.removeAll()
        steps.append(await workspaceEvidence(model, action: expectedCleanupFailure ? "expected-cleanup-failure-retained" : "final-shutdown"))
        totalBridge.accumulate(await model.diagnostics()); aggregate(model.accessDiagnostics)
    } catch {
        try? await model.shutdown(); await pendingExport?.value; await pendingImport?.value
        for selection in pendingSelections { await selection.value }; pendingSelections.removeAll()
        heartbeat.cancel(); await heartbeat.value; throw error
    }
    heartbeat.cancel(); await heartbeat.value
    let ownershipConfirmed = expectedCleanupFailure
        ? !model.closed && model.unconfirmedCleanup && totalAccess.heldLeases > 0 && !model.canImport && !model.canExport
        : model.closed && totalAccess.activeGranted == 0 && totalAccess.heldLeases == 0 && totalAccess.granted == totalAccess.stops
    let passed = ownershipConfirmed && totalBridge.allCallsOffMainThread && totalBridge.outputCalls == totalBridge.bufferReleaseCalls
    return WorkspaceSelfTestReport(passed: passed, steps: steps, heartbeatCount: heartbeatCount, heartbeatMaxIntervalMs: maxInterval,
                                  bridgeDiagnostics: totalBridge, accessDiagnostics: totalAccess, closed: model.closed,
                                  expectedCleanupFailure: expectedCleanupFailure)
}
