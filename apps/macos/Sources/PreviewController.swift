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
    var mediaKind: String?
    var mappingSourceDurationUs: Int64?
    var mappingTargetDurationUs: Int64?
    var audioStartItemUs: Int64?
    var audioEndItemUs: Int64?
    var audioEndpointMappingConfirmed = false
    var timelineThumbnailCount = 0
}

@MainActor
final class PreviewController: ObservableObject {
    let player = AVPlayer()
    @Published var status = "尚未导入媒体"
    @Published var positionUs: Int64 = 0
    @Published var ready = false
    @Published var playing = false
    @Published private(set) var timelineThumbnails: [CGImage] = []
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
    private var timelineGenerator: AVAssetImageGenerator?
    private var imageRequests = 0

    func clear() {
        generation = UUID()
        activeAsset?.cancelLoading()
        activeGenerator?.cancelAllCGImageGeneration()
        timelineGenerator?.cancelAllCGImageGeneration()
        timelineGenerator = nil; timelineThumbnails = []
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
        let hasVideo = media.streams.contains { $0.codecType == "video" }
        let tracks = try await asset.loadTracks(withMediaType: hasVideo ? .video : .audio)
        guard let track = tracks.first else { throw CheckFailure(message: "预览没有可用的媒体轨道。") }
        let range = try await track.load(.timeRange)
        let assetDuration = try await asset.load(.duration)
        let segments = try await track.load(.segments)
        let content = segments.filter { !$0.isEmpty }
        guard content.count == 1, let segment = content.first else {
            throw CheckFailure(message: "预览暂不支持复杂编辑时间线；导出支持由 Core 判断。")
        }
        let sourceDuration = segment.timeMapping.source.duration
        let targetDuration = segment.timeMapping.target.duration
        guard sourceDuration.isNumeric, targetDuration.isNumeric,
              CMTimeCompare(sourceDuration, .zero) > 0,
              CMTimeCompare(sourceDuration, targetDuration) == 0,
              media.durationUs > 0, media.durationUs <= 8_000_000_000_000 else {
            throw CheckFailure(message: "预览暂不支持变速或与 Core 不一致的编辑时间线；导出支持由 Core 判断。")
        }
        let (durationDifference, durationOverflow) = try micros(targetDuration).subtractingReportingOverflow(media.durationUs)
        guard !durationOverflow, durationDifference.magnitude <= 1 else {
            throw CheckFailure(message: "预览暂不支持与 Core 时长不一致的编辑时间线；导出支持由 Core 判断。")
        }
        let offset = try micros(segment.timeMapping.target.start)
        guard token == generation else { return }
        diagnostics.mediaKind = hasVideo ? "video" : "audio"
        diagnostics.mappingSourceDurationUs = try micros(sourceDuration)
        diagnostics.mappingTargetDurationUs = try micros(targetDuration)
        if hasVideo {
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
        let (firstImageDifference, firstImageOverflow) = firstImageUs.subtractingReportingOverflow(offset)
        guard !firstImageOverflow, firstImageDifference.magnitude <= 1 else {
            throw CheckFailure(message: "预览首帧与内容时间线原点不一致；导出支持由 Core 判断。")
        }
        guard token == generation else { return }
        diagnostics.firstVisibleImageItemUs = firstImageUs
        diagnostics.firstVisibleImageConfirmed = firstImage.image.width > 0 && firstImage.image.height > 0
        }
        let (end, overflow) = offset.addingReportingOverflow(media.durationUs)
        guard offset >= 0, !overflow else { throw CheckFailure(message: "系统预览时间轴超出范围。") }
        durationUs = media.durationUs
        timelineOffsetUs = offset
        endItemUs = end
        diagnostics.timelineOffsetUs = offset
        diagnostics.videoTrackStartUs = hasVideo ? try micros(range.start) : nil
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
        if !hasVideo {
            // A single unit-rate edit is necessary but insufficient: on this
            // OS some AAC items advertise the full track segment while their
            // playable item ends early. Prove the actual player's endpoints
            // before presenting it as a canonical Core audio timeline.
            let itemEnd = try micros(item.duration)
            let (coverage, coverageOverflow) = itemEnd.subtractingReportingOverflow(end)
            guard !coverageOverflow, coverage >= -1,
                  let rate = media.streams.first(where: { $0.codecType == "audio" })?.sampleRate.flatMap(Int64.init),
                  rate > 0, rate <= 1_000_000 else { throw CheckFailure(message: "系统音频预览未覆盖完整内容时间线。") }
            let startCompleted = await player.seek(to: CMTime(value: offset, timescale: 1_000_000), toleranceBefore: .zero, toleranceAfter: .zero)
            guard token == generation else { return }
            let startObserved = try micros(player.currentTime())
            let (startDifference, startOverflow) = startObserved.subtractingReportingOverflow(offset)
            guard startCompleted, !startOverflow, startDifference.magnitude <= 1 else {
                throw CheckFailure(message: "系统音频预览起点与内容时间线不一致。")
            }
            let endpoint = end - 1
            let endCompleted = await player.seek(to: CMTime(value: endpoint, timescale: 1_000_000), toleranceBefore: .zero, toleranceAfter: .zero)
            guard token == generation else { return }
            let endObserved = try micros(player.currentTime())
            let (endDifference, endOverflow) = endObserved.subtractingReportingOverflow(endpoint)
            guard endCompleted, !endOverflow, endDifference.magnitude <= UInt64((1_000_000 + rate - 1) / rate) else {
                throw CheckFailure(message: "系统音频预览末端与内容时间线不一致。")
            }
            diagnostics.audioStartItemUs = startObserved
            diagnostics.audioEndItemUs = endObserved
            diagnostics.audioEndpointMappingConfirmed = true
        }
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
        if hasVideo, token == generation { generateTimelineThumbnails(asset: asset, token: token) }
    }

