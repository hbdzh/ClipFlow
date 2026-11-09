import Foundation

struct CoreVersion: Codable, Sendable {
    let schemaVersion: Int
    let coreVersion: String
    let abiVersion: Int
    let stage: String
    let mockOnly: Bool
}
struct CoreFeature: Codable, Sendable { let available: Bool; let reason: String? }
struct MediaBackend: Codable, Sendable {
    let name: String
    let version: String?
    let ffmpegVersion: String?
    let ffprobeVersion: String?
}
struct CoreCapabilities: Codable, Sendable {
    let schemaVersion: Int
    let coreVersion: String
    let platform: String
    let mockOnly: Bool
    let features: [String: CoreFeature]
    let mediaBackend: MediaBackend
}
struct JobFailure: Codable, Sendable { let code: String; let message: String }
struct MediaStream: Codable, Sendable {
    let index: Int
    let codecType: String
    let codecName: String?
    let timeBase: String?
    let width: Int?
    let height: Int?
    let sampleRate: String?
    let channels: Int?
}
struct MediaInfo: Codable, Sendable {
    let container: String
    let videoOriginUs: Int64
    let durationUs: Int64
    let videoFrameCount: Int?
    let trimDisplayMetadataSupported: Bool?
    let streams: [MediaStream]
}
struct MediaRange: Codable, Sendable {
    let startUs: Int64
    let endUs: Int64
}
struct OutputValidation: Codable, Sendable {
    let allStreamsRetained: Bool
    let completeSourceFrameSequence: Bool
    let verifiedVideoFrames: Int
    let compressedPacketPayloadsFromSource: Bool
    let decodeAllStreams: Bool
    let packetDtsMonotonic: Bool
    let allStreamPacketSequencesContiguous: Bool
    let sourceRelativePacketTimestamps: Bool
    let packetTimestampToleranceUs: Int64
    let audioVideoEndDifferenceUs: Int64
    let perceptualAvSyncVerified: Bool
    let sourceIdentityUnchanged: Bool
}
struct JobResult: Codable, Sendable {
    let mockOnly: Bool
    let fileCreated: Bool
    let mediaInfo: MediaInfo?
    let requestedRangeUs: MediaRange?
    let actualRangeUs: MediaRange?
    let actualSourceRangeUs: MediaRange?
    let outputMediaInfo: MediaInfo?
    let validation: OutputValidation?
}
struct BackendConfiguration: Codable, Equatable, Sendable {
    let ffmpeg: String
    let ffprobe: String
    let timeoutMs: UInt64
    let maxOutputBytes: UInt64
    static func hostDefault() -> Self {
        let environment = ProcessInfo.processInfo.environment
        return Self(ffmpeg: environment["MEDIASTUDIO_FFMPEG"] ?? "/opt/homebrew/bin/ffmpeg",
                    ffprobe: environment["MEDIASTUDIO_FFPROBE"] ?? "/opt/homebrew/bin/ffprobe",
                    timeoutMs: UInt64(environment["MEDIASTUDIO_TIMEOUT_MS"] ?? "30000") ?? 0,
                    maxOutputBytes: UInt64(environment["MEDIASTUDIO_MAX_OUTPUT_BYTES"] ?? "8388608") ?? 0)
    }
}
private struct EngineConfiguration: Encodable {
    let schemaVersion = 0
    let maxJobs = 256
    let mediaBackend: BackendConfiguration?
}
private struct MediaLocation: Encodable { let uri: String }
private struct ProbeRequest: Encodable {
    let schemaVersion = 0
    let operation = "probe"
    let source: MediaLocation
}
private struct TrimRequest: Encodable {
    let schemaVersion = 0
    let operation = "trim"
    let source: MediaLocation
    let destination: MediaLocation
    let range: MediaRange
    let strategy = "stream_copy"
    let streamSelection = "all_supported"
    let allowReencodeFallback = false
}
private func requestData(_ value: some Encodable) throws -> Data {
    let encoder = JSONEncoder()
    encoder.keyEncodingStrategy = .convertToSnakeCase
    return try encoder.encode(value)
}
struct JobSnapshot: Codable, Sendable {
    let schemaVersion: Int
    let jobId: String
    let operation: String
    let state: String
    let mockOnly: Bool
    let error: JobFailure?
    let result: JobResult?
}
struct CoreConnection: Codable, Sendable { let version: CoreVersion; let capabilities: CoreCapabilities }
enum MockOutcome: String, Sendable { case succeed, fail }
struct BridgeError: Error, LocalizedError, Sendable {
    let code: Int32
    let operation: String
    var errorDescription: String? {
        if code == -2 { return "Core 会话已关闭，请重新连接。" }
        if code == -9 { return "会话容量已满或输入超限。请重新导入媒体以重建会话，或检查输入。" }
        return "Core 调用未完成（\(operation)，状态 \(code)）。"
    }
}
struct BridgeDiagnostics: Codable, Sendable {
    var outputCalls = 0
    var bufferReleaseCalls = 0
    var allCallsOffMainThread = true
    var errorOutputsWereEmpty = true
    mutating func accumulate(_ other: Self) {
        outputCalls += other.outputCalls; bufferReleaseCalls += other.bufferReleaseCalls
        allCallsOffMainThread = allCallsOffMainThread && other.allCallsOffMainThread
        errorOutputsWereEmpty = errorOutputsWereEmpty && other.errorOutputsWereEmpty
    }
}

