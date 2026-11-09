import AppKit
import AVFoundation
import AVKit
import Foundation
import SwiftUI

struct PreviewDiagnostics: Codable, Sendable {
    var status = "empty"
    var ready = false
    var seekCompleted = false
    var seekNormalizedUs: Int64?
    var observedItemUs: Int64?
    var timelineOffsetUs: Int64?
    var assetDurationUs: Int64?
    var playbackObserved = false
    var firstVisibleImageItemUs: Int64?
    var firstVisibleImageConfirmed = false
    var imageActualItemUs: Int64?
    var imageColorSpace: String?
    var videoTrackStartUs: Int64?
}

@MainActor
final class PreviewController: ObservableObject {
    let player = AVPlayer()
    @Published var status = "尚未导入媒体"
    @Published var positionUs: Int64 = 0
    @Published var ready = false
    @Published var playing = false
    private(set) var diagnostics = PreviewDiagnostics()
    private var generation = UUID()
    private var durationUs: Int64 = 0
    private var timelineOffsetUs: Int64 = 0
    private var endItemUs: Int64 = 0
    private var periodicObserver: Any?
    private var loadingCount = 0
    private var loading: Bool { loadingCount > 0 }
    private var activeAsset: AVURLAsset?
    private var activeGenerator: AVAssetImageGenerator?
    private var imageRequests = 0

    func clear() {
        generation = UUID()
        activeAsset?.cancelLoading()
        activeGenerator?.cancelAllCGImageGeneration()
        activeGenerator = nil; activeAsset = nil
        player.pause()
        if let periodicObserver { player.removeTimeObserver(periodicObserver) }
        periodicObserver = nil
        player.replaceCurrentItem(with: nil)
        ready = false; playing = false; positionUs = 0
        diagnostics = PreviewDiagnostics()
        status = "尚未导入媒体"
    }

    private func generateImage(_ generator: AVAssetImageGenerator, at time: CMTime) async throws -> (image: CGImage, actualTime: CMTime) {
        // Retain/cancel the native generator only on MainActor. Its callback API
        // performs decoding asynchronously and returns owned Sendable image/time
        // values; the callback captures no UI or generator reference. This avoids
        // sending a retained non-Sendable generator to the @concurrent overlay.
        try await withCheckedThrowingContinuation { continuation in
            generator.generateCGImagesAsynchronously(forTimes: [NSValue(time: time)]) { _, image, actual, result, error in
                guard result == .succeeded, let image else {
                    continuation.resume(throwing: error ?? CheckFailure(message: "系统预览图像生成已取消或失败。"))
                    return
                }
                continuation.resume(returning: (image, actual))
            }
        }
    }
    private func micros(_ time: CMTime) throws -> Int64 {
        guard time.isNumeric else { throw CheckFailure(message: "预览时间轴不可读取。") }
        let converted = CMTimeConvertScale(time, timescale: 1_000_000, method: .roundTowardZero)
        guard converted.isNumeric else { throw CheckFailure(message: "预览时间超出可表示范围。") }
        return converted.value
    }

    func load(url: URL, media: MediaInfo) async throws {
        clear()
        loadingCount += 1
        defer { loadingCount -= 1 }
        let token = generation
        status = "正在准备预览…"
        diagnostics.status = "loading"
        let asset = AVURLAsset(url: url)
        activeAsset = asset
        guard try await asset.load(.isPlayable) else { throw CheckFailure(message: "系统预览器不支持这份媒体。") }
        let tracks = try await asset.loadTracks(withMediaType: .video)
        guard let video = tracks.first else { throw CheckFailure(message: "预览没有可用的视频轨道。") }
        let range = try await video.load(.timeRange)
        let assetDuration = try await asset.load(.duration)
        let segments = try await video.load(.segments)
        let content = segments.filter { !$0.isEmpty }
        guard content.count == 1, let segment = content.first else {
            throw CheckFailure(message: "预览暂不支持复杂编辑时间线；导出支持由 Core 判断。")
        }
        let offset = try micros(segment.timeMapping.target.start)
        guard token == generation else { return }
        let generator = AVAssetImageGenerator(asset: asset)
        generator.appliesPreferredTrackTransform = true
        generator.requestedTimeToleranceBefore = .zero
        generator.requestedTimeToleranceAfter = .zero
        activeGenerator = generator; imageRequests += 1
        let firstImage: (image: CGImage, actualTime: CMTime)
        do { firstImage = try await generateImage(generator, at: CMTime(value: offset, timescale: 1_000_000)) }
        catch { imageRequests -= 1; throw error }
        imageRequests -= 1
        if token == generation { activeGenerator = nil }
        let firstImageUs = try micros(firstImage.actualTime)
        guard firstImageUs >= offset else { throw CheckFailure(message: "未取得内容区间内的系统预览画面。") }
        guard token == generation else { return }
        let (end, overflow) = offset.addingReportingOverflow(media.durationUs)
        guard offset >= 0, !overflow else { throw CheckFailure(message: "系统预览时间轴超出范围。") }
        durationUs = media.durationUs
        timelineOffsetUs = offset
        endItemUs = end
        diagnostics.timelineOffsetUs = offset
        diagnostics.firstVisibleImageItemUs = firstImageUs
        diagnostics.firstVisibleImageConfirmed = firstImage.image.width > 0 && firstImage.image.height > 0
        diagnostics.videoTrackStartUs = try micros(range.start)
        diagnostics.assetDurationUs = try micros(assetDuration)
        // Empty MP4 edits can make track.timeRange.start == 0 even when actual
        // content begins at +5s. Use the nonempty edit segment's target timeline
        // and confirm a real generated image there; raw PTS/first filler sample
        // alone do not establish the player timeline.
        let item = AVPlayerItem(asset: asset)
        player.replaceCurrentItem(with: item)
        let deadline = ContinuousClock.now + .seconds(12)
        while item.status == .unknown && ContinuousClock.now < deadline {
            try await Task.sleep(for: .milliseconds(20))
            guard token == generation else { return }
        }
        guard item.status == .readyToPlay else { throw CheckFailure(message: "预览未能就绪；仍可依据 Core 信息判断是否导出。") }
        ready = true; diagnostics.ready = true; diagnostics.status = "ready"
        status = "预览已就绪"
        periodicObserver = player.addPeriodicTimeObserver(forInterval: CMTime(value: 100_000, timescale: 1_000_000), queue: .main) { [weak self] time in
            Task { @MainActor [weak self] in
                guard let self, self.generation == token, time.isNumeric else { return }
                let converted = CMTimeConvertScale(time, timescale: 1_000_000, method: .roundTowardZero)
                guard converted.isNumeric else { return }
                let raw = converted.value
                self.positionUs = self.normalizedPosition(raw)
                if self.player.rate > 0 { self.diagnostics.playbackObserved = true }
                if raw >= self.endItemUs { self.player.pause() }
                self.playing = self.player.rate > 0
            }
        }
        try await seek(normalizedUs: 0)
    }

