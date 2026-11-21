import Foundation

// Presentation choices only: Core validates support and builds every media plan.
enum TranscodePreset: String, Codable, CaseIterable, Sendable {
    case standard = "mp4_h264_standard_v1"
    case compact720p = "mp4_h264_720p_v1"
    var title: String { self == .standard ? "标准质量 · 保持尺寸" : "720p 小体积 · 不放大" }
}
struct AudioSelection: Codable, Equatable, Hashable, Sendable {
    let mode: String
    let streamIndex: Int?
    static let all = Self(mode: "all_supported", streamIndex: nil)
    static let none = Self(mode: "none", streamIndex: nil)
    static func one(index: Int) -> Self { Self(mode: "one", streamIndex: index) }
}
struct VideoTransform: Codable, Equatable, Sendable {
    var rotationDegrees = 0
    var scalePercent = 100
    var offsetX = 0
    var offsetY = 0
    static let identity = VideoTransform()
    var isIdentity: Bool { self == .identity }
}
struct VisibleContentRect: Codable, Sendable {
    let x: Int
    let y: Int
    let width: Int
    let height: Int
}
struct TranscodePlan: Codable, Sendable {
    let profile: String
    let contentWidth: Int
    let contentHeight: Int
    let canvasWidth: Int
    let canvasHeight: Int
    let paddingRight: Int
    let paddingBottom: Int
    let selectedSourceAudioStreamIndices: [Int]
    let maximumOutputFileBytes: UInt64
    let videoTransform: VideoTransform?
    let visibleContentRect: VisibleContentRect?

    // Verify evidence shape, not geometry/codec business rules owned by Core.
    func matches(profile expected: String?, output: MediaInfo?) -> Bool {
        guard profile == expected, TranscodePreset(rawValue: profile) != nil,
              contentWidth > 0, contentHeight > 0, canvasWidth > 0, canvasHeight > 0,
              paddingRight >= 0, paddingBottom >= 0,
              maximumOutputFileBytes > 0,
              selectedSourceAudioStreamIndices.allSatisfy({ $0 >= 0 }),
              Set(selectedSourceAudioStreamIndices).count == selectedSourceAudioStreamIndices.count,
              let output else { return false }
        if videoTransform != nil {
            guard profile == TranscodePreset.standard.rawValue, let rect = visibleContentRect,
                  rect.x >= 0, rect.y >= 0, rect.width == contentWidth, rect.height == contentHeight else { return false }
            let right = rect.x.addingReportingOverflow(rect.width)
            let bottom = rect.y.addingReportingOverflow(rect.height)
            guard !right.overflow, !bottom.overflow, right.partialValue <= canvasWidth, bottom.partialValue <= canvasHeight,
                  canvasWidth - right.partialValue == paddingRight, canvasHeight - bottom.partialValue == paddingBottom else { return false }
        } else {
            let horizontal = contentWidth.addingReportingOverflow(paddingRight)
            let vertical = contentHeight.addingReportingOverflow(paddingBottom)
            guard visibleContentRect == nil, !horizontal.overflow, !vertical.overflow,
                  horizontal.partialValue == canvasWidth, vertical.partialValue == canvasHeight else { return false }
        }
        let video = output.streams.filter { $0.codecType == "video" }
        return video.count == 1 && video[0].width == canvasWidth && video[0].height == canvasHeight
            && output.streams.filter { $0.codecType == "audio" }.count == selectedSourceAudioStreamIndices.count
    }
}
enum QueueMediaRequest: Encodable, Sendable {
    case trim(source: URL, destination: URL, range: MediaRange, strategy: ExportStrategy)
    case transcode(source: URL, destination: URL, range: MediaRange?, preset: TranscodePreset, audio: AudioSelection, videoTransform: VideoTransform?)

    private enum CodingKeys: String, CodingKey {
        case schemaVersion, operation, source, destination, range, strategy, profile
        case streamSelection, allowReencodeFallback, audioSelection, videoTransform
    }
    private struct Location: Encodable { let uri: String }
    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(0, forKey: .schemaVersion)
        switch self {
        case let .trim(source, destination, range, strategy):
            guard strategy.transcodePreset == nil else { throw BridgeError(code: -1, operation: "trim_strategy") }
            try c.encode("trim", forKey: .operation)
            try c.encode(Location(uri: source.absoluteString), forKey: .source)
            try c.encode(Location(uri: destination.absoluteString), forKey: .destination)
            try c.encode(range, forKey: .range)
            try c.encode(strategy.wireStrategy, forKey: .strategy)
            try c.encodeIfPresent(strategy.profile, forKey: .profile)
            try c.encode("all_supported", forKey: .streamSelection)
            try c.encode(false, forKey: .allowReencodeFallback)
        case let .transcode(source, destination, range, preset, audio, videoTransform):
            try c.encode("transcode", forKey: .operation)
            try c.encode(Location(uri: source.absoluteString), forKey: .source)
            try c.encode(Location(uri: destination.absoluteString), forKey: .destination)
            try c.encodeIfPresent(range, forKey: .range)
            try c.encode(preset.rawValue, forKey: .profile)
            try c.encode(audio, forKey: .audioSelection)
            try c.encodeIfPresent(videoTransform, forKey: .videoTransform)
        }
    }
}
struct QueueRejection: Codable, Sendable {
    let index: Int
    let code: String
    let message: String
}
struct QueueControlResponse: Codable, Sendable {
    let schemaVersion: Int
    let operation: String
    let accepted: Bool
    let submissionId: String?
    let replayed: Bool?
    let jobIds: [String]?
    let queuedJobIds: [String]?
    let clearedCount: Int?
    let errors: [QueueRejection]?
}
struct QueueJobSummary: Codable, Sendable {
    let jobId: String
    let operation: String
    let state: String
    let sourceName: String?
    let destinationName: String?
    let requestedRangeUs: MediaRange?
    let profile: String?
    let audioSelection: AudioSelection?
    let error: JobFailure?
    let createdSequence: UInt64
    let transcodePlan: TranscodePlan?
}
struct QueueResources: Codable, Sendable {
    let scope: String
    let reservedStorageBytes: UInt64
    let estimatedRetainedPlanBytes: UInt64
    let maximumEstimatedPlanBytes: UInt64
    let globalReservedStorageBytes: UInt64
    let globalReservedTargetCount: UInt64
    let globalEstimatedRetainedPlanBytes: UInt64
    let globalQuarantined: Bool
}
struct QueueSnapshot: Codable, Sendable {
    let schemaVersion: Int
    let operation: String
    let queuedJobIds: [String]
    let runningJobId: String?
    let activeCount: Int
    let queueCapacity: Int
    let historyLimit: Int
    let history: [QueueJobSummary]
    let resources: QueueResources
    let cleanupUnconfirmed: Bool
}
