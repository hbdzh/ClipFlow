import Foundation

// One model-owned, private PCM proxy. The original asset and its permission
// remain the source identity; this file is never an export or a library asset.
@MainActor
final class AudioPreviewOwner {
    let root: URL
    let destination: URL
    let sourceAccess: SecurityAccess
    let bridge = CoreBridge()
    private var preparation: Task<URL, Error>?
    private var cleanup: Task<Void, Error>?
    private var closing = false

    init(sourceAccess: SecurityAccess) throws {
        self.sourceAccess = sourceAccess
        let temporary = FileManager.default.temporaryDirectory.resolvingSymlinksInPath()
        root = temporary.appendingPathComponent("mediastudio-audio-preview-" + UUID().uuidString, isDirectory: true)
        destination = root.appendingPathComponent("preview.wav")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
    }
    func prepare(source: URL, media: MediaInfo, configuration: BackendConfiguration) async throws -> URL {
        guard !closing else { throw CheckFailure(message: "原音预览已停止准备。") }
        if preparation == nil {
            preparation = Task { @MainActor [self] in
                guard !closing else { throw CheckFailure(message: "原音预览已停止准备。") }
                _ = try await bridge.connect(backend: configuration)
                guard !closing else { throw CheckFailure(message: "原音预览已停止准备。") }
                let submitted = try await bridge.submitAudioPreview(source: source, destination: destination)
                while true {
                    guard !closing else { throw CheckFailure(message: "原音预览已停止准备。") }
                    let value = try await bridge.poll(id: submitted.jobId)
                    if value.state == "failed" { throw CoreSubmissionRejection(failure: value.error ?? JobFailure(code: "PROCESS_FAILED", message: "Audio preview failed.")) }
                    if value.state == "cancelled" { throw CheckFailure(message: "原音预览已停止准备。") }
                    if value.state == "succeeded" {
                        guard let result = value.result, result.previewOnly == true,
                              let count = result.sampleCount, let rate = result.sampleRate,
                              let duration = result.sourceContentDurationUs,
                              result.sourceAudioOriginUs == media.audioOriginUs,
                              rate == media.streams.first?.sampleRate.flatMap(Int.init),
                              result.channels == media.streams.first?.channels,
                              duration == media.durationUs,
                              (count * 1_000_000 / Int64(rate) - duration).magnitude <= 1 else {
                            throw CheckFailure(message: "原音预览结果与已探测的内容时间线不一致。")
                        }
                        return destination
                    }
                    try await Task.sleep(for: .milliseconds(20))
                }
            }
        }
        return try await preparation!.value
    }
    func shutdown() async throws {
        closing = true
        if cleanup == nil {
            cleanup = Task { @MainActor [self] in
                // connect/submit may be suspended. closing blocks their next
                // boundary; CoreBridge discovers and drains accepted jobs.
                try await bridge.close()
                _ = try? await preparation?.value
                // A connect already enqueued before closing may have executed
                // after the first no-owner close. No preparation calls remain
                // after its task returns, so this final close seals that race.
                try await bridge.close()
                let directory = root
                try await Task.detached { try FileManager.default.removeItem(at: directory) }.value
            }
        }
        do { try await cleanup!.value }
        catch { cleanup = nil; throw error }
    }
}
