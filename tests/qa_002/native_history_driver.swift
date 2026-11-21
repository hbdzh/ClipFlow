import AppKit
import Foundation

@main struct HistoryProbe {
    @MainActor static func main() async throws {
        _ = NSApplication.shared
        let args = CommandLine.arguments
        guard args.count >= 3 else { throw CheckFailure(message: "mode and private QA work directory required") }
        let mode = args[1], work = URL(fileURLWithPath: args[2], isDirectory: true)
        var rows: [[String: Any]] = []
        func check(_ value: Bool, _ name: String, _ detail: [String: Any] = [:]) {
            rows.append(["name": name, "result": value ? "PASS" : "FAIL", "detail": detail])
        }
        func task(_ state: String = "succeeded", index: Int = 0) -> WorkspaceTask {
            WorkspaceTask(id: UUID(), sourceAssetId: UUID(), sourceName: "source-\(index).mp4", destinationName: "result-\(index).mp4", mode: .streamCopyV1, requestedRange: MediaRange(startUs: 100_000, endUs: 900_000), createdAt: Date(timeIntervalSince1970: Double(1_000 + index)), coreJobId: "old-job-\(index)", state: state, sessionId: UUID())
        }
        if mode == "red" {
            try FileManager.default.createDirectory(at: work, withIntermediateDirectories: false)
            let history = work.appendingPathComponent("history.json")
            let model = WorkspaceModel(historyPath: history, queueEnabled: false)
            let result = work.appendingPathComponent("result-0.mp4")
            try Data("QA owned file, not a real successful media job".utf8).write(to: result, options: .withoutOverwriting)
            var item = task(); item.destinationBookmark = RecentTaskStore.bookmark(result); model.tasks = [item]
            check(item.destinationBookmark != nil, "native-bookmark-created")
            try FileManager.default.setAttributes([.posixPermissions: 0], ofItemAtPath: result.path)
            check(!FileManager.default.isReadableFile(atPath: result.path), "actual-result-read-denied")
            check(model.resultURL(item.id) == nil, "unreadable-bookmarked-result-requires-reauthorization")
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: result.path)
            model.resultURLs[item.id] = result
            try FileManager.default.removeItem(at: result)
            try FileManager.default.createDirectory(at: result, withIntermediateDirectories: false)
            check(model.resultURL(item.id) == nil, "replaced-result-directory-requires-reauthorization")
            try FileManager.default.removeItem(at: result)
            try Data("QA owned file".utf8).write(to: result, options: .withoutOverwriting)
            try RecentTaskStore(path: history).save([item])
            try FileManager.default.setAttributes([.posixPermissions: 0o500], ofItemAtPath: work.path)
            model.clearHistory()
            check(model.tasks.count == 1, "failed-clear-retains-visible-history", ["count": model.tasks.count])
            check(!model.status.contains("已清除"), "failed-clear-does-not-claim-success", ["status": model.status])
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: work.path)
            check(model.events.allSatisfy { !$0.contains(work.path) }, "failure-events-redact-private-path")
            try await model.shutdown()
        } else if mode == "seed" {
            try FileManager.default.createDirectory(at: work, withIntermediateDirectories: false)
            let history = work.appendingPathComponent("history.json")
            let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
            let result = try decoder.decode(JobResult.self, from: Data("{\"mock_only\":true,\"file_created\":false}".utf8))
            var values = (0..<110).map { task($0 < 3 ? ["queued", "running", "cancelling"][$0] : "succeeded", index: $0) }
            values[0].result = result
            try RecentTaskStore(path: history).save(values)
            let bytes = try Data(contentsOf: history)
            let saved = try JSONSerialization.jsonObject(with: bytes) as! [[String: Any]]
            check(saved.count == 100, "saved-history-bounded-at100", ["count": saved.count])
            let allowed: Set<String> = ["id", "sourceAssetId", "sourceName", "destinationName", "mode", "requestedRange", "createdAt", "coreJobId", "state", "actualRange", "failure", "destinationBookmark", "interrupted", "sessionId", "audioSelection"]
            check(saved.allSatisfy { Set($0.keys).isSubset(of: allowed) && $0["result"] == nil }, "history-only-bounded-summary-no-Core-result-or-handle")
            check(!String(decoding: bytes, as: UTF8.self).contains(work.path), "history-has-no-raw-source-or-destination-path")
            check(saved[0]["state"] as? String == "queued" && saved[1]["state"] as? String == "running" && saved[2]["state"] as? String == "cancelling", "unfinished-states-written-for-next-process")
        } else if mode == "restart" {
            let history = work.appendingPathComponent("history.json")
            let model = WorkspaceModel(historyPath: history)
            check(model.tasks.count == 100, "restart-loads100-bounded-records")
            check(model.tasks.prefix(3).allSatisfy { $0.state == "interrupted" && $0.interrupted && $0.result == nil }, "queued-running-cancelling-restart-as-interrupted")
            check(model.tasks.dropFirst(3).allSatisfy { $0.state == "succeeded" }, "terminal-summary-states-retained")
            check(model.tasks.allSatisfy { $0.sessionId != model.queueSessionId } && model.assets.isEmpty && model.queueRequests.isEmpty && model.queueLeases.isEmpty && model.connection == nil && model.queueSnapshot == nil, "restart-does-not-recreate-session-assets-native-handles-or-requests")
            let before = await model.diagnostics()
            check(before.outputCalls == 0, "restart-makes-no-Core-calls")
            await model.retryTask(model.tasks[0].id, destination: work.appendingPathComponent("must-not-run.mp4"))
            let after = await model.diagnostics()
            check(after.outputCalls == 0 && !FileManager.default.fileExists(atPath: work.appendingPathComponent("must-not-run.mp4").path) && model.issue?.contains("重新导入") == true, "historical-job-ID-not-retried-reimport-required")
            await model.clearQueueHistory()
            check(model.tasks.isEmpty && model.status.contains("已清除"), "clear-history-confirmed-with-no-live-Core-session")
            try await model.shutdown()
        } else if mode == "empty" {
            let model = WorkspaceModel(historyPath: work.appendingPathComponent("history.json"))
            check(model.tasks.isEmpty, "cleared-history-stays-empty-in-third-process")
            let diagnostics = await model.diagnostics()
            check(diagnostics.outputCalls == 0, "empty-restart-no-Core-recovery")
            try await model.shutdown()
        } else if mode == "files" {
            let directory = work.appendingPathComponent("files", isDirectory: true)
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false)
            let model = WorkspaceModel(historyPath: directory.appendingPathComponent("history.json"))
            var item = task(); item.destinationBookmark = nil
            let result = directory.appendingPathComponent(item.destinationName)
            try Data("QA result bytes".utf8).write(to: result, options: .withoutOverwriting)
            model.tasks = [item]; model.resultURLs[item.id] = result
            check(model.resultURL(item.id) == result, "cached-readable-regular-result-available")
            let movedParent = directory.appendingPathComponent("moved", isDirectory: true)
            try FileManager.default.createDirectory(at: movedParent, withIntermediateDirectories: false)
            let moved = movedParent.appendingPathComponent(item.destinationName)
            try FileManager.default.moveItem(at: result, to: moved)
            check(model.resultURL(item.id) == nil, "moved-cached-result-not-falsely-available")
            model.openResult(taskId: item.id)
            check(model.issue?.contains("重新授权") == true, "moved-result-open-prompts-reauthorization-without-opening-system-app")
            model.revealResult(taskId: item.id)
            check(model.issue?.contains("重新授权") == true, "moved-result-reveal-prompts-reauthorization-without-Finder")
            check(model.authorizeResult(taskId: item.id, url: moved), "same-name-moved-result-explicit-reauthorization")
            check(model.resultURL(item.id) == moved, "reauthorized-result-available")
            let wrong = directory.appendingPathComponent("wrong.mp4")
            try Data("QA wrong filename".utf8).write(to: wrong, options: .withoutOverwriting)
            check(!model.authorizeResult(taskId: item.id, url: wrong), "wrong-name-reauthorization-refused")
            let before = model.tasks[0].destinationBookmark
            try FileManager.default.setAttributes([.posixPermissions: 0o500], ofItemAtPath: directory.path)
            check(!model.authorizeResult(taskId: item.id, url: moved) && model.tasks[0].destinationBookmark == before, "reauthorization-save-failure-keeps-old-record")
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: directory.path)
            let linkParent = directory.appendingPathComponent("links", isDirectory: true)
            try FileManager.default.createDirectory(at: linkParent, withIntermediateDirectories: false)
            let link = linkParent.appendingPathComponent(item.destinationName)
            try FileManager.default.createSymbolicLink(at: link, withDestinationURL: moved)
            check(!model.authorizeResult(taskId: item.id, url: link), "symlink-reauthorization-refused")
            model.tasks[0].state = "failed"
            check(!model.authorizeResult(taskId: item.id, url: moved), "changed-task-state-reauthorization-refused")
            model.tasks = []
            check(!model.authorizeResult(taskId: item.id, url: moved), "removed-task-reauthorization-refused")
            check(model.accessDiagnostics.heldLeases == 0 && model.accessDiagnostics.activeGranted == 0 && model.accessDiagnostics.granted == model.accessDiagnostics.stops, "all-result-scopes-balanced")
            try await model.shutdown()
        } else if mode == "queue-fault" {
            let directory = work.appendingPathComponent("queue-fault", isDirectory: true)
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false)
            let model = WorkspaceModel(historyPath: directory.appendingPathComponent("history.json"))
            model.tasks = [task()]; model.persistTasks()
            try FileManager.default.setAttributes([.posixPermissions: 0o500], ofItemAtPath: directory.path)
            await model.clearQueueHistory()
            check(model.tasks.count == 1 && model.status.contains("未确认"), "queued-mode-failed-clear-retains-record-and-refuses-success")
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: directory.path)
            await model.clearQueueHistory()
            check(model.tasks.isEmpty && RecentTaskStore(path: directory.appendingPathComponent("history.json")).load().isEmpty, "queued-mode-clear-recovers-after-storage-fixed")
            try await model.shutdown()
        } else if mode == "unified" {
            try FileManager.default.createDirectory(at: work, withIntermediateDirectories: false)
            let history = work.appendingPathComponent("unified.json")
            let suite = "MediaStudio.HistoryQA." + UUID().uuidString
            let defaults = UserDefaults(suiteName: suite)!
            defer { defaults.removePersistentDomain(forName: suite) }
            let legacyKey = "MediaStudio.mac002.recentTasks.v1"
            defaults.set(try JSONEncoder().encode((0..<100).map { task(index: $0) }), forKey: legacyKey)
            let ledger = RecentTaskLedger(path: history, defaults: defaults)
            check(ledger.videoTasks.count == 100, "unified-migrates-legacy-video-array")
            let model = WorkspaceModel(historyLedger: ledger)
            let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
            model.tasks[0].result = try decoder.decode(JobResult.self, from: Data("{\"mock_only\":true,\"file_created\":false}".utf8))
            let liveID = model.tasks[0].id
            let oldID = model.tasks.last!.id
            let aiID = try ledger.beginAI(sourceName: "QA source.wav")
            check(ledger.videoTasks.count + ledger.aiTasks.count == 100 && ledger.aiTasks.count == 1 && !ledger.videoTasks.contains { $0.id == oldID }, "global101-evicts-oldest-terminal-across-types")
            check(model.tasks.count == 99 && model.tasks.first(where: { $0.id == liveID })?.result != nil, "ledger-ID-pruning-preserves-live-Core-result")
            check(defaults.data(forKey: legacyKey) == nil && FileManager.default.fileExists(atPath: history.path), "legacy-key-removed-only-after-file-commit")
            let bytes = try Data(contentsOf: history)
            let object = try JSONSerialization.jsonObject(with: bytes) as! [String: Any]
            let ai = (object["ai"] as! [[String: Any]])[0]
            check(object["schemaVersion"] as? Int == 2 && ai["mode"] == nil && ai["coreJobId"] == nil && ai["sessionId"] == nil, "AI-summary-has-own-typed-schema-without-video-strategy-or-old-job")
            check(!String(decoding: bytes, as: UTF8.self).contains(work.path), "unified-summary-has-no-raw-path")
            try ledger.updateAI(aiID, state: "succeeded")
            let result = work.appendingPathComponent("QA stems", isDirectory: true)
            try FileManager.default.createDirectory(at: result, withIntermediateDirectories: false)
            for name in ["vocals.wav", "accompaniment.wav"] { try Data("QA access fixture only; not separation quality".utf8).write(to: result.appendingPathComponent(name)) }
            try ledger.recordAIExport(aiID, directory: result)
            let scoped = ledger.withAIResultDirectory(aiID) { $0.standardizedFileURL.path == result.standardizedFileURL.path && ledger.activeResultAccesses == 1 }
            check(scoped == true && ledger.activeResultAccesses == 0, "AI-result-two-stems-validated-and-used-under-same-balanced-scope", ["callback": scoped as Any? ?? "nil", "bookmarkResolved": RecentTaskStore.resolve(ledger.aiTasks.first(where: { $0.id == aiID })?.resultBookmark) != nil])
            let accompaniment = result.appendingPathComponent("accompaniment.wav")
            try FileManager.default.removeItem(at: accompaniment)
            check(ledger.withAIResultDirectory(aiID, { _ in true }) == nil, "missing-one-stem-requires-reauthorization")
            try FileManager.default.createSymbolicLink(at: accompaniment, withDestinationURL: result.appendingPathComponent("vocals.wav"))
            do { try ledger.reauthorizeAIResult(aiID, directory: result); check(false, "AI-symlink-stem-refused") } catch { check(true, "AI-symlink-stem-refused") }
            try FileManager.default.removeItem(at: accompaniment)
            try Data("QA own file".utf8).write(to: accompaniment)
            try FileManager.default.setAttributes([.posixPermissions: 0], ofItemAtPath: accompaniment.path)
            check(ledger.withAIResultDirectory(aiID, { _ in true }) == nil, "real-stem-read-denial-requires-reauthorization")
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: accompaniment.path)
            try ledger.reauthorizeAIResult(aiID, directory: result)
            let previousCount = ledger.videoTasks.count + ledger.aiTasks.count
            try FileManager.default.setAttributes([.posixPermissions: 0o500], ofItemAtPath: work.path)
            do { try ledger.clearTerminated(); check(false, "unified-real-write-denial-retains-all-summaries") }
            catch { check(ledger.videoTasks.count + ledger.aiTasks.count == previousCount && ledger.persistenceIssue?.contains(work.path) != true, "unified-real-write-denial-retains-all-summaries") }
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: work.path)
            for state in ["queued", "running", "cancelling"] { let id = try ledger.beginAI(sourceName: "QA \(state).wav"); try ledger.updateAI(id, state: state) }
            let activeVideo = task("running", index: -1)
            try ledger.saveVideos([activeVideo] + ledger.videoTasks)
            for index in 0..<10 { let id = try ledger.beginAI(sourceName: "newer-\(index).wav"); try ledger.updateAI(id, state: "succeeded") }
            check(ledger.videoTasks.contains { $0.id == activeVideo.id && !$0.terminal } && ledger.aiTasks.filter { !$0.terminal }.count == 3 && ledger.videoTasks.count + ledger.aiTasks.count == 100, "old-active-records-survive-newer-terminal-pressure")
            // Do not call shutdown here: the next executable process tests abrupt persistence boundaries.
        } else if mode == "unified-restart" {
            let ledger = RecentTaskLedger(path: work.appendingPathComponent("unified.json"))
            let model = WorkspaceModel(historyLedger: ledger)
            check(ledger.aiTasks.filter(\.interrupted).count == 3 && ledger.aiTasks.filter(\.interrupted).allSatisfy { $0.state == "interrupted" } && model.tasks.filter(\.interrupted).count == 1, "new-process-AI-and-video-queued-running-cancelling-become-interrupted")
            let diagnostics = await model.diagnostics()
            check(diagnostics.outputCalls == 0 && model.queueRequests.isEmpty && model.connection == nil, "unified-restart-never-reuses-engine-job-or-resumes-work")
            try ledger.clearTerminated()
            check(ledger.aiTasks.isEmpty && ledger.videoTasks.isEmpty && model.tasks.isEmpty, "global-clear-updates-both-observable-native-models")
            try await model.shutdown()
        } else if mode == "real-crash" {
            let ledger = RecentTaskLedger(path: work.appendingPathComponent("real-history.json"))
            let model = WorkspaceModel(historyLedger: ledger)
            model.ffmpegPath = work.appendingPathComponent("gate/ffmpeg-hold").path
            model.ffprobePath = "/opt/homebrew/bin/ffprobe"
            await model.connect()
            await model.importURL(work.appendingPathComponent("source.mp4"))
            guard let asset = model.assets.first, asset.state == "succeeded", asset.info != nil else { throw CheckFailure(message: "real crash source import failed") }
            model.startText = "1.100000"; model.endText = "2.600000"; model.strategy = .accurateReencode
            await model.submitBatch(assetIDs: [asset.id], destinations: [work.appendingPathComponent("must-not-publish.mp4")])
            guard model.tasks.count == 1 else { throw CheckFailure(message: "real submit failed: \(model.issue ?? model.status)") }
            let deadline = ContinuousClock.now + .seconds(30)
            while ContinuousClock.now < deadline {
                await model.refreshQueue()
                if model.tasks.first?.state == "running", let data = try? Data(contentsOf: work.appendingPathComponent("gate/events.jsonl")), String(decoding: data, as: UTF8.self).contains("staging-ready") {
                    try Data("real Core running and actual nonempty stage observed".utf8).write(to: work.appendingPathComponent("crash-ready"))
                    while true { try await Task.sleep(for: .milliseconds(50)) }
                }
                try await Task.sleep(for: .milliseconds(20))
            }
            throw CheckFailure(message: "real encoding observation timed out")
        } else if mode == "real-restart" {
            let ledger = RecentTaskLedger(path: work.appendingPathComponent("real-history.json"))
            let model = WorkspaceModel(historyLedger: ledger)
            check(model.tasks.count == 1 && model.tasks[0].state == "interrupted" && model.tasks[0].interrupted, "actual-Core-running-SIGKILL-restarts-as-interrupted")
            let before = await model.diagnostics()
            check(before.outputCalls == 0 && model.queueRequests.isEmpty && model.connection == nil && !model.closed && !model.shutdownDiagnostics.cleanupCompleted, "real-crash-restart-neither-resumes-old-Core-nor-claims-cleanup")
            if let item = model.tasks.first { await model.retryTask(item.id, destination: work.appendingPathComponent("must-not-retry.mp4")) }
            check(model.issue?.contains("重新导入") == true && !FileManager.default.fileExists(atPath: work.appendingPathComponent("must-not-retry.mp4").path), "actual-old-job-requires-reimport-not-ID-reuse")
            try await model.shutdown()
        } else if mode == "unified-empty" {
            let ledger = RecentTaskLedger(path: work.appendingPathComponent("unified.json"))
            check(ledger.videoTasks.isEmpty && ledger.aiTasks.isEmpty && ledger.persistenceIssue == nil, "unified-cleared-history-stays-empty-next-process")
        } else { throw CheckFailure(message: "unknown mode") }
        let report: [String: Any] = ["scope": "Actual production persistence/model and Foundation bookmarks; seeded QA summaries, not fabricated Core or GUI success", "mode": mode, "result": rows.allSatisfy { $0["result"] as? String == "PASS" } ? "PASS" : "FAIL", "checks": rows]
        FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]))
        FileHandle.standardOutput.write(Data("\n".utf8))
    }
}
