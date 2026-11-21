import AppKit
import AVFoundation
import Foundation

@main struct AudioPreviewLifecycleObservation {
    @MainActor static func main() async {
        _ = NSApplication.shared
        var checks: [[String: Any]] = [], passed = false
        var model: WorkspaceModel?, faultRoot: URL?
        func check(_ ok: Bool, _ name: String, _ detail: [String: Any] = [:]) throws {
            checks.append(["name": name, "result": ok ? "PASS" : "FAIL", "detail": detail])
            if !ok { throw CheckFailure(message: name) }
        }
        do {
            let args = CommandLine.arguments
            let config = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: args[args.firstIndex(of: "--config")! + 1]))) as! [String: Any]
            let work = URL(fileURLWithPath: config["work"] as! String), marker = URL(fileURLWithPath: config["preview_ready_marker"] as! String)
            let paths = (config["proxy_switch_sources"] as! [String]).map { URL(fileURLWithPath: $0) }
            let m = WorkspaceModel(historyPath: work.appendingPathComponent("lifecycle-history.json")); model = m
            await m.importURLs(paths)
            try check(m.assets.count == 3 && m.assets.allSatisfy { $0.info != nil && $0.state == "succeeded" }, "initial-real-audio-video-probes")
            let initialRoot = m.audioPreviewDirectory
            await m.selectAsset(id: m.assets[1].id)
            try check(initialRoot != nil && !FileManager.default.fileExists(atPath: initialRoot!.path) && m.audioPreviewDirectory == nil, "completed-proxy-reclaimed-on-switch")
            m.ffmpegPath = config["preview_gate"] as! String
            for cycle in 0..<3 {
                if FileManager.default.fileExists(atPath: marker.path) { try FileManager.default.removeItem(at: marker) }
                let loading = Task { await m.selectAsset(id: m.assets[0].id) }
                let deadline = ContinuousClock.now + .seconds(15)
                var witness: [String: Any]?, root: URL?
                while ContinuousClock.now < deadline {
                    if let data = try? Data(contentsOf: marker), let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                       let output = value["output"] as? String, let count = (try? FileManager.default.attributesOfItem(atPath: output)[.size]) as? NSNumber,
                       count.intValue > 0, let currentRoot = m.audioPreviewDirectory {
                        witness = value; root = currentRoot; break
                    }
                    try await Task.sleep(for: .milliseconds(10))
                }
                guard let witness, let root, let pid = witness["pid"] as? Int32 else { throw CheckFailure(message: "No real running preview output witness") }
                try check(m.isPreparingAudioPreview && m.audioPreviewAccessRetained && kill(pid, 0) == 0, "real-proxy-process-and-owned-source-before-stop", ["cycle": cycle, "witness": witness, "root": root.path])
                if cycle == 1 { await m.cancel(); await loading.value; await m.selectAsset(id: m.assets[1].id) }
                else { let switching = Task { await m.selectAsset(id: m.assets[1].id) }; await loading.value; await switching.value }
                let current = (m.preview.player.currentItem?.asset as? AVURLAsset)?.url
                try check(m.selectedAssetId == m.assets[1].id && m.sourceURL?.path == m.assets[1].url.path && current?.path == m.assets[1].url.path && m.preview.ready && m.audioPreviewDirectory == nil && m.accessDiagnostics.heldLeases == 3 && !FileManager.default.fileExists(atPath: root.path) && kill(pid, 0) != 0, "running-proxy-cancel-drain-switch-no-stale-player", ["cycle": cycle, "root_removed": true, "pid": pid])
            }
            m.ffmpegPath = config["real_ffmpeg"] as? String ?? "/opt/homebrew/bin/ffmpeg"
            for round in 0..<6 {
                let prior = m.audioPreviewDirectory
                let first = Task { await m.selectAsset(id: m.assets[0].id) }; await Task.yield()
                let second = Task { await m.selectAsset(id: m.assets[1].id) }; await Task.yield()
                let third = Task { await m.selectAsset(id: m.assets[2].id) }
                await first.value; await second.value; await third.value
                let current = (m.preview.player.currentItem?.asset as? AVURLAsset)?.url
                try check(m.selectedAssetId == m.assets[2].id && m.sourceURL?.path == m.assets[2].url.path && m.preview.ready && m.audioPreviewAccessRetained && current?.path == m.audioPreviewDirectory?.appendingPathComponent("preview.wav").path && m.accessDiagnostics.heldLeases == 4 && (prior == nil || !FileManager.default.fileExists(atPath: prior!.path)), "rapid-fallback-video-fallback-generation-isolation", ["round": round])
            }
            if FileManager.default.fileExists(atPath: marker.path) { try FileManager.default.removeItem(at: marker) }
            await m.selectAsset(id: m.assets[2].id)
            guard let root = m.audioPreviewDirectory else { throw CheckFailure(message: "Final fallback did not produce a private proxy: \(m.preview.status) / \(m.issue ?? "")") }
            try check(m.preview.ready && m.sourceURL?.path == m.assets[2].url.path && m.audioPreviewAccessRetained && !m.isPreparingAudioPreview, "fallback-recovers-after-repeated-live-cancellation")
            if config["cleanup_fault"] as? Bool == true {
                faultRoot = root; try FileManager.default.setAttributes([.posixPermissions: 0o500], ofItemAtPath: root.path)
                await m.selectAsset(id: m.assets[1].id)
                try check(m.unconfirmedCleanup && m.audioPreviewDirectory == root && m.audioPreviewAccessRetained && !m.canImport && !m.canExport && !m.canEdit && !m.preview.ready, "cleanup-failure-retains-owner-access-and-quarantines")
                var rejected = false
                do { try await m.shutdown() } catch { rejected = true }
                try check(rejected && !m.closed && m.audioPreviewAccessRetained, "cleanup-unconfirmed-refuses-safe-close")
                // Explicit QA teardown after the observation. This is not a
                // product close-success assertion; the model remains isolated.
                try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: root.path)
                try? await m.shutdown()
                try check(!FileManager.default.fileExists(atPath: root.path), "qa-teardown-confirmed-private-proxy-reclaimed")
                faultRoot = nil
            } else {
                try await m.shutdown()
                try check(m.closed && m.accessDiagnostics.heldLeases == 0 && !FileManager.default.fileExists(atPath: root.path), "normal-shutdown-drains-and-releases-proxy")
            }
            passed = true
        } catch {
            if let root = faultRoot { try? FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: root.path) }
            try? await model?.shutdown()
            checks.append(["name": "unexpected-failure", "result": "FAIL", "error": error.localizedDescription])
        }
        let report: [String: Any] = ["passed": passed, "scope": "Production native owner with real FFmpeg rate-controlled PCM output; not actual GUI or sandbox panel grant evidence", "checks": checks, "summaries": []]
        let data = try! JSONSerialization.data(withJSONObject: report, options: [.sortedKeys])
        FileHandle.standardOutput.write(data); FileHandle.standardOutput.write(Data("\n".utf8)); exit(passed ? 0 : 1)
    }
}