// This owner is private and never leaves CoreBridge. It is deliberately not
// Sendable. All pointer reads/writes and destruction are actor-confined. Its
// deinit only reports an unclosed owner; it never performs FFI on an unknown executor.
private final class EngineOwner {
    private var handle: OpaquePointer?
    private var knownJobs: Set<String> = []
    private(set) var diagnostics = BridgeDiagnostics()
    private let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()

    init(backend: BackendConfiguration?) throws {
        let config = Array(try requestData(EngineConfiguration(mediaBackend: backend)))
        var created: OpaquePointer?
        let status = config.withUnsafeBufferPointer { bytes in
            ms_engine_create(bytes.baseAddress, bytes.count, &created)
        }
        guard status == 0, let created else { throw BridgeError(code: status == 0 ? -100 : status, operation: "create") }
        handle = created
        noteThread()
    }

    deinit {
        // The actor explicitly cancels and drains before releasing this owner.
        // Never run FFI cleanup on an unknown deinit executor. Exceptional owners
        // remain live rather than freeing/reusing a possibly active Rust handle.
        if handle != nil {
            fputs("MediaStudio: explicit Core close was not confirmed; live handle retained.\n", stderr)
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
    var jobIDs: [String] { Array(knownJobs).sorted() }
    func destroy() throws {
        guard let engine = handle else { return }
        noteThread()
        let status = ms_engine_destroy(engine)
        if status == 0 { handle = nil; knownJobs.removeAll(); return }
        throw BridgeError(code: status, operation: "destroy")
    }
}

actor CoreBridge {
    private var owner: EngineOwner?
    private var lastDiagnostics = BridgeDiagnostics()
    private var backend: BackendConfiguration?
    private var closing = false
    private var closeSnapshots: [JobSnapshot] = []
    func connect(backend requested: BackendConfiguration? = nil) throws -> CoreConnection {
        guard !closing else { throw BridgeError(code: -7, operation: "closing") }
        if owner == nil { owner = try EngineOwner(backend: requested); backend = requested }
        guard requested == backend else { throw BridgeError(code: -7, operation: "configuration_requires_close") }
        guard let owner else { throw BridgeError(code: -100, operation: "connect") }
        return try CoreConnection(version: owner.version(), capabilities: owner.capabilities())
    }
    func submit(outcome: MockOutcome) throws -> JobSnapshot {
        guard !closing else { throw BridgeError(code: -7, operation: "closing") }
        guard let owner else { throw BridgeError(code: -2, operation: "closed") }
        let request = Data("{\"schema_version\":0,\"operation\":\"mock\",\"outcome\":\"\(outcome.rawValue)\"}".utf8)
        return try owner.submit(request)
    }
    func submitProbe(source: URL) throws -> JobSnapshot {
        try submitRawForSelfTest(requestData(ProbeRequest(source: MediaLocation(uri: source.absoluteString))))
    }
    func submitTrim(source: URL, destination: URL, range: MediaRange) throws -> JobSnapshot {
        try submitRawForSelfTest(requestData(TrimRequest(source: MediaLocation(uri: source.absoluteString),
                                                       destination: MediaLocation(uri: destination.absoluteString), range: range)))
    }
    func submitRawForSelfTest(_ data: Data) throws -> JobSnapshot {
        guard !closing else { throw BridgeError(code: -7, operation: "closing") }
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
    func close() async throws {
        guard !closing else { throw BridgeError(code: -7, operation: "closing") }
        guard let owner else { return }
        closing = true
        defer { closing = false }
        do {
            for id in owner.jobIDs { try owner.cancel(id) }
            let deadline = ContinuousClock.now + .milliseconds(Int64(backend?.timeoutMs ?? 30000) + 5000)
            while true {
                var active = false
                for id in owner.jobIDs {
                    let snapshot = try owner.poll(id)
                    active = active || !["succeeded", "failed", "cancelled"].contains(snapshot.state)
                    if ["succeeded", "failed", "cancelled"].contains(snapshot.state) {
                        closeSnapshots.removeAll { $0.jobId == snapshot.jobId }
                        closeSnapshots.append(snapshot)
                    }
                }
                if !active { break }
                guard ContinuousClock.now < deadline else { throw BridgeError(code: -7, operation: "cleanup_not_confirmed") }
                // Sleep yields the actor; no pointer crosses isolation. New submissions
                // are blocked while closing. Poll/cancel remain serialized on this actor.
                try await Task.sleep(for: .milliseconds(20))
            }
        } catch {
            // A quarantined engine rejects cancel/poll but explicitly permits
            // destroy, whose Rust Drop cancels and joins its worker. A healthy
            // busy engine returns BUSY and remains owned for retry.
            do { try owner.destroy() }
            catch { throw error }
            lastDiagnostics.accumulate(owner.diagnostics)
            self.owner = nil; backend = nil
            return
        }
        try owner.destroy()
        lastDiagnostics.accumulate(owner.diagnostics)
        self.owner = nil
        backend = nil
    }
    func closedJobSnapshots() -> [JobSnapshot] { closeSnapshots }
    func diagnostics() -> BridgeDiagnostics {
        var value = lastDiagnostics
        if let owner { value.accumulate(owner.diagnostics) }
        return value
    }
}
