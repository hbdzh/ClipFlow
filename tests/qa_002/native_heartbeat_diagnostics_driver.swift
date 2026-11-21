import AppKit
import Darwin
import Foundation

private enum DriverFailure: Error { case expected }

@main struct HeartbeatDiagnosticsReview {
    @MainActor static func main() async throws {
        let directory = FileManager.default.temporaryDirectory.resolvingSymlinksInPath()
        let sentinel = directory.appendingPathComponent(NativeHeartbeat.sentinelName)
        guard NSApp == nil, !FileManager.default.fileExists(atPath: sentinel.path) else {
            throw NSError(domain: "QA requires no NSApplication and an isolated temporary directory", code: 1)
        }
        var checks: [[String: Any]] = []
        func check(_ value: Bool, _ name: String) {
            checks.append(["name": name, "result": value ? "PASS" : "FAIL"])
        }
        let heartbeat = NativeHeartbeat.shared
        let returned = heartbeat.measure(.historyPersist) { 42 }
        check(returned == 42, "disabled-measure-preserves-return-value")
        do { try heartbeat.measure(.aiBookmark) { throw DriverFailure.expected } }
        catch DriverFailure.expected { check(true, "disabled-measure-preserves-thrown-error") }
        check(try FileManager.default.contentsOfDirectory(atPath: directory.path).isEmpty, "disabled-measure-does-not-create-files")
        let fd = Darwin.open(sentinel.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, mode_t(0o600))
        guard fd >= 0 else { throw NSError(domain: "sentinel create", code: Int(errno)) }
        let token = Data(NativeHeartbeat.sentinelToken.utf8)
        let count = token.withUnsafeBytes { Darwin.write(fd, $0.baseAddress, $0.count) }
        Darwin.close(fd)
        guard count == token.count else { throw NSError(domain: "sentinel write", code: 1) }
        defer { try? FileManager.default.removeItem(at: sentinel) }
        heartbeat.startIfEnabled(activity: { .media })
        try await Task.sleep(for: .milliseconds(2_300))
        for stage: NativeHeartbeat.Stage in [.historyPersist, .historyEncode, .historyMetadata, .historyAtomicWrite, .destinationBookmark, .aiBookmark, .filePanelPreparation, .filePanelPresentation] {
            let value = heartbeat.measure(stage) { Thread.sleep(forTimeInterval: 0.001); return 17 }
            check(value == 17, "enabled-measure-preserves-\(stage.rawValue)-return")
        }
        do { try heartbeat.measure(.aiBookmark) { throw DriverFailure.expected } }
        catch DriverFailure.expected { check(true, "enabled-measure-preserves-thrown-error") }
        for index in 0..<5 {
            // Make the longest interval last so its span is intentionally in
            // the bounded recent-tick history rather than depending on jitter.
            heartbeat.measure(.destinationBookmark) { Thread.sleep(forTimeInterval: index == 4 ? 0.65 : 0.27) }
            try await Task.sleep(for: .milliseconds(150))
        }
        let stopBegin = ContinuousClock.now
        await heartbeat.stop()
        check(stopBegin.duration(to: .now) < .milliseconds(150), "stop-does-not-wait-for-diagnostic-IO")
        try await Task.sleep(for: .milliseconds(250))
        let files = try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
            .filter { $0.lastPathComponent.hasPrefix("qa002-heartbeat-") && $0.pathExtension == "jsonl" }
        guard files.count == 1 else { throw NSError(domain: "one output expected", code: files.count) }
        let raw = try String(contentsOf: files[0], encoding: .utf8)
        let lines = raw.split(separator: "\n")
        let records = try lines.map { try JSONSerialization.jsonObject(with: Data($0.utf8)) as! [String: Any] }
        guard let last = records.last else { throw NSError(domain: "no diagnostic output", code: 1) }
        check(last["kind"] as? String == "stopped", "observed-best-effort-final-record")
        check(last["period_ms"] as? Int == 100 && last["warmup_ms"] as? Int == 2_000, "original-period-warmup-unchanged")
        check((last["measured_max_ms"] as? Double ?? 0) > 500 && (last["active_max_ms"] as? Double ?? 0) > 500,
              "650ms-block-still-fails-original-global-and-active-thresholds")
        let ticks = last["slow_ticks"] as? [[String: Any]] ?? []
        check(ticks.count == 4, "recent-slow-ticks-bounded-to-four")
        check(ticks.allSatisfy { tick in
            guard let begin = tick["begin_ms"] as? Double, let end = tick["end_ms"] as? Double,
                  let delta = tick["delta_ms"] as? Double else { return false }
            return begin < end && delta > 250 && abs((end - begin) - delta) < 0.01
                && tick["previous_activity"] as? String == "media" && tick["current_activity"] as? String == "media"
                && tick["previous_window"] as? String == "none" && tick["current_window"] as? String == "none"
        }, "slow-tick-monotonic-times-and-endpoint-labels")
        let stages = last["stage_timings"] as? [String: [String: Any]] ?? [:]
        check(stages.count == 8, "fixed-eight-stage-schema-populated")
        check((stages["ai_bookmark"]?["count"] as? Int ?? 0) == 2, "throwing-synchronous-operation-also-recorded")
        let bookmark = stages["destination_bookmark"] ?? [:]
        check((bookmark["count"] as? Int ?? 0) == 6 && (bookmark["maximum_ms"] as? Double ?? 0) >= 640,
              "real-synchronous-stage-duration-and-count-recorded")
        let stageBegin = bookmark["begin_ms"] as? Double ?? 0, stageEnd = bookmark["end_ms"] as? Double ?? 0
        check(ticks.contains { ($0["begin_ms"] as? Double ?? .infinity) <= stageBegin && ($0["end_ms"] as? Double ?? 0) >= stageEnd },
              "actual-max-stage-overlaps-an-observed-slow-tick")
        let windows = last["window_interval_maxima"] as? [String: [String: Any]] ?? [:]
        check((windows["none"]?["maximum_ms"] as? Double ?? 0) == last["measured_max_ms"] as? Double,
              "window-interval-max-does-not-replace-global-max")
        check(lines.allSatisfy { $0.utf8.count <= 4_096 }, "all-encoded-records-fit-4096-byte-budget")
        check(!raw.contains(directory.path) && !raw.contains("/Users/") && !raw.contains("/Volumes/"), "no-paths-or-media-identifiers-in-diagnostics")
        check(NSApp == nil, "no-NSApplication-created-and-no-GUI-or-audio-used")
        let passed = checks.allSatisfy { $0["result"] as? String == "PASS" }
        let report: [String: Any] = ["result": passed ? "PASS" : "FAIL", "scope": "Silent native observer/file controls; not product GUI performance acceptance",
                                   "checks": checks, "records": records, "maximum_record_bytes": lines.map { $0.utf8.count }.max() ?? 0]
        FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]))
        FileHandle.standardOutput.write(Data("\n".utf8))
        exit(passed ? 0 : 1)
    }
}
