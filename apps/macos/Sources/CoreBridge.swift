import Foundation

struct CoreVersion: Codable, Sendable {
    let schemaVersion: Int
    let coreVersion: String
    let abiVersion: Int
    let stage: String
    let mockOnly: Bool
}
struct CoreFeature: Codable, Sendable { let available: Bool; let reason: String? }
struct MediaBackend: Codable, Sendable { let name: String; let version: String }
struct CoreCapabilities: Codable, Sendable {
    let schemaVersion: Int
    let coreVersion: String
    let platform: String
    let mockOnly: Bool
    let features: [String: CoreFeature]
    let mediaBackend: MediaBackend
}
struct JobFailure: Codable, Sendable { let code: String; let message: String }
struct JobResult: Codable, Sendable { let mockOnly: Bool; let fileCreated: Bool }
struct JobSnapshot: Codable, Sendable {
    let schemaVersion: Int
    let jobId: String
    let operation: String
    let state: String
    let mockOnly: Bool
    let error: JobFailure?
    let result: JobResult?
}
struct CoreConnection: Sendable { let version: CoreVersion; let capabilities: CoreCapabilities }
enum MockOutcome: String, Sendable { case succeed, fail }
struct BridgeError: Error, LocalizedError, Sendable {
    let code: Int32
    let operation: String
    var errorDescription: String? {
        if code == -2 { return "Core 会话已关闭，请重新连接。" }
        return "Core 调用未完成（\(operation)，状态 \(code)）。"
    }
}
struct BridgeDiagnostics: Codable, Sendable {
    var outputCalls = 0
    var bufferReleaseCalls = 0
    var allCallsOffMainThread = true
    var errorOutputsWereEmpty = true
}

// This owner is private and never leaves CoreBridge. It is deliberately not
// Sendable. All pointer reads/writes and destruction are actor-confined. Its
// deinit is only a final synchronous safeguard for this bounded mock Core.
private final class EngineOwner {
    private var handle: OpaquePointer?
    private var knownJobs: Set<String> = []
    private(set) var diagnostics = BridgeDiagnostics()
    private let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()

    init() throws {
        let config = Array(#"{"schema_version":0,"max_jobs":64}"#.utf8)
        var created: OpaquePointer?
        let status = config.withUnsafeBufferPointer { bytes in
            ms_engine_create(bytes.baseAddress, bytes.count, &created)
        }
        guard status == 0, let created else { throw BridgeError(code: status == 0 ? -100 : status, operation: "create") }
        handle = created
        noteThread()
    }

    deinit {
        if handle != nil {
            do { try close() }
            catch { fputs("MAC-001: bounded mock engine cleanup could not be confirmed; handle not reused.\n", stderr) }
        }
    }

    private func noteThread() { diagnostics.allCallsOffMainThread = diagnostics.allCallsOffMainThread && !Thread.isMainThread }
    private func liveHandle() throws -> OpaquePointer {
        guard let handle else { throw BridgeError(code: -2, operation: "closed") }
        return handle
    }
    private func output<T: Decodable>(_ operation: String, call: (OpaquePointer, UnsafeMutablePointer<ms_buffer>) -> Int32) throws -> T {
        let engine = try liveHandle()
        noteThread()
        var buffer = ms_buffer(ptr: nil, len: 0)
        diagnostics.outputCalls += 1
        // This is the only release path, including Core error and decode failure.
        // The exact original ptr/len is returned to its Rust allocator once.
        defer { ms_buffer_free(buffer); diagnostics.bufferReleaseCalls += 1 }
        let status = call(engine, &buffer)
        guard status == 0 else {
            diagnostics.errorOutputsWereEmpty = diagnostics.errorOutputsWereEmpty && buffer.ptr == nil && buffer.len == 0
            throw BridgeError(code: status, operation: operation)
        }
        guard let bytes = buffer.ptr, buffer.len > 0 else { throw BridgeError(code: -100, operation: "empty_output") }
        let copied = Data(bytes: bytes, count: buffer.len)
        return try decoder.decode(T.self, from: copied)
    }
    func version() throws -> CoreVersion { try output("version", call: ms_get_version) }
    func capabilities() throws -> CoreCapabilities { try output("capabilities", call: ms_get_capabilities) }
    func submit(_ data: Data) throws -> JobSnapshot {
        let response: JobSnapshot = try data.withUnsafeBytes { bytes in
            try output("submit") { engine, out in
                ms_submit_job(engine, bytes.bindMemory(to: UInt8.self).baseAddress, bytes.count, out)
            }
        }
        knownJobs.insert(response.jobId)
        return response
    }
    func poll(_ id: String) throws -> JobSnapshot {
        let bytes = Array(id.utf8)
        return try bytes.withUnsafeBufferPointer { bytes in
            try output("snapshot") { engine, out in ms_get_job_snapshot(engine, bytes.baseAddress, bytes.count, out) }
        }
    }
    func cancel(_ id: String) throws {
        let engine = try liveHandle()
        noteThread()
        let bytes = Array(id.utf8)
        let status = bytes.withUnsafeBufferPointer { ms_cancel_job(engine, $0.baseAddress, $0.count) }
        guard status == 0 else { throw BridgeError(code: status, operation: "cancel") }
    }
    func close() throws {
        guard let engine = handle else { return }
        noteThread()
        // All jobs were created through this owner. CORE-001 mock cancellation
        // reaches terminal in two polls; real workers require a different policy.
        var cleanupError: Error?
        for id in knownJobs {
            do {
                try cancel(id)
                var terminal = false
                for _ in 0..<4 {
                    let snapshot = try poll(id)
                    if ["succeeded", "failed", "cancelled"].contains(snapshot.state) { terminal = true; break }
                }
                if !terminal { throw BridgeError(code: -7, operation: "mock_cleanup") }
            } catch { cleanupError = error }
        }
        let status = ms_engine_destroy(engine)
        if status == 0 { handle = nil; knownJobs.removeAll(); return }
        // BUSY does not consume the Rust handle. Keep ownership for retry.
        if let cleanupError { throw cleanupError }
        throw BridgeError(code: status, operation: "destroy")
    }
}

actor CoreBridge {
    private var owner: EngineOwner?
    private var lastDiagnostics = BridgeDiagnostics()
    func connect() throws -> CoreConnection {
        if owner == nil { owner = try EngineOwner() }
        guard let owner else { throw BridgeError(code: -100, operation: "connect") }
        return try CoreConnection(version: owner.version(), capabilities: owner.capabilities())
    }
    func submit(outcome: MockOutcome) throws -> JobSnapshot {
        guard let owner else { throw BridgeError(code: -2, operation: "closed") }
        let request = Data("{\"schema_version\":0,\"operation\":\"mock\",\"outcome\":\"\(outcome.rawValue)\"}".utf8)
        return try owner.submit(request)
    }
    func submitRawForSelfTest(_ data: Data) throws -> JobSnapshot {
        guard let owner else { throw BridgeError(code: -2, operation: "closed") }
        return try owner.submit(data)
    }
    func poll(id: String) throws -> JobSnapshot {
        guard let owner else { throw BridgeError(code: -2, operation: "closed") }
        return try owner.poll(id)
    }
    func cancel(id: String) throws {
        guard let owner else { throw BridgeError(code: -2, operation: "closed") }
        try owner.cancel(id)
    }
    func close() throws {
        guard let owner else { return }
        try owner.close()
        lastDiagnostics = owner.diagnostics
        self.owner = nil
    }
    func diagnostics() -> BridgeDiagnostics { owner?.diagnostics ?? lastDiagnostics }
}
