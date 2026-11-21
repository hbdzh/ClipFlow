import CryptoKit
import Foundation

@main struct ComponentStoreProbe {
    @MainActor static func main() async throws {
        let args = CommandLine.arguments
        guard args.count == 3 else { throw AIComponentStoreError.invalidSelection("QA mode/work required") }
        let mode = args[1], work = URL(fileURLWithPath: args[2], isDirectory: true), fm = FileManager.default
        let root = work.appendingPathComponent("cache", isDirectory: true)
        func url(_ name: String) -> URL { work.appendingPathComponent(name) }
        func mkdir(_ path: URL) throws { try fm.createDirectory(at: path, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700]) }
        func write(_ data: Data, _ path: URL) throws { try data.write(to: path, options: .atomic); try fm.setAttributes([.posixPermissions: 0o600], ofItemAtPath: path.path) }
        func hash(_ path: URL) throws -> String { SHA256.hash(data: try Data(contentsOf: path)).map { String(format: "%02x", $0) }.joined() }
        func reference(_ name: String, large: Bool = false) throws {
            let dir = url(name); try mkdir(dir.appendingPathComponent("graphs", isDirectory: true))
            for (index, file) in AIComponentStore.graphNames.enumerated() { try write(Data(repeating: UInt8(index + (large ? 33 : 1)), count: large ? 262144 : 4096), dir.appendingPathComponent("graphs/" + file)) }
            try write(Data(repeating: large ? 91 : 43, count: large ? 8_388_608 : 131_072), dir.appendingPathComponent("model.onnx"))
        }
        func store(_ ref: String = "reference") -> AIComponentStore { AIComponentStore(root: root, referenceGraphs: url(ref + "/graphs"), referenceModel: url(ref + "/model.onnx")) }
        func sourceHashes(_ name: String) throws -> [String] { try (AIComponentStore.graphNames.map { name + "/graphs/" + $0 } + [name + "/model.onnx"]).map { try hash(url($0)) } }
        var rows: [[String: Any]] = []
        func check(_ ok: Bool, _ name: String) { rows.append(["name": name, "result": ok ? "PASS" : "FAIL", "detail": [:]]) }
        @MainActor func rejected(_ action: () async throws -> Void) async -> Bool { do { try await action(); return false } catch { return true } }
        let selectionFile = root.appendingPathComponent("selection.json")
        if mode == "seed" {
            try mkdir(work); try reference("reference"); try reference("external")
            // Untrusted extras are deliberately never copied or executed.
            try write(Data("not executable QA text".utf8), url("external/graphs/worker"))
            let before = try sourceHashes("external"), cache = store()
            check(try await cache.load() == nil, "missing-root-is-unconfigured")
            let result = try await cache.install(graphs: url("external/graphs"), model: url("external/model.onnx"))
            let folder = result.model.deletingLastPathComponent()
            check(try result.modelBytes == 131072 && (try hash(result.model)) == hash(url("external/model.onnx")), "data-installed-with-identical-model-bytes")
            check(Set(try fm.contentsOfDirectory(atPath: result.graphs.path)) == Set(AIComponentStore.graphNames), "only-eight-data-graphs-copied-no-worker-or-runtime")
            let dirs = [root, folder, result.graphs]
            let files = [selectionFile, folder.appendingPathComponent("owner.json"), result.model] + AIComponentStore.graphNames.map { result.graphs.appendingPathComponent($0) }
            check(try dirs.allSatisfy { (try fm.attributesOfItem(atPath: $0.path)[.posixPermissions] as? NSNumber)?.intValue == 0o700 } && files.allSatisfy { (try fm.attributesOfItem(atPath: $0.path)[.posixPermissions] as? NSNumber)?.intValue == 0o600 }, "directories0700-and-all-data-records0600")
            let signature = try fm.attributesOfItem(atPath: result.model.path)[.systemFileNumber] as? NSNumber
            let repeated = try await cache.install(graphs: url("external/graphs"), model: url("external/model.onnx"))
            check(try repeated.model == result.model && (try fm.attributesOfItem(atPath: result.model.path)[.systemFileNumber] as? NSNumber) == signature, "validated-identical-cache-is-reused-without-recopy")
            check(try before == sourceHashes("external"), "external-source-data-unchanged")
        } else if mode == "restart" || mode == "final-restart" {
            let cache = store(mode == "restart" ? "reference" : "reference-B")
            let value = try await cache.load()
            check(value != nil && value?.modelBytes == (mode == "restart" ? 131072 : 8388608), "fresh-process-validates-and-restores-managed-data")
            let emptyRoot = url("empty-cache"); try mkdir(emptyRoot)
            check(try await AIComponentStore(root: emptyRoot, referenceGraphs: url("reference/graphs"), referenceModel: url("reference/model.onnx")).load() == nil, "existing-root-without-record-is-unconfigured")
        } else if mode == "edges" {
            let cache = store(), before = try sourceHashes("external"), record = try Data(contentsOf: selectionFile)
            let original = try await cache.load()!
            let bad = url("bad-model.onnx"); var wrong = try Data(contentsOf: url("external/model.onnx")); wrong[0] ^= 1; try write(wrong, bad)
            check(try await rejected { _ = try await cache.install(graphs: url("external/graphs"), model: bad) } && (try Data(contentsOf: selectionFile)) == record, "same-size-hash-mismatch-refused-original-selection-unchanged")
            let graph = url("external/graphs/pre.onnx"), bytes = try Data(contentsOf: graph)
            try fm.removeItem(at: graph)
            check(await rejected { _ = try await cache.install(graphs: url("external/graphs"), model: url("external/model.onnx")) }, "missing-input-graph-refused")
            try fm.createSymbolicLink(at: graph, withDestinationURL: url("reference/graphs/pre.onnx"))
            check(await rejected { _ = try await cache.install(graphs: url("external/graphs"), model: url("external/model.onnx")) }, "input-file-symlink-refused")
            try fm.removeItem(at: graph); try write(bytes, graph)
            let link = url("source-parent-link"); try fm.createSymbolicLink(at: link, withDestinationURL: url("external"))
            check(await rejected { _ = try await cache.install(graphs: url("external/graphs"), model: link.appendingPathComponent("model.onnx")) }, "input-direct-parent-symlink-refused")
            try fm.setAttributes([.posixPermissions: 0], ofItemAtPath: url("external/model.onnx").path)
            check(await rejected { _ = try await cache.install(graphs: url("external/graphs"), model: url("external/model.onnx")) }, "actual-input-read-permission-denial-refused")
            try fm.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url("external/model.onnx").path)
            try fm.setAttributes([.posixPermissions: 0o500], ofItemAtPath: root.path)
            check(try await rejected { _ = try await cache.install(graphs: url("external/graphs"), model: url("external/model.onnx")) } && (try Data(contentsOf: selectionFile)) == record, "actual-root-write-permission-denial-preserves-record")
            try fm.setAttributes([.posixPermissions: 0o700], ofItemAtPath: root.path)
            try write(Data(repeating: 7, count: 131072), original.model)
            check(try await rejected { _ = try await cache.load() } && (try Data(contentsOf: selectionFile)) == record, "corrupt-cache-load-throws-without-dropping-record")
            let repaired = try await cache.install(graphs: url("external/graphs"), model: url("external/model.onnx"))
            check(repaired.model != original.model && !fm.fileExists(atPath: original.model.deletingLastPathComponent().path), "explicit-reimport-repairs-corrupt-owned-cache-and-retires-it")
            try fm.removeItem(at: repaired.graphs.appendingPathComponent("post.onnx"))
            check(await rejected { _ = try await cache.load() }, "missing-cached-graph-is-an-explicit-load-error")
            let repairedAgain = try await cache.install(graphs: url("external/graphs"), model: url("external/model.onnx"))
            check(!fm.fileExists(atPath: repaired.model.deletingLastPathComponent().path) && fm.fileExists(atPath: repairedAgain.model.path), "explicit-reimport-repairs-missing-owned-data-without-cleanup-deadlock")
            let invalid = Data("broken JSON QA record".utf8); try write(invalid, selectionFile)
            check(try await rejected { _ = try await cache.load() } && (try Data(contentsOf: selectionFile)) == invalid, "bad-record-load-throws-and-preserves-exact-bytes")
            _ = try await cache.install(graphs: url("external/graphs"), model: url("external/model.onnx"))
            let backups = try fm.contentsOfDirectory(atPath: root.path).filter { $0.hasPrefix(".invalid-") }
            let restored = try await cache.load()
            check(try backups.count == 1 && (try Data(contentsOf: root.appendingPathComponent(backups[0] + "/record.json"))) == invalid && restored != nil, "explicit-reimport-isolates-bad-record-and-restores-configuration")
            _ = try await cache.install(graphs: url("external/graphs"), model: url("external/model.onnx"))
            check(try fm.contentsOfDirectory(atPath: root.path).allSatisfy { !$0.hasPrefix(".invalid-") }, "verified-old-invalid-record-backup-does-not-accumulate")
            let unknown = root.appendingPathComponent(UUID().uuidString); try mkdir(unknown); try write(Data("KEEP QA unknown".utf8), unknown.appendingPathComponent("unknown.txt"))
            check(await rejected { _ = try await cache.install(graphs: url("external/graphs"), model: url("external/model.onnx")) } && fm.fileExists(atPath: unknown.appendingPathComponent("unknown.txt").path), "unowned-cache-directory-is-preserved-and-new-stage-refused")
            try fm.removeItem(at: unknown)
            check(try before == sourceHashes("external"), "all-input-content-unchanged-after-failures-and-recovery")
        } else if mode == "atomic" {
            try reference("reference-B", large: true); try reference("external-B", large: true)
            let cache = store("reference-B"), sourceModel = url("external-B/model.onnx"), sourceBefore = try sourceHashes("external-B")
            for denyCleanup in [false, true] {
                let before = try Data(contentsOf: selectionFile)
                let observer = Task.detached { () -> Bool in
                    let deadline = Date().addingTimeInterval(15)
                    while Date() < deadline {
                        if let names = try? FileManager.default.contentsOfDirectory(atPath: root.path), names.contains(where: { $0.hasPrefix(".stage-") }) {
                            try FileManager.default.setAttributes([.posixPermissions: 0], ofItemAtPath: sourceModel.path)
                            if denyCleanup { try FileManager.default.setAttributes([.posixPermissions: 0o500], ofItemAtPath: root.path) }
                            return true
                        }; try await Task.sleep(for: .microseconds(200))
                    }; return false
                }
                var failure: Error?
                do { _ = try await cache.install(graphs: url("external-B/graphs"), model: sourceModel) } catch { failure = error }
                let observed = try await observer.value
                try fm.setAttributes([.posixPermissions: 0o600], ofItemAtPath: sourceModel.path); try fm.setAttributes([.posixPermissions: 0o700], ofItemAtPath: root.path)
                check(try observed && failure != nil && (try Data(contentsOf: selectionFile)) == before, denyCleanup ? "cleanup-denial-retains-original-selection" : "real-copy-phase-read-denial-preserves-selection-atomically")
                if denyCleanup {
                    if let typed = failure as? AIComponentStoreError, case .cleanupFailed(let stage, _) = typed {
                        let names = try fm.contentsOfDirectory(atPath: root.path)
                        check(fm.fileExists(atPath: stage.path), "cleanup-failure-has-specific-error-and-retained-stage")
                        check(try await rejected { _ = try await cache.install(graphs: url("external-B/graphs"), model: sourceModel) } && (try fm.contentsOfDirectory(atPath: root.path)) == names, "unconfirmed-stage-prevents-new-stage-accumulation")
                        guard stage.path.hasPrefix(root.path + "/") else { throw AIComponentStoreError.invalidSelection("QA cleanup outside work") }; try fm.removeItem(at: stage)
                    } else { check(false, "cleanup-failure-has-specific-error-and-retained-stage") }
                } else { check(try fm.contentsOfDirectory(atPath: root.path).allSatisfy { !$0.hasPrefix(".stage-") }, "failed-copy-cleans-owned-stage") }
            }
            let upgraded = try await cache.install(graphs: url("external-B/graphs"), model: sourceModel)
            check(try upgraded.modelBytes == 8388608 && (try fm.contentsOfDirectory(atPath: root.path)).count == 2, "successful-reference-upgrade-retires-only-owned-old-cache")
            check(try sourceBefore == sourceHashes("external-B"), "copy-and-cleanup-failures-never-modify-input-data")
        } else { throw AIComponentStoreError.invalidSelection("unknown QA mode") }
        let report: [String: Any] = ["scope": "Silent actor/file-only synthetic model+graph bytes; no App/GUI/AVPlayer/VO/worker/inference", "mode": mode, "checks": rows, "result": rows.allSatisfy { $0["result"] as? String == "PASS" } ? "PASS" : "FAIL"]
        FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])); FileHandle.standardOutput.write(Data("\n".utf8))
    }
}