    private func normalizedPosition(_ itemUs: Int64) -> Int64 {
        if itemUs <= timelineOffsetUs { return 0 }
        if itemUs >= endItemUs { return durationUs }
        return itemUs - timelineOffsetUs
    }
    func shutdown() async throws {
        generation = UUID()
        activeAsset?.cancelLoading()
        activeGenerator?.cancelAllCGImageGeneration()
        let deadline = ContinuousClock.now + .seconds(15)
        while loading || imageRequests > 0 {
            guard ContinuousClock.now < deadline else { throw CheckFailure(message: "系统预览停止尚未确认，已保留文件访问；请重试安全关闭。") }
            // Cleanup can be entered by an already-cancelled caller. A fresh
            // detached sleeper still yields MainActor so load's defer can run.
            _ = await Task.detached { try? await Task.sleep(for: .milliseconds(20)) }.value
        }
        clear()
    }
    func seek(normalizedUs: Int64) async throws {
        guard ready else { throw CheckFailure(message: "预览尚未就绪。") }
        let bounded = max(0, min(durationUs, normalizedUs))
        let (raw, overflow) = timelineOffsetUs.addingReportingOverflow(bounded)
        guard !overflow else { throw CheckFailure(message: "预览时间超出范围。") }
        let token = generation
        let completed = await player.seek(to: CMTime(value: raw, timescale: 1_000_000), toleranceBefore: .zero, toleranceAfter: .zero)
        guard token == generation, ready else { throw CheckFailure(message: "预览定位所属媒体已关闭或切换。") }
        let observed = try micros(player.currentTime())
        diagnostics.seekCompleted = completed
        diagnostics.seekNormalizedUs = bounded
        diagnostics.observedItemUs = observed
        positionUs = normalizedPosition(observed)
        guard completed else { throw CheckFailure(message: "预览定位被中断，请重试。") }
    }
    func writePreviewFrame(to url: URL, normalizedUs: Int64) async throws {
        guard ready, let asset = player.currentItem?.asset else { throw CheckFailure(message: "预览尚未就绪。") }
        let (itemUs, overflow) = timelineOffsetUs.addingReportingOverflow(normalizedUs)
        guard !overflow else { throw CheckFailure(message: "预览时间超出范围。") }
        let generator = AVAssetImageGenerator(asset: asset)
        generator.appliesPreferredTrackTransform = true
        generator.requestedTimeToleranceBefore = .zero; generator.requestedTimeToleranceAfter = .zero
        let token = generation
        activeGenerator = generator; imageRequests += 1
        defer { imageRequests -= 1
        if token == generation { activeGenerator = nil } }
        let image = try await generateImage(generator, at: CMTime(value: itemUs, timescale: 1_000_000))
        guard token == generation, ready else { throw CheckFailure(message: "预览测试帧所属媒体已关闭。") }
        diagnostics.imageActualItemUs = try micros(image.actualTime)
        diagnostics.imageColorSpace = image.image.colorSpace?.name.map { $0 as String }
        guard let bytes = NSBitmapImageRep(cgImage: image.image).representation(using: .png, properties: [:]) else {
            throw CheckFailure(message: "无法编码预览测试帧。")
        }
        // This entry exists only for independent QA. Never replace any leaf,
        // symlink, or existing frame; no source path is used as a test output.
        try bytes.write(to: url, options: .withoutOverwriting)
    }
    func togglePlayback() {
        guard ready else { return }
        if player.rate > 0 { player.pause(); playing = false }
        else if positionUs >= durationUs {
            let token = generation
            Task {
                do { try await seek(normalizedUs: 0) } catch { return }
                guard token == generation, ready else { return }
                player.play(); playing = true
            }
        } else { player.play(); playing = true }
    }
    func pause() { player.pause(); playing = false }
}

struct NativePreview: NSViewRepresentable {
    let player: AVPlayer
    func makeNSView(context: Context) -> AVPlayerView {
        let view = AVPlayerView()
        view.player = player
        view.controlsStyle = .none
        view.videoGravity = .resizeAspect
        view.showsFullScreenToggleButton = true
        return view
    }
    func updateNSView(_ view: AVPlayerView, context: Context) { view.player = player }
}
