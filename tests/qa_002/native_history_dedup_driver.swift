import Combine
import Foundation

/// Silent filesystem-only QA. No application, player, speech or media job.
@main struct HistoryDedupProbe {
    @MainActor static func main() throws {
        let args = CommandLine.arguments
        guard args.count == 3 else { throw CheckFailure(message: "private QA mode and directory required") }
        let mode = args[1], work = URL(fileURLWithPath: args[2], isDirectory: true)
        let isolatedSuite = "MediaStudio.HistoryDedupIsolated." + UUID().uuidString
        let isolatedDefaults = UserDefaults(suiteName: isolatedSuite)!
        defer { isolatedDefaults.removePersistentDomain(forName: isolatedSuite) }
        var rows: [[String: Any]] = []
        func check(_ ok: Bool, _ name: String) { rows.append(["name": name, "result": ok ? "PASS" : "FAIL", "detail": [:]]) }
        func signature(_ file: URL) throws -> String {
            let info = try FileManager.default.attributesOfItem(atPath: file.path)
            return "\(info[.systemFileNumber]!)|\((info[.modificationDate] as! Date).timeIntervalSince1970)"
        }
        if mode == "dedup" {
            try FileManager.default.createDirectory(at: work, withIntermediateDirectories: false)
            let path = work.appendingPathComponent("history.json")
            let ledger = RecentTaskLedger(path: path, defaults: isolatedDefaults)
            var video = WorkspaceTask(id: UUID(), sourceAssetId: UUID(), sourceName: "QA source.mp4", destinationName: "QA result.mp4", mode: .streamCopyV1, requestedRange: MediaRange(startUs: 0, endUs: 1_000_000), createdAt: Date(timeIntervalSince1970: 1000), state: "running")
            try ledger.saveVideos([video])
            var videoPublications = 0, aiPublications = 0, idPublications = 0
            let videoObserver = ledger.$videoTasks.dropFirst().sink { _ in videoPublications += 1 }
            let aiObserver = ledger.$aiTasks.dropFirst().sink { _ in aiPublications += 1 }
            let idObserver = ledger.videoIDsDidChange.sink { _ in idPublications += 1 }
            let original = try signature(path)
            for _ in 0..<20 { try ledger.saveVideos([video]) }
            check(try signature(path) == original, "unchanged-video-summary-keeps-inode-and-mtime")
            check(videoPublications == 0 && aiPublications == 0 && idPublications == 0, "unchanged-document-does-not-republish")
            video.state = "succeeded"
            try ledger.saveVideos([video])
            check(try signature(path) != original && ledger.videoTasks[0].state == "succeeded", "changed-video-state-still-atomically-replaces-file")
            let aiID = try ledger.beginAI(sourceName: "QA source.wav")
            try ledger.updateAI(aiID, state: "running")
            let combined = try signature(path), publicationCount = idPublications
            try ledger.saveVideos([video]); try ledger.updateAI(aiID, state: "running")
            check(try signature(path) == combined && idPublications == publicationCount, "full-AI-video-document-deduplicates-both-entrypoints")
            try ledger.updateAI(aiID, state: "succeeded")
            check(try signature(path) != combined && ledger.aiTasks[0].state == "succeeded", "AI-state-change-is-not-hidden-by-video-equality")
            try FileManager.default.removeItem(at: path)
            try ledger.saveVideos([video])
            check(FileManager.default.fileExists(atPath: path.path), "externally-deleted-file-is-rewritten-despite-identical-summary")
            let external = Data("QA owned external replacement".utf8)
            try external.write(to: path, options: .atomic)
            let replaced = try signature(path)
            try ledger.saveVideos([video])
            check(try signature(path) != replaced && Data(contentsOf: path) != external, "externally-replaced-file-is-rewritten-despite-identical-summary")
            let linkTarget = work.appendingPathComponent("link-target.json")
            try FileManager.default.moveItem(at: path, to: linkTarget)
            let targetBytes = try Data(contentsOf: linkTarget), publicationsBeforeLink = idPublications
            try FileManager.default.createSymbolicLink(at: path, withDestinationURL: linkTarget)
            do { try ledger.saveVideos([video]); check(false, "externally-symlinked-history-is-refused-without-touching-target-or-UI") }
            catch { check(try Data(contentsOf: linkTarget) == targetBytes && path.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink == true && idPublications == publicationsBeforeLink, "externally-symlinked-history-is-refused-without-touching-target-or-UI") }
            try FileManager.default.removeItem(at: path)
            try FileManager.default.moveItem(at: linkTarget, to: path)
            let beforeFailure = try Data(contentsOf: path), failureSignature = try signature(path), beforePublications = idPublications
            try FileManager.default.setAttributes([.posixPermissions: 0o500], ofItemAtPath: work.path)
            defer { try? FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: work.path) }
            do { try ledger.updateAI(aiID, state: "failed"); check(false, "changed-document-write-denial-retains-file-and-visible-state") }
            catch { check(try Data(contentsOf: path) == beforeFailure && signature(path) == failureSignature && ledger.aiTasks[0].state == "succeeded" && idPublications == beforePublications, "changed-document-write-denial-retains-file-and-visible-state") }
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: work.path)
            try ledger.updateAI(aiID, state: "failed")
            check(try signature(path) != failureSignature && ledger.aiTasks[0].state == "failed", "failed-write-does-not-poison-successful-byte-cache")
            let priorSuccess = try signature(path)
            try FileManager.default.setAttributes([.posixPermissions: 0o500], ofItemAtPath: work.path)
            do { try ledger.updateAI(aiID, state: "succeeded") } catch { /* real failure before retry of cached successful state */ }
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: work.path)
            try ledger.updateAI(aiID, state: "failed")
            check(try signature(path) != priorSuccess && ledger.persistenceIssue == nil, "return-to-last-successful-content-after-failure-retries-IO-and-clears-error")
            try FileManager.default.setAttributes([.posixPermissions: 0o500], ofItemAtPath: work.path)
            do { try ledger.clearTerminated(); check(false, "clear-write-denial-is-never-swallowed") }
            catch { check(ledger.videoTasks.count == 1 && ledger.aiTasks.count == 1, "clear-write-denial-is-never-swallowed") }
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: work.path)
            try ledger.clearTerminated()
            check(ledger.videoTasks.isEmpty && ledger.aiTasks.isEmpty, "clear-removes-both-kinds-after-storage-recovers")
            let emptySignature = try signature(path)
            try ledger.clearTerminated()
            check(try signature(path) != emptySignature, "explicit-clear-bypasses-identical-byte-cache")

            let migrationPath = work.appendingPathComponent("migrated.json")
            let suite = "MediaStudio.HistoryDedupQA." + UUID().uuidString
            let defaults = UserDefaults(suiteName: suite)!
            defer { defaults.removePersistentDomain(forName: suite) }
            let key = "MediaStudio.mac002.recentTasks.v1"
            defaults.set(try JSONEncoder().encode([video]), forKey: key)
            let migrated = RecentTaskLedger(path: migrationPath, defaults: defaults)
            try migrated.saveVideos(migrated.videoTasks)
            check(FileManager.default.fileExists(atPath: migrationPath.path) && defaults.data(forKey: key) == nil, "migration-unchanged-summary-still-commits-and-removes-legacy-key")
            let corruptPath = work.appendingPathComponent("corrupt.json")
            try Data("not JSON".utf8).write(to: corruptPath)
            let corrupt = RecentTaskLedger(path: corruptPath, defaults: isolatedDefaults)
            do { try corrupt.saveVideos([]); check(false, "load-failure-cannot-be-hidden-by-empty-summary") }
            catch { check(try Data(contentsOf: corruptPath) == Data("not JSON".utf8) && corrupt.persistenceIssue != nil, "load-failure-cannot-be-hidden-by-empty-summary") }
            try corrupt.clearTerminated()
            check(corrupt.persistenceIssue == nil && RecentTaskLedger(path: corruptPath, defaults: isolatedDefaults).persistenceIssue == nil, "explicit-clear-recovers-invalid-store-with-real-write")
            let parent = work.appendingPathComponent("parent-boundary", isDirectory: true)
            try FileManager.default.createDirectory(at: parent, withIntermediateDirectories: false)
            let parentHistory = parent.appendingPathComponent("history.json")
            let parentLedger = RecentTaskLedger(path: parentHistory, defaults: isolatedDefaults)
            try parentLedger.saveVideos([video])
            let parentBytes = try Data(contentsOf: parentHistory), parentSignature = try signature(parentHistory)
            var parentPublications = 0
            let parentObserver = parentLedger.videoIDsDidChange.sink { _ in parentPublications += 1 }
            let movedParent = work.appendingPathComponent("moved-parent-boundary", isDirectory: true)
            try FileManager.default.moveItem(at: parent, to: movedParent)
            try FileManager.default.createSymbolicLink(at: parent, withDestinationURL: movedParent)
            var refusals = 0
            do { try parentLedger.saveVideos([video]) } catch { refusals += 1 }
            var changed = video; changed.state = "failed"
            do { try parentLedger.saveVideos([changed]) } catch { refusals += 1 }
            check(try refusals == 2 && Data(contentsOf: parentHistory) == parentBytes && signature(parentHistory) == parentSignature && parentPublications == 0, "symlink-direct-parent-refused-for-cache-hit-and-real-commit")
            withExtendedLifetime(parentObserver) {}
            withExtendedLifetime((videoObserver, aiObserver, idObserver)) {}
        } else if mode == "dedup-restart" {
            let ledger = RecentTaskLedger(path: work.appendingPathComponent("history.json"), defaults: isolatedDefaults)
            check(ledger.videoTasks.isEmpty && ledger.aiTasks.isEmpty && ledger.persistenceIssue == nil, "clear-remains-empty-in-fresh-process")
        } else { throw CheckFailure(message: "unknown QA mode") }
        let report: [String: Any] = ["scope": "Silent production ledger and actual filesystem only; no AVPlayer, application, speech, GUI or media jobs", "mode": mode, "checks": rows, "result": rows.allSatisfy { $0["result"] as? String == "PASS" } ? "PASS" : "FAIL"]
        FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]))
        FileHandle.standardOutput.write(Data("\n".utf8))
    }
}