    private func generateTimelineThumbnails(asset: AVAsset, token: UUID) {
        let generator = AVAssetImageGenerator(asset: asset)
        generator.appliesPreferredTrackTransform = true
        generator.maximumSize = CGSize(width: 240, height: 135)
        let offset = timelineOffsetUs, duration = durationUs
        timelineGenerator = generator
        imageRequests += 1
        Task { @MainActor [weak self] in
            guard let self else { return }
            defer {
                self.imageRequests -= 1
                if self.generation == token { self.timelineGenerator = nil }
            }
            var images: [CGImage] = []
            for index in 0..<8 {
                guard self.generation == token else { return }
                let time = offset + (duration / 16) * Int64(index * 2 + 1)
                guard let frame = try? await self.generateImage(generator, at: CMTime(value: time, timescale: 1_000_000)),
                      self.generation == token else { return }
                images.append(frame.image)
            }
            guard self.generation == token else { return }
            self.timelineThumbnails = images
            self.diagnostics.timelineThumbnailCount = images.count
        }
    }

    private func normalizedPosition(_ itemUs: Int64) -> Int64 {
        if itemUs <= timelineOffsetUs { return 0 }
        if itemUs >= endItemUs { return durationUs }
        return itemUs - timelineOffsetUs
    }
    func shutdown() async throws {
        generation = UUID()
        let token = generation
        player.pause(); playing = false
        activeAsset?.cancelLoading()
        activeGenerator?.cancelAllCGImageGeneration()
        timelineGenerator?.cancelAllCGImageGeneration()
        let deadline = ContinuousClock.now + .seconds(15)
        while loading || imageRequests > 0 {
            guard ContinuousClock.now < deadline else { throw CheckFailure(message: "系统预览停止尚未确认，已保留文件访问；请重试安全关闭。") }
            // Cleanup can be entered by an already-cancelled caller. A fresh
            // detached sleeper still yields MainActor so load's defer can run.
            _ = await Task.detached { try? await Task.sleep(for: .milliseconds(20)) }.value
        }
        // A newer selection may have loaded while this asynchronous drain was
        // yielding. Its own clear replaced the old item; never clear that item.
        guard generation == token else { return }
        clear()
    }
    func seek(normalizedUs: Int64) async throws {
        let bounded = max(0, min(durationUs, normalizedUs))
        try await seek(normalizedTime: CMTime(value: bounded, timescale: 1_000_000), diagnosticUs: bounded)
    }
    func seekFrame(index: Int64, rateNumerator: Int64, rateDenominator: Int64, normalizedUs: Int64) async throws {
        guard index >= 0, rateNumerator > 0, rateNumerator <= Int64(Int32.max), rateDenominator > 0 else {
            throw CheckFailure(message: "Core 帧率信息不可用于逐帧定位。")
        }
        let (value, overflow) = index.multipliedReportingOverflow(by: rateDenominator)
        guard !overflow else { throw CheckFailure(message: "预览帧位置超出范围。") }
        let exact = normalizedUs >= durationUs ? CMTime(value: durationUs, timescale: 1_000_000)
            : CMTime(value: value, timescale: Int32(rateNumerator))
        try await seek(normalizedTime: exact, diagnosticUs: normalizedUs)
    }
    private func seek(normalizedTime: CMTime, diagnosticUs: Int64) async throws {
        guard ready else { throw CheckFailure(message: "预览尚未就绪。") }
        let raw = CMTimeAdd(CMTime(value: timelineOffsetUs, timescale: 1_000_000), normalizedTime)
        guard raw.isNumeric else { throw CheckFailure(message: "预览时间超出范围。") }
        let token = generation
        let completed = await player.seek(to: raw, toleranceBefore: .zero, toleranceAfter: .zero)
        guard token == generation, ready else { throw CheckFailure(message: "预览定位所属媒体已关闭或切换。") }
        let observed = try micros(player.currentTime())
        diagnostics.seekCompleted = completed
        diagnostics.seekNormalizedUs = diagnosticUs
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
