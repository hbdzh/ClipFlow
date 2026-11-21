import Foundation

struct CoreVersion: Codable, Sendable {
    let schemaVersion: Int
    let coreVersion: String
    let abiVersion: Int
    let stage: String
    let mockOnly: Bool
}
struct CoreFeature: Codable, Sendable { let available: Bool; let reason: String?; let profile: String?; let profiles: [String]? }
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
    let language: String?
    let profile: String?
    let rFrameRate: String?
    let avgFrameRate: String?
    let sampleAspectRatio: String?
}
struct MediaInfo: Codable, Sendable {
    let container: String
    let videoOriginUs: Int64?
    let mediaKind: String?
    let audioOriginUs: Int64?
    let videoCfrRate: String?
    let durationUs: Int64
    let videoFrameCount: Int?
    let trimDisplayMetadataSupported: Bool?
    let core003DisplayMetadataSupported: Bool?
    let streams: [MediaStream]
    var isAudioOnly: Bool { mediaKind == "audio" && !streams.contains(where: { $0.codecType == "video" }) }
}
struct MediaRange: Codable, Sendable {
    let startUs: Int64
    let endUs: Int64
}
// A presentation selection only. Core owns every codec/range/FFmpeg decision.
enum ExportStrategy: String, Codable, CaseIterable, Sendable {
    case legacyStreamCopy
    case streamCopyV1
    case accurateReencode
    case transcodeStandard
    case transcode720p
    var title: String {
        switch self {
        case .legacyStreamCopy: return "快速无损 · 兼容流复制"
        case .streamCopyV1: return "快速无损 · 流复制"
        case .accurateReencode: return "精确裁剪 · 重编码"
        case .transcodeStandard: return "转码 · 标准质量"
        case .transcode720p: return "转码 · 720p 小体积"
        }
    }
    var capabilityKey: String {
        switch self {
        case .legacyStreamCopy: return "trim_stream_copy"
        case .streamCopyV1: return "trim_stream_copy_v1"
        case .accurateReencode: return "accurate_reencode"
        case .transcodeStandard: return "transcode"
        case .transcode720p: return "transcode"
        }
    }
    var profile: String? {
        switch self {
        case .legacyStreamCopy: return nil
        case .streamCopyV1: return "mp4_h264_copy_v1"
        case .accurateReencode, .transcodeStandard: return "mp4_h264_standard_v1"
        case .transcode720p: return "mp4_h264_720p_v1"
        }
    }
    var wireStrategy: String { self == .accurateReencode ? "accurate_reencode" : "stream_copy" }
    var transcodePreset: TranscodePreset? {
        switch self {
        case .transcodeStandard: return .standard
        case .transcode720p: return .compact720p
        default: return nil
        }
    }
    var isReencoded: Bool { self == .accurateReencode || transcodePreset != nil }
}

struct AccuratePlan: Codable, Sendable {
    let sourceTimeBase: String
    let sourceFirstFrameIndex: Int
    let sourceEndFrameIndexExclusive: Int
    let actualStartPts: Int64
    let actualEndExclusivePts: Int64
    let commonOutputOriginSourcePts: Int64
    let videoEncoder: String
    let qualityCrf: Int
    let preset: String
    let lossless: Bool
}
struct VideoQualityValidation: Codable, Sendable {
    let comparedFrames: Int
    let reference: String
    let minimumPlanePsnrDb: Int
}
struct AudioContentValidation: Codable, Sendable {
    let method: String
    let comparisonSampleRate: Int
    let allTracksCompared: Int
    let fullFrequencyOrPerceptualFidelityVerified: Bool
}
struct OutputValidation: Codable, Sendable {
    // These are present in both accepted output contracts and stay required.
    let allStreamsRetained: Bool
    let allSelectedStreamsRetained: Bool?
    let verifiedVideoFrames: Int
    let decodeAllStreams: Bool
    let packetDtsMonotonic: Bool
    let perceptualAvSyncVerified: Bool
    let sourceIdentityUnchanged: Bool
    // Source-payload equality applies only to stream copy, never lossy output.
    let completeSourceFrameSequence: Bool?
    let compressedPacketPayloadsFromSource: Bool?
    let allStreamPacketSequencesContiguous: Bool?
    let sourceRelativePacketTimestamps: Bool?
    let packetTimestampToleranceUs: Int64?
    let audioVideoEndDifferenceUs: Int64?
    let core003ScopeVerified: Bool?
    // Accurate output supplies a different set of content/time evidence.
    let decodedPresentationPtsMonotonic: Bool?
    let verifiedOutputPacketCount: Int?
    let videoTimestampToleranceUs: Int64?
    let audioMappingMaximumErrorUs: Int64?
    let fullColourFidelityVerified: Bool?
    let videoQuality: VideoQualityValidation?
    let audioBandlimitedContent: AudioContentValidation?

