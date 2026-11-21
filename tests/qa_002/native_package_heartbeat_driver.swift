import Darwin
import Foundation

@MainActor private func blockForNegativeControl() { Thread.sleep(forTimeInterval: 0.65) }

@main struct HeartbeatObservation {
    @MainActor static func main() async throws {
        let directory = FileManager.default.temporaryDirectory.resolvingSymlinksInPath()
        let sentinel = directory.appendingPathComponent(NativeHeartbeat.sentinelName)
        guard !FileManager.default.fileExists(atPath: sentinel.path) else {
            throw NSError(domain: "QA refuses existing sentinel", code: 1)
        }
        var checks: [[String: Any]] = []
        func outputs() throws -> Set<String> {
            Set(try FileManager.default.contentsOfDirectory(atPath: directory.path).filter {
                $0.hasPrefix("qa002-heartbeat-") && $0.hasSuffix(".jsonl")
            })
        }
        let before = try outputs()
        func check(_ value: Bool, _ name: String) throws {
            checks.append(["name": name, "result": value ? "PASS" : "FAIL"])
            guard value else { throw NSError(domain: name, code: 1) }
        }
        func create(_ text: String, mode: mode_t = 0o600) throws {
            let fd = Darwin.open(sentinel.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, mode)
            guard fd >= 0 else { throw NSError(domain: "create QA sentinel", code: Int(errno)) }
            defer { Darwin.close(fd) }
            let bytes = Array(text.utf8)
            let wrote = bytes.withUnsafeBytes { Darwin.write(fd, $0.baseAddress, $0.count) }
            guard wrote == bytes.count else { throw NSError(domain: "write QA sentinel", code: 1) }
        }
        NativeHeartbeat.shared.startIfEnabled()
        try await Task.sleep(for: .milliseconds(200))
        try check(try outputs() == before, "default-disabled-no-log")
        try create("wrong-token\n")
        NativeHeartbeat.shared.startIfEnabled()
        try await Task.sleep(for: .milliseconds(120))
        try check(try outputs() == before, "wrong-token-refused")
        try FileManager.default.removeItem(at: sentinel)
        try create(NativeHeartbeat.sentinelToken, mode: 0o644)
        NativeHeartbeat.shared.startIfEnabled()
        try check(try outputs() == before, "nonprivate-mode-refused")
        try FileManager.default.removeItem(at: sentinel)
        let linkTarget = directory.appendingPathComponent("qa002-heartbeat-negative-" + UUID().uuidString)
        try Data(NativeHeartbeat.sentinelToken.utf8).write(to: linkTarget, options: .withoutOverwriting)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: linkTarget.path)
        defer { try? FileManager.default.removeItem(at: linkTarget) }
        guard symlink(linkTarget.path, sentinel.path) == 0 else { throw NSError(domain: "symlink", code: Int(errno)) }
        NativeHeartbeat.shared.startIfEnabled()
        try check(try outputs() == before, "symlink-refused")
        try FileManager.default.removeItem(at: sentinel)
        guard link(linkTarget.path, sentinel.path) == 0 else { throw NSError(domain: "hardlink", code: Int(errno)) }
        NativeHeartbeat.shared.startIfEnabled()
        try check(try outputs() == before, "hardlink-refused")
        try FileManager.default.removeItem(at: sentinel)
        guard mkfifo(sentinel.path, 0o600) == 0 else { throw NSError(domain: "FIFO", code: Int(errno)) }
        let fifoStart = ContinuousClock.now
        NativeHeartbeat.shared.startIfEnabled()
        try check(fifoStart.duration(to: .now) < .milliseconds(100), "FIFO-refused-without-blocking")
        try FileManager.default.removeItem(at: sentinel)
        try create(NativeHeartbeat.sentinelToken)
        defer { try? FileManager.default.removeItem(at: sentinel) }
        NativeHeartbeat.shared.startIfEnabled(activity: { .media })
        try await Task.sleep(for: .milliseconds(3_300))
        let stopStart = ContinuousClock.now
        await NativeHeartbeat.shared.stop()
        try check(stopStart.duration(to: .now) < .milliseconds(100), "normal-stop-not-delayed-by-writer")
        try await Task.sleep(for: .milliseconds(200))
        let first = try outputs().subtracting(before)
        try check(first.count == 1, "one-private-output-per-run")
        let normalURL = directory.appendingPathComponent(first.first!)
        func records(_ url: URL) throws -> [[String: Any]] {
            try String(contentsOf: url, encoding: .utf8).split(separator: "\n").map {
                try JSONSerialization.jsonObject(with: Data($0.utf8)) as! [String: Any]
            }
        }
        let normal = try records(normalURL), n = normal.last!
        try check((n["period_ms"] as? Int) == 100 && (n["warmup_ms"] as? Int) == 2_000, "exact-period-and-declared-warmup")
        try check((n["measured_count"] as? Int ?? 0) >= 10 && (n["active_count"] as? Int ?? 0) >= 10, "active-postwarmup-samples-recorded")
        try check((n["measured_max_ms"] as? Double ?? 999) <= 500, "normal-mainactor-threshold")
        NativeHeartbeat.shared.startIfEnabled(activity: { .ai })
        try await Task.sleep(for: .milliseconds(2_300))
        blockForNegativeControl()
        try await Task.sleep(for: .milliseconds(200))
        await NativeHeartbeat.shared.stop()
        try await Task.sleep(for: .milliseconds(200))
        let all = try outputs().subtracting(before)
        try check(all.count == 2, "restart-produces-separate-session")
        let negativeName = all.subtracting(first).first!
        let negativeURL = directory.appendingPathComponent(negativeName)
        let negative = try records(negativeURL), b = negative.last!
        try check((b["measured_max_ms"] as? Double ?? 0) > 500 && (b["active_max_ms"] as? Double ?? 0) > 500,
                  "650ms-real-mainactor-block-detected")
        for url in [normalURL, negativeURL] {
            let raw = try String(contentsOf: url, encoding: .utf8)
            let attrs = try FileManager.default.attributesOfItem(atPath: url.path)
            try check((attrs[.posixPermissions] as? NSNumber)?.intValue == 0o600, "private-log-permissions")
            try check(!raw.contains(directory.path) && !raw.contains("/Users/") && !raw.contains("/Volumes/"), "no-PII-or-paths-in-records")
        }
        let report: [String: Any] = ["result": "PASS", "scope": "Independent observer controls; not GUI responsiveness acceptance",
                                    "checks": checks, "normal": normal, "negative": negative,
                                    "normal_file": normalURL.path, "negative_file": negativeURL.path]
        let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
        FileHandle.standardOutput.write(data); FileHandle.standardOutput.write(Data("\n".utf8))
    }
}
