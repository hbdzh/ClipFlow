import AppKit
import Foundation

struct MediaSelfTestReport: Encodable, Sendable {
    let schemaVersion = 0
    let scope = "UI-001 same native WorkspaceModel/CoreBridge/AVFoundation preview; no simulated successful media"
    let passed: Bool
    let expectedState: String
    let connection: CoreConnection?
    let probe: JobSnapshot?
    let trace: [JobSnapshot]
    let final: JobSnapshot?
    let preview: PreviewDiagnostics
    let heartbeatCount: Int
    let bridgeDiagnostics: BridgeDiagnostics
    let accessDiagnostics: AccessDiagnostics
    let outputUri: String
    let frameUri: String?
    let error: String?
    let rejected: Bool
    let closed: Bool
}

@MainActor
func runMediaSelfTest() async throws -> MediaSelfTestReport {
    _ = NSApplication.shared
    let args = CommandLine.arguments
    func argument(_ name: String) -> String? {
        guard let index = args.firstIndex(of: name), index + 1 < args.count else { return nil }
        return args[index + 1]
    }
    guard let source = argument("--source"), let destination = argument("--destination"),
          source.hasPrefix("/"), destination.hasPrefix("/") else {
        throw CheckFailure(message: "--media-self-test requires absolute --source and --destination")
    }
    let sourceURL = URL(fileURLWithPath: source), destinationURL = URL(fileURLWithPath: destination)
    let frameURL = argument("--preview-frame").map { URL(fileURLWithPath: $0) }
    if let frameURL {
        guard frameURL.path.hasPrefix("/"), frameURL.pathExtension.lowercased() == "png",
              !FileManager.default.fileExists(atPath: frameURL.path),
              frameURL.standardizedFileURL != sourceURL.standardizedFileURL,
              frameURL.standardizedFileURL != destinationURL.standardizedFileURL else {
            throw CheckFailure(message: "Preview frame must be a new separate absolute PNG")
        }
    }
    let model = WorkspaceModel(legacyCompatibility: true)
    var heartbeat = 0
    let heartbeatTask = Task { @MainActor in
        while !Task.isCancelled {
            heartbeat += 1
            do { try await Task.sleep(for: .milliseconds(20)) } catch { break }
        }
    }
    let expected = argument("--expect-state") ?? "succeeded"
    let expectImportFailure = args.contains("--expect-import-failure")
    await model.importURL(sourceURL)
    let connection = model.connection
    var rejected = false
    var capturedPreview: PreviewDiagnostics?
    if model.mediaInfo != nil, !expectImportFailure {
        let start = Int64(argument("--start-us") ?? "1100000") ?? -1
        let end = Int64(argument("--end-us") ?? "2600000") ?? -1
        model.startText = timeText(start); model.endText = timeText(end)
        if model.preview.ready {
            try await model.preview.seek(normalizedUs: max(0, start))
            if let frameURL { try await model.preview.writePreviewFrame(to: frameURL, normalizedUs: max(0, start)) }
            model.preview.togglePlayback()
            try await Task.sleep(for: .milliseconds(150))
            model.preview.pause()
        }
        var exportFinished = false
        let exportTask = Task { @MainActor in await model.export(to: destinationURL); exportFinished = true }
        if let cancelText = argument("--cancel-after-ms"), let cancel = UInt64(cancelText) {
            let deadline = ContinuousClock.now + .seconds(5)
            while model.snapshot?.operation != "trim" && !exportFinished && ContinuousClock.now < deadline {
                try await Task.sleep(for: .milliseconds(5))
            }
            try await Task.sleep(for: .milliseconds(Int64(min(cancel, 300000))))
            await model.cancel()
        }
        if args.contains("--close-during-job") {
            let close = UInt64(argument("--close-after-ms") ?? "0") ?? 0
            let deadline = ContinuousClock.now + .seconds(5)
            while model.snapshot?.operation != "trim" && !exportFinished && ContinuousClock.now < deadline {
                try await Task.sleep(for: .milliseconds(5))
            }
            try await Task.sleep(for: .milliseconds(Int64(min(close, 300000))))
            capturedPreview = model.preview.diagnostics
            try await model.shutdown()
        }
        await exportTask.value
        rejected = model.snapshot?.operation != "trim" && model.issue != nil
    }
    let final = model.snapshot?.operation == "trim" ? model.snapshot : nil
    let preview = capturedPreview ?? model.preview.diagnostics
    let issue = model.issue
    let probe = model.probeSnapshot
    let trace = model.trace
    let imported = model.mediaInfo != nil
    if !model.closed { try await model.shutdown() }
    heartbeatTask.cancel(); await heartbeatTask.value
    let diagnostics = await model.diagnostics()
    let access = model.accessDiagnostics
    let matched: Bool
    if expectImportFailure { matched = !imported && issue != nil }
    else if expected == "rejected" { matched = rejected }
    else { matched = final?.state == expected }
    let positivePreview = expected != "succeeded" || expectImportFailure || (preview.ready && preview.seekCompleted && preview.firstVisibleImageConfirmed)
    let passed = matched && positivePreview && model.closed && diagnostics.allCallsOffMainThread
        && diagnostics.outputCalls == diagnostics.bufferReleaseCalls && access.activeGranted == 0 && access.granted == access.stops
    return MediaSelfTestReport(passed: passed, expectedState: expectImportFailure ? "import_failed" : expected,
        connection: connection, probe: probe, trace: trace, final: final, preview: preview,
        heartbeatCount: heartbeat, bridgeDiagnostics: diagnostics, accessDiagnostics: access,
        outputUri: destinationURL.absoluteString, frameUri: frameURL?.absoluteString,
        error: issue, rejected: rejected, closed: model.closed)
}