    func hasRequiredEvidence(for strategy: ExportStrategy) -> Bool {
        guard (strategy.transcodePreset != nil ? allSelectedStreamsRetained == true : allStreamsRetained),
              verifiedVideoFrames > 0, decodeAllStreams,
              packetDtsMonotonic, sourceIdentityUnchanged else { return false }
        if strategy.isReencoded {
            // Presence is mandatory; numeric media acceptance remains in Core.
            return decodedPresentationPtsMonotonic == true
                && (verifiedOutputPacketCount ?? 0) > 0
                && videoTimestampToleranceUs != nil && audioMappingMaximumErrorUs != nil
                && videoQuality?.comparedFrames == verifiedVideoFrames
                && audioBandlimitedContent != nil && fullColourFidelityVerified != nil
        }
        return completeSourceFrameSequence == true && compressedPacketPayloadsFromSource == true
            && allStreamPacketSequencesContiguous == true && sourceRelativePacketTimestamps == true
            && packetTimestampToleranceUs != nil && audioVideoEndDifferenceUs != nil
            && (strategy != .streamCopyV1 || core003ScopeVerified == true)
    }
}
struct AudioPreviewValidation: Codable, Sendable {
    let sourceIdentityUnchanged: Bool
    let fullDecode: Bool
    let finiteSamples: Bool
    let contentMappingVerified: Bool
    var verified: Bool { sourceIdentityUnchanged && fullDecode && finiteSamples && contentMappingVerified }
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
    let strategy: String?
    let profile: String?
    let reencoded: Bool?
    let lossless: Bool?
    let accuratePlan: AccuratePlan?
    let transcodePlan: TranscodePlan?
    let previewOnly: Bool?
    let sampleRate: Int?
    let channels: Int?
    let sampleCount: Int64?
    let sourceAudioOriginUs: Int64?
    let sourceContentDurationUs: Int64?
    let previewValidation: AudioPreviewValidation?
    var exportStrategy: ExportStrategy? {
        guard fileCreated, previewOnly != true else { return nil }
        if strategy == nil && profile == nil { return .legacyStreamCopy }
        if strategy == "stream_copy", profile == "mp4_h264_copy_v1" { return .streamCopyV1 }
        if strategy == "accurate_reencode", profile == "mp4_h264_standard_v1" { return .accurateReencode }
        if strategy == "transcode", profile == "mp4_h264_standard_v1" { return .transcodeStandard }
        if strategy == "transcode", profile == "mp4_h264_720p_v1" { return .transcode720p }
        return nil
    }
    enum CodingKeys: String, CodingKey {
        case mockOnly, fileCreated, mediaInfo, requestedRangeUs, actualRangeUs, actualSourceRangeUs
        case outputMediaInfo, validation, strategy, profile, reencoded, lossless, accuratePlan, transcodePlan
        case previewOnly, sampleRate, channels, sampleCount, sourceAudioOriginUs, sourceContentDurationUs, previewValidation
    }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        mockOnly = try c.decode(Bool.self, forKey: .mockOnly)
        fileCreated = try c.decode(Bool.self, forKey: .fileCreated)
        mediaInfo = try c.decodeIfPresent(MediaInfo.self, forKey: .mediaInfo)
        requestedRangeUs = try c.decodeIfPresent(MediaRange.self, forKey: .requestedRangeUs)
        actualRangeUs = try c.decodeIfPresent(MediaRange.self, forKey: .actualRangeUs)
        actualSourceRangeUs = try c.decodeIfPresent(MediaRange.self, forKey: .actualSourceRangeUs)
        outputMediaInfo = try c.decodeIfPresent(MediaInfo.self, forKey: .outputMediaInfo)
        validation = try c.decodeIfPresent(OutputValidation.self, forKey: .validation)
        strategy = try c.decodeIfPresent(String.self, forKey: .strategy)
        profile = try c.decodeIfPresent(String.self, forKey: .profile)
        reencoded = try c.decodeIfPresent(Bool.self, forKey: .reencoded)
        lossless = try c.decodeIfPresent(Bool.self, forKey: .lossless)
        accuratePlan = try c.decodeIfPresent(AccuratePlan.self, forKey: .accuratePlan)
        transcodePlan = try c.decodeIfPresent(TranscodePlan.self, forKey: .transcodePlan)
        previewOnly = try c.decodeIfPresent(Bool.self, forKey: .previewOnly)
        sampleRate = try c.decodeIfPresent(Int.self, forKey: .sampleRate)
        channels = try c.decodeIfPresent(Int.self, forKey: .channels)
        sampleCount = try c.decodeIfPresent(Int64.self, forKey: .sampleCount)
        sourceAudioOriginUs = try c.decodeIfPresent(Int64.self, forKey: .sourceAudioOriginUs)
        sourceContentDurationUs = try c.decodeIfPresent(Int64.self, forKey: .sourceContentDurationUs)
        previewValidation = try c.decodeIfPresent(AudioPreviewValidation.self, forKey: .previewValidation)
        if previewOnly == true {
            guard !mockOnly, fileCreated, profile == "wav_pcm_f32_preview_v1", strategy == nil,
                  let sampleRate, [44100, 48000].contains(sampleRate), let channels, [1, 2].contains(channels),
                  let sampleCount, sampleCount > 0, sampleCount <= Int64(sampleRate) * 600,
                  sourceAudioOriginUs != nil, let sourceContentDurationUs, sourceContentDurationUs > 0,
                  sourceContentDurationUs <= 600_000_000,
                  (sampleCount * 1_000_000 / Int64(sampleRate) - sourceContentDurationUs).magnitude <= 1,
                  previewValidation?.verified == true else {
                throw DecodingError.dataCorruptedError(forKey: .previewValidation, in: c, debugDescription: "Unverified private audio preview result.")
            }
            return
        }
        if fileCreated {
            guard !mockOnly, requestedRangeUs != nil, actualRangeUs != nil,
                  actualSourceRangeUs != nil, outputMediaInfo != nil,
                  let mode = exportStrategy, validation?.hasRequiredEvidence(for: mode) == true,
                  (mode == .legacyStreamCopy || (reencoded == mode.isReencoded && lossless == !mode.isReencoded)),
                  (!mode.isReencoded || accuratePlan != nil),
                  (mode.transcodePreset == nil || transcodePlan?.matches(profile: profile, output: outputMediaInfo) == true) else {
                throw DecodingError.dataCorruptedError(forKey: .validation, in: c,
                    debugDescription: "Core output lacks the declared strategy's required validation evidence.")
            }
        }
    }
}
struct AIBackendConfiguration: Codable, Equatable, Sendable {
    let nativeWorker: String
    let nativeGraphs: String
    let nativeRuntime: String
    let model: String
    let timeoutMs: UInt64
    let developmentRoot: String
}
struct BackendConfiguration: Codable, Equatable, Sendable {
    let ffmpeg: String
    let ffprobe: String
    let timeoutMs: UInt64
    let maxOutputBytes: UInt64
    let core003Enabled: Bool
    let accurateReencodeTimeoutMs: UInt64
    let core004Enabled: Bool
    let videoTransformEnabled: Bool?
    let mixStemsEnabled: Bool?
    let transcodeTimeoutMs: UInt64
    let aiBackend: AIBackendConfiguration?
    init(ffmpeg: String, ffprobe: String, timeoutMs: UInt64, maxOutputBytes: UInt64,
         core003Enabled: Bool = false, accurateReencodeTimeoutMs: UInt64 = 600000,
         core004Enabled: Bool = false, videoTransformEnabled: Bool? = nil, mixStemsEnabled: Bool? = nil, transcodeTimeoutMs: UInt64 = 600000,
         aiBackend: AIBackendConfiguration? = nil) {
        self.ffmpeg = ffmpeg; self.ffprobe = ffprobe; self.timeoutMs = timeoutMs
        self.maxOutputBytes = maxOutputBytes; self.core003Enabled = core003Enabled
        self.accurateReencodeTimeoutMs = accurateReencodeTimeoutMs
        self.core004Enabled = core004Enabled; self.videoTransformEnabled = videoTransformEnabled; self.transcodeTimeoutMs = transcodeTimeoutMs
        self.mixStemsEnabled = mixStemsEnabled
        self.aiBackend = aiBackend
    }
    enum CodingKeys: String, CodingKey {
        case ffmpeg, ffprobe, timeoutMs, maxOutputBytes, core003Enabled, accurateReencodeTimeoutMs
        case core004Enabled, videoTransformEnabled, mixStemsEnabled, transcodeTimeoutMs, aiBackend
    }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ffmpeg = try c.decode(String.self, forKey: .ffmpeg); ffprobe = try c.decode(String.self, forKey: .ffprobe)
        timeoutMs = try c.decode(UInt64.self, forKey: .timeoutMs)
        maxOutputBytes = try c.decode(UInt64.self, forKey: .maxOutputBytes)
        core003Enabled = try c.decodeIfPresent(Bool.self, forKey: .core003Enabled) ?? false
        accurateReencodeTimeoutMs = try c.decodeIfPresent(UInt64.self, forKey: .accurateReencodeTimeoutMs) ?? 600000
        core004Enabled = try c.decodeIfPresent(Bool.self, forKey: .core004Enabled) ?? false
        videoTransformEnabled = try c.decodeIfPresent(Bool.self, forKey: .videoTransformEnabled)
        mixStemsEnabled = try c.decodeIfPresent(Bool.self, forKey: .mixStemsEnabled)
        transcodeTimeoutMs = try c.decodeIfPresent(UInt64.self, forKey: .transcodeTimeoutMs) ?? 600000
        aiBackend = try c.decodeIfPresent(AIBackendConfiguration.self, forKey: .aiBackend)
    }
    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(ffmpeg, forKey: .ffmpeg); try c.encode(ffprobe, forKey: .ffprobe)
        try c.encode(timeoutMs, forKey: .timeoutMs); try c.encode(maxOutputBytes, forKey: .maxOutputBytes)
        // Keep legacy configurations byte-shape compatible with older Core.
        if core003Enabled {
            try c.encode(true, forKey: .core003Enabled)
            try c.encode(accurateReencodeTimeoutMs, forKey: .accurateReencodeTimeoutMs)
        }
        // Opt-in remains explicit. Core rejects core004=true/core003=false.
        if core004Enabled {
            try c.encode(true, forKey: .core004Enabled)
            try c.encode(transcodeTimeoutMs, forKey: .transcodeTimeoutMs)
        }
        try c.encodeIfPresent(videoTransformEnabled, forKey: .videoTransformEnabled)
        try c.encodeIfPresent(mixStemsEnabled, forKey: .mixStemsEnabled)
        try c.encodeIfPresent(aiBackend, forKey: .aiBackend)
    }
    static func hostDefault(core003Enabled: Bool = false, core004Enabled: Bool = false) -> Self {
        let environment = ProcessInfo.processInfo.environment
        let bundledFFmpeg = Bundle.main.url(forAuxiliaryExecutable: "ffmpeg")?.path
        let bundledFFprobe = Bundle.main.url(forAuxiliaryExecutable: "ffprobe")?.path
        // A missing bundled helper must remain a missing dependency. Do not
        // silently borrow a developer's globally installed media component.
        let executableDirectory = Bundle.main.executableURL?.deletingLastPathComponent()
        let expectedFFmpeg = executableDirectory?.appendingPathComponent("ffmpeg").path ?? ""
        let expectedFFprobe = executableDirectory?.appendingPathComponent("ffprobe").path ?? ""
        return Self(ffmpeg: environment["MEDIASTUDIO_FFMPEG"] ?? bundledFFmpeg ?? expectedFFmpeg,
                    ffprobe: environment["MEDIASTUDIO_FFPROBE"] ?? bundledFFprobe ?? expectedFFprobe,
                    timeoutMs: UInt64(environment["MEDIASTUDIO_TIMEOUT_MS"] ?? "30000") ?? 0,
                    maxOutputBytes: UInt64(environment["MEDIASTUDIO_MAX_OUTPUT_BYTES"] ?? "8388608") ?? 0,
                    core003Enabled: core003Enabled,
                    accurateReencodeTimeoutMs: core003Enabled
                        ? UInt64(environment["MEDIASTUDIO_ACCURATE_TIMEOUT_MS"] ?? "600000") ?? 0 : 600000,
                    core004Enabled: core004Enabled,
                    videoTransformEnabled: core004Enabled && environment["MEDIASTUDIO_VIDEO_TRANSFORM_DEVELOPMENT"] == "1" ? true : nil,
                    transcodeTimeoutMs: core004Enabled
                        ? UInt64(environment["MEDIASTUDIO_TRANSCODE_TIMEOUT_MS"] ?? "600000") ?? 0 : 600000)
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
private struct AudioPreviewRequest: Encodable {
    let schemaVersion = 0
    let operation = "prepare_audio_preview"
    let source: MediaLocation
    let destination: MediaLocation
    let profile = "wav_pcm_f32_preview_v1"
}
private struct AIRequest: Encodable {
    let schemaVersion = 0
    let operation = "ai_separate"
    let source: MediaLocation
    let destination: MediaLocation
    let modelId = "umx_hq_vocals_onnx_v1"
}
private struct MixStemsRequest: Encodable {
    let schemaVersion = 0
    let operation = "mix_stems"
    let vocals: MediaLocation
    let accompaniment: MediaLocation
    let destination: MediaLocation
    let includeVocals: Bool
    let includeAccompaniment: Bool
}
private struct TrimRequest: Encodable {
    let schemaVersion = 0
    let operation = "trim"
    let source: MediaLocation
    let destination: MediaLocation
    let range: MediaRange
    let strategy: String
    let profile: String?
    let streamSelection = "all_supported"
    let allowReencodeFallback = false
}
private struct BatchRequest: Encodable {
    let schemaVersion = 0
    let operation = "batch_submit"
    let submissionId: String
    let items: [QueueMediaRequest]
}
private struct StoragePreviewRequest: Encodable {
    let schemaVersion = 0
    let operation = "storage_preview"
    let items: [QueueMediaRequest]
}
private struct QueueSnapshotRequest: Encodable {
    let schemaVersion = 0
    let operation = "queue_snapshot"
}
private struct QueueReorderRequest: Encodable {
    let schemaVersion = 0
    let operation = "queue_reorder"
    let jobIds: [String]
}
private struct QueueRetryRequest: Encodable {
    let schemaVersion = 0
    let operation = "job_retry"
    let jobId: String
    let newDestination: MediaLocation
    let submissionId: String
}
private struct QueueClearRequest: Encodable {
    let schemaVersion = 0
    let operation = "history_clear"
}
private func requestData(_ value: some Encodable) throws -> Data {
    let encoder = JSONEncoder()
    encoder.keyEncodingStrategy = .convertToSnakeCase
    return try encoder.encode(value)
}
struct AIJobResult: Codable, Sendable {
    let modelId: String
    let stems: [String]
    let directoryName: String
    let frames: Int64
    let sampleRate: Int
    let channels: Int
    let cleanupUnconfirmed: Bool
}
struct MixStemsResult: Codable, Sendable {
    let fileCreated: Bool
    let profile: String
    let sampleRate: Int
    let channels: Int
    let sampleCount: Int64
    let includeVocals: Bool
    let includeAccompaniment: Bool
}
struct JobSnapshot: Codable, Sendable {
    let schemaVersion: Int
    let jobId: String
    let operation: String
    let state: String
    let mockOnly: Bool
    let error: JobFailure?
    let result: JobResult?
    let aiResult: AIJobResult?
    let mixStemsResult: MixStemsResult?
    enum CodingKeys: String, CodingKey { case schemaVersion, jobId, operation, state, mockOnly, error, result }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try c.decode(Int.self, forKey: .schemaVersion)
        jobId = try c.decode(String.self, forKey: .jobId)
        operation = try c.decode(String.self, forKey: .operation)
        state = try c.decode(String.self, forKey: .state)
        mockOnly = try c.decode(Bool.self, forKey: .mockOnly)
        error = try c.decodeIfPresent(JobFailure.self, forKey: .error)
        if operation == "ai_separate" {
            aiResult = try c.decodeIfPresent(AIJobResult.self, forKey: .result); result = nil; mixStemsResult = nil
        } else if operation == "mix_stems" {
            mixStemsResult = try c.decodeIfPresent(MixStemsResult.self, forKey: .result); result = nil; aiResult = nil
        } else {
            result = try c.decodeIfPresent(JobResult.self, forKey: .result); aiResult = nil; mixStemsResult = nil
        }
    }
    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(schemaVersion, forKey: .schemaVersion); try c.encode(jobId, forKey: .jobId)
        try c.encode(operation, forKey: .operation); try c.encode(state, forKey: .state)
        try c.encode(mockOnly, forKey: .mockOnly); try c.encodeIfPresent(error, forKey: .error)
        if operation == "ai_separate" { try c.encodeIfPresent(aiResult, forKey: .result) }
        else if operation == "mix_stems" { try c.encodeIfPresent(mixStemsResult, forKey: .result) }
        else { try c.encodeIfPresent(result, forKey: .result) }
    }
}
struct AISubmission: Decodable, Sendable {
    let jobId: String?
    let accepted: Bool?
    let errors: [QueueRejection]?
}
struct CoreConnection: Codable, Sendable { let version: CoreVersion; let capabilities: CoreCapabilities }
struct CoreSubmissionRejection: Error, Sendable {
    let failure: JobFailure
}
// CORE-004 single submissions can reject during preflight without allocating
// a job. Such a response is not a malformed JobSnapshot and owns no job ID.
private enum SubmissionResponse: Decodable {
    case job(JobSnapshot)
    case rejected(JobFailure)
    private enum CodingKeys: String, CodingKey { case accepted }
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        if container.contains(.accepted) {
            let response = try QueueControlResponse(from: decoder)
            guard !response.accepted, response.jobIds == nil,
                  let failure = response.errors?.first else {
                throw DecodingError.dataCorruptedError(forKey: .accepted, in: container,
                    debugDescription: "Invalid single-submission preflight rejection")
            }
            self = .rejected(JobFailure(code: failure.code, message: failure.message))
        } else {
            self = .job(try JobSnapshot(from: decoder))
        }
    }
}
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
        let submission: SubmissionResponse = try data.withUnsafeBytes { bytes in
            try output("submit") { engine, out in
                ms_submit_job(engine, bytes.bindMemory(to: UInt8.self).baseAddress, bytes.count, out)
            }
        }
        let response: JobSnapshot
        switch submission {
        case .job(let value): response = value
        case .rejected(let failure): throw CoreSubmissionRejection(failure: failure)
        }
        knownJobs.insert(response.jobId)
        return response
    }
    func control<T: Decodable>(_ data: Data, as: T.Type) throws -> T {
        try data.withUnsafeBytes { bytes in
            try output("queue_control") { engine, out in
                ms_submit_job(engine, bytes.bindMemory(to: UInt8.self).baseAddress, bytes.count, out)
            }
        }
    }
    func remember(_ ids: [String]) { knownJobs.formUnion(ids) }
    func forget(_ id: String) { knownJobs.remove(id) }
    func reconcile(_ snapshot: QueueSnapshot) {
        knownJobs = Set(snapshot.queuedJobIds + snapshot.history.map(\.jobId))
        if let running = snapshot.runningJobId { knownJobs.insert(running) }
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
    private var cleanupUnconfirmed = false
    func connect(backend requested: BackendConfiguration? = nil) throws -> CoreConnection {
        guard !closing else { throw BridgeError(code: -7, operation: "closing") }
        guard !cleanupUnconfirmed else { throw BridgeError(code: -100, operation: "cleanup_unconfirmed") }
        if owner == nil {
            closeSnapshots.removeAll(keepingCapacity: true)
            owner = try EngineOwner(backend: requested); backend = requested
        }
        guard requested == backend else { throw BridgeError(code: -7, operation: "configuration_requires_close") }
        guard let owner else { throw BridgeError(code: -100, operation: "connect") }
        return try CoreConnection(version: owner.version(), capabilities: owner.capabilities())
    }
    func submit(outcome: MockOutcome) throws -> JobSnapshot {
        guard !closing else { throw BridgeError(code: -7, operation: "closing") }
        guard !cleanupUnconfirmed else { throw BridgeError(code: -100, operation: "cleanup_unconfirmed") }
        guard let owner else { throw BridgeError(code: -2, operation: "closed") }
        let request = Data("{\"schema_version\":0,\"operation\":\"mock\",\"outcome\":\"\(outcome.rawValue)\"}".utf8)
        return try owner.submit(request)
    }
    func submitProbe(source: URL) throws -> JobSnapshot {
        try submitRawForSelfTest(requestData(ProbeRequest(source: MediaLocation(uri: source.absoluteString))))
    }
    func submitAudioPreview(source: URL, destination: URL) throws -> JobSnapshot {
        try submitRawForSelfTest(requestData(AudioPreviewRequest(source: MediaLocation(uri: source.absoluteString), destination: MediaLocation(uri: destination.absoluteString))))
    }
    func submitTrim(source: URL, destination: URL, range: MediaRange, strategy: ExportStrategy = .legacyStreamCopy) throws -> JobSnapshot {
        guard strategy.transcodePreset == nil else { throw BridgeError(code: -1, operation: "trim_strategy") }
        return try submitRawForSelfTest(requestData(TrimRequest(source: MediaLocation(uri: source.absoluteString),
                                                       destination: MediaLocation(uri: destination.absoluteString), range: range,
                                                       strategy: strategy.wireStrategy, profile: strategy.profile)))
    }
    func submitAI(source: URL, destination: URL) throws -> AISubmission {
        guard !closing, !cleanupUnconfirmed, let owner, backend?.aiBackend != nil else {
            throw BridgeError(code: -8, operation: "ai_unavailable")
        }
        let data = try requestData(AIRequest(source: MediaLocation(uri: source.absoluteString),
                                             destination: MediaLocation(uri: destination.absoluteString)))
        let response = try owner.control(data, as: AISubmission.self)
        if let id = response.jobId { owner.remember([id]) }
        if response.errors?.contains(where: { $0.code == "CLEANUP_FAILED" }) == true { cleanupUnconfirmed = true }
        return response
    }
    func submitMixStems(directory: URL, destination: URL, includeVocals: Bool, includeAccompaniment: Bool) throws -> JobSnapshot {
        guard !closing, !cleanupUnconfirmed, let owner, backend?.mixStemsEnabled == true else {
            throw BridgeError(code: -8, operation: "mix_stems_unavailable")
        }
        let data = try requestData(MixStemsRequest(
            vocals: MediaLocation(uri: directory.appendingPathComponent("vocals.wav").absoluteString),
            accompaniment: MediaLocation(uri: directory.appendingPathComponent("accompaniment.wav").absoluteString),
            destination: MediaLocation(uri: destination.absoluteString), includeVocals: includeVocals,
            includeAccompaniment: includeAccompaniment))
        return try owner.submit(data)
    }
    func submitRawForSelfTest(_ data: Data) throws -> JobSnapshot {
        guard !closing else { throw BridgeError(code: -7, operation: "closing") }
        guard !cleanupUnconfirmed else { throw BridgeError(code: -100, operation: "cleanup_unconfirmed") }
        guard let owner else { throw BridgeError(code: -2, operation: "closed") }
        return try owner.submit(data)
    }
    private func control(_ data: Data, operation: String, submissionId: String? = nil,
                         expectedJobs: Int? = nil) throws -> QueueControlResponse {
        guard !closing else { throw BridgeError(code: -7, operation: "closing") }
        guard !cleanupUnconfirmed else { throw BridgeError(code: -100, operation: "cleanup_unconfirmed") }
        guard backend?.core004Enabled == true, let owner else {
            throw BridgeError(code: -8, operation: "core004_not_enabled")
        }
        // A decoding failure can follow acceptance. Caller retains its immutable
        // token/payload/leases and may replay this SAME request to recover IDs.
        let response = try owner.control(data, as: QueueControlResponse.self)
        guard response.schemaVersion == 0, response.operation == operation else {
            throw BridgeError(code: -100, operation: "control_response_mismatch")
        }
        if response.accepted {
            if let submissionId, let expectedJobs {
                guard response.submissionId == submissionId, response.replayed != nil,
                      let ids = response.jobIds, ids.count == expectedJobs,
                      Set(ids).count == ids.count, ids.allSatisfy({ !$0.isEmpty && $0.utf8.count <= 128 }) else {
                    throw BridgeError(code: -100, operation: "submission_receipt_unconfirmed")
                }
                owner.remember(ids)
            }
        } else {
            guard response.jobIds == nil, let errors = response.errors, !errors.isEmpty else {
                throw BridgeError(code: -100, operation: "rejection_receipt_unconfirmed")
            }
            if errors.contains(where: { $0.code == "CLEANUP_FAILED" }) { cleanupUnconfirmed = true }
        }
        return response
    }
    func batchSubmit(items: [QueueMediaRequest], submissionId: String) throws -> QueueControlResponse {
        try control(requestData(BatchRequest(submissionId: submissionId, items: items)),
                    operation: "batch_submit", submissionId: submissionId, expectedJobs: items.count)
    }
    func storagePreview(items: [QueueMediaRequest]) throws -> StoragePreviewResponse {
        guard !closing, !cleanupUnconfirmed, let owner, backend?.core004Enabled == true else {
            throw BridgeError(code: -8, operation: "storage_preview_unavailable")
        }
        let response = try owner.control(requestData(StoragePreviewRequest(items: items)), as: StoragePreviewResponse.self)
        guard response.schemaVersion == 0, response.operation == "storage_preview",
              response.volumes.count <= items.count,
              Set(response.volumes.map(\.volumeIndex)).count == response.volumes.count else {
            throw BridgeError(code: -100, operation: "storage_preview_unconfirmed")
        }
        let indices = response.volumes.flatMap(\.itemIndices)
        guard Set(indices).count == indices.count,
              indices.allSatisfy({ items.indices.contains($0) }),
              response.volumes.allSatisfy({ $0.volumeIndex >= 0 && !$0.itemIndices.isEmpty &&
                  $0.sufficient == ($0.availableStorageBytes >= $0.requiredStorageBytes) }),
              response.errors.allSatisfy({ items.indices.contains($0.index) }),
              !response.ready || (response.errors.isEmpty && indices.count == items.count && response.volumes.allSatisfy(\.sufficient)) else {
            throw BridgeError(code: -100, operation: "storage_preview_unconfirmed")
        }
        if response.errors.contains(where: { $0.code == "CLEANUP_FAILED" }) { cleanupUnconfirmed = true }
        return response
    }
    func queueSnapshot() throws -> QueueSnapshot {
        guard !closing else { throw BridgeError(code: -7, operation: "closing") }
        guard let owner, backend?.core004Enabled == true else {
            throw BridgeError(code: -8, operation: "core004_not_enabled")
        }
        return try currentQueueSnapshot(owner)
    }
    private func currentQueueSnapshot(_ owner: EngineOwner) throws -> QueueSnapshot {
        let response = try owner.control(requestData(QueueSnapshotRequest()), as: QueueSnapshot.self)
        guard response.schemaVersion == 0, response.operation == "queue_snapshot",
              response.queueCapacity == 32, response.historyLimit == 100,
              response.activeCount >= 0, response.activeCount <= response.queueCapacity,
              Set(response.queuedJobIds).count == response.queuedJobIds.count else {
            throw BridgeError(code: -100, operation: "queue_snapshot_unconfirmed")
        }
        if response.cleanupUnconfirmed || response.resources.globalQuarantined { cleanupUnconfirmed = true }
        owner.reconcile(response)
        return response
    }
    func reorderQueue(jobIDs: [String]) throws -> QueueControlResponse {
        let value = try control(requestData(QueueReorderRequest(jobIds: jobIDs)), operation: "queue_reorder")
        if value.accepted && value.queuedJobIds != jobIDs {
            throw BridgeError(code: -100, operation: "queue_order_unconfirmed")
        }
        return value
    }
    func retry(jobID: String, newDestination: URL, submissionId: String) throws -> QueueControlResponse {
        try control(requestData(QueueRetryRequest(jobId: jobID,
                    newDestination: MediaLocation(uri: newDestination.absoluteString), submissionId: submissionId)),
                    operation: "job_retry", submissionId: submissionId, expectedJobs: 1)
    }
    func clearHistory() throws -> QueueControlResponse {
        let value = try control(requestData(QueueClearRequest()), operation: "history_clear")
        if value.accepted {
            guard let count = value.clearedCount, count >= 0 else {
                throw BridgeError(code: -100, operation: "history_clear_unconfirmed")
            }
            if let owner { _ = try currentQueueSnapshot(owner) }
        }
        return value
    }
    func poll(id: String) throws -> JobSnapshot {
        guard let owner else { throw BridgeError(code: -2, operation: "closed") }
        let value = try owner.poll(id)
        if value.error?.code == "CLEANUP_FAILED" || value.aiResult?.cleanupUnconfirmed == true { cleanupUnconfirmed = true }
        return value
    }
    func cancel(id: String) throws {
        guard let owner else { throw BridgeError(code: -2, operation: "closed") }
        try owner.cancel(id)
    }
    func close() async throws {
        guard !closing else { throw BridgeError(code: -7, operation: "closing") }
        guard !cleanupUnconfirmed else { throw BridgeError(code: -100, operation: "cleanup_unconfirmed") }
        guard let owner else { return }
        closing = true
        defer { closing = false }
        do {
            // Queue history is bounded/clearable; discover current active IDs,
            // including any accepted submission whose receipt failed decoding.
            if backend?.core004Enabled == true { _ = try currentQueueSnapshot(owner) }
            guard !cleanupUnconfirmed else { throw BridgeError(code: -100, operation: "cleanup_unconfirmed") }
            for id in owner.jobIDs {
                do { try owner.cancel(id) }
                catch let error as BridgeError where error.code == -6 { owner.forget(id) }
            }
            // Cancellation is a separate bounded drain, not the media encode deadline.
            // Do not wait 600s merely because accurate processing permits that long.
            let deadline = ContinuousClock.now + .seconds(15)
            while true {
                var active = false
                for id in owner.jobIDs {
                    let snapshot: JobSnapshot
                    do { snapshot = try owner.poll(id) }
                    catch let error as BridgeError where error.code == -6 {
                        owner.forget(id)
                        continue
                    }
                    active = active || !["succeeded", "failed", "cancelled"].contains(snapshot.state)
                    if snapshot.error?.code == "CLEANUP_FAILED" { cleanupUnconfirmed = true }
                    if ["succeeded", "failed", "cancelled"].contains(snapshot.state) {
                        closeSnapshots.removeAll { $0.jobId == snapshot.jobId }
                        closeSnapshots.append(snapshot)
                        if closeSnapshots.count > 256 { closeSnapshots.removeFirst(closeSnapshots.count - 256) }
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
            guard !cleanupUnconfirmed else { throw BridgeError(code: -100, operation: "cleanup_unconfirmed") }
            do { try owner.destroy() }
            catch { throw error }
            lastDiagnostics.accumulate(owner.diagnostics)
            self.owner = nil; backend = nil
            return
        }
        guard !cleanupUnconfirmed else { throw BridgeError(code: -100, operation: "cleanup_unconfirmed") }
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
