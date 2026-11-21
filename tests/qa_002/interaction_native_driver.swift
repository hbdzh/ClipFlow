import AppKit
import AVFoundation
import Foundation

@main struct InteractionObservation {
    @MainActor static func main() async {
        _ = NSApplication.shared
        var checks: [[String: Any]] = [], summaries: [[String: Any]] = []
        var model: WorkspaceModel?, passed = false, observeAudio = false
        func object<T: Encodable>(_ value: T) throws -> Any { try JSONSerialization.jsonObject(with: JSONEncoder().encode(value)) }
        func captureAudio(_ url: URL, startUs: Int64, durationUs: Int64, to outputURL: URL) async throws -> (Int64, Int64) {
            let result = try await Task.detached { () -> (Data, Int64, Int64) in
                let asset = AVURLAsset(url: url)
                guard let track = try await asset.loadTracks(withMediaType: .audio).first else { throw CheckFailure(message: "No audio track for independent PCM capture") }
                let reader = try AVAssetReader(asset: asset)
                let output = AVAssetReaderTrackOutput(track: track, outputSettings: [AVFormatIDKey: kAudioFormatLinearPCM, AVLinearPCMBitDepthKey: 32, AVLinearPCMIsFloatKey: true, AVLinearPCMIsBigEndianKey: false, AVLinearPCMIsNonInterleaved: false])
                let provider = reader.outputProvider(for: output)
                reader.timeRange = CMTimeRange(start: CMTime(value: startUs, timescale: 1_000_000), duration: CMTime(value: durationUs, timescale: 1_000_000))
                try reader.start()
                var data = Data(), first: Int64?, samples: Int64 = 0
                while let payload = try await provider.next() {
                    if first == nil { first = CMTimeConvertScale(payload.presentationTimeStamp, timescale: 1_000_000, method: .roundTowardZero).value }
                    let copied: (Data, Int64) = try payload.withUnsafeSampleBuffer { buffer in
                    guard let block = CMSampleBufferGetDataBuffer(buffer) else { throw CheckFailure(message: "No PCM block") }
                    let count = CMBlockBufferGetDataLength(block)
                    guard count > 0, count <= 20_000_000 else { throw CheckFailure(message: "PCM observation exceeds QA bound") }
                    var bytes = Data(count: count)
                    let status = bytes.withUnsafeMutableBytes { CMBlockBufferCopyDataBytes(block, atOffset: 0, dataLength: count, destination: $0.baseAddress!) }
                    guard status == 0 else { throw CheckFailure(message: "PCM block copy failed") }
                    return (bytes, Int64(CMSampleBufferGetNumSamples(buffer)))
                    }
                    guard data.count + copied.0.count <= 20_000_000 else { throw CheckFailure(message: "PCM observation exceeds QA bound") }
                    data.append(copied.0); samples += copied.1
                }
                guard reader.status == .completed, let first else { throw CheckFailure(message: "Independent PCM reader did not complete") }
                return (data, first, samples)
            }.value
            try result.0.write(to: outputURL, options: .withoutOverwriting)
            return (result.1, result.2)
        }
        func capture(_ preview: PreviewController, at url: URL) async throws -> Int64 {
            guard let asset = preview.player.currentItem?.asset else { throw CheckFailure(message: "No current preview item") }
            let generator = AVAssetImageGenerator(asset: asset)
            generator.appliesPreferredTrackTransform = true
            generator.requestedTimeToleranceBefore = .zero; generator.requestedTimeToleranceAfter = .zero
            let requested = preview.player.currentTime()
            let pair: (CGImage, CMTime) = try await withCheckedThrowingContinuation { continuation in
                generator.generateCGImagesAsynchronously(forTimes: [NSValue(time: requested)]) { _, image, actual, result, error in
                    guard result == .succeeded, let image else { continuation.resume(throwing: error ?? CheckFailure(message: "Frame capture failed")); return }
                    continuation.resume(returning: (image, actual))
                }
            }
            guard let data = NSBitmapImageRep(cgImage: pair.0).representation(using: .png, properties: [:]) else { throw CheckFailure(message: "PNG capture failed") }
            try data.write(to: url, options: .withoutOverwriting)
            return CMTimeConvertScale(pair.1, timescale: 1_000_000, method: .roundTowardZero).value
        }
        func check(_ ok: Bool, _ name: String, _ detail: [String: Any] = [:]) throws {
            checks.append(["name": name, "result": ok ? "PASS" : "FAIL", "detail": detail])
            if !ok && !observeAudio { throw CheckFailure(message: name) }
        }
        do {
            let args = CommandLine.arguments
            let path = args[args.firstIndex(of: "--config")! + 1]
            let config = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: path))) as! [String: Any]
            observeAudio = config["audio_observe_only"] as? Bool == true
            let work = URL(fileURLWithPath: config["work"] as! String)
            var serial = 0
            func fresh() -> WorkspaceModel {
                serial += 1
                return WorkspaceModel(historyPath: work.appendingPathComponent("history-\(serial).json"))
            }
            if config["imports"] as? Bool == true {
                let sources = (config["sources"] as! [String]).map { URL(fileURLWithPath: $0) }
                let extra = URL(fileURLWithPath: config["extra"] as! String)
                let m = fresh(); model = m
                await m.importURLs(sources + [extra])
                try check(m.assets.isEmpty && m.issue?.contains("100") == true && m.accessDiagnostics.heldLeases == 0, "101-atomic-preflight-refusal")
                await m.importURLs([sources[0], sources[0], URL(fileURLWithPath: config["hardlink"] as! String), URL(fileURLWithPath: config["symlink"] as! String)])
                try check(m.assets.count == 1 && m.assets[0].state == "succeeded", "identity-dedup-url-hardlink-symlink", ["access": try object(m.accessDiagnostics)])
                let id = m.assets[0].id
                await m.importURLs([extra, sources[0].deletingLastPathComponent()])
                try check(m.assets.count == 1 && m.assets[0].id == id && m.accessDiagnostics.heldLeases == 1, "mixed-directory-batch-atomic-refusal")
                await m.importURLs([URL(string: "https://example.invalid/private.mp4")!])
                try check(m.assets.count == 1 && m.assets[0].id == id && m.accessDiagnostics.heldLeases == 1, "network-url-refused-without-reading")
                try await m.shutdown(); try check(m.accessDiagnostics.heldLeases == 0, "rejected-input-access-balanced")
                let hundred = fresh(); model = hundred
                await hundred.importURLs(sources)
                try check(hundred.assets.count == 100 && hundred.assets.allSatisfy { $0.state == "succeeded" && $0.info != nil }, "100-real-core-probes", ["states": hundred.assets.map(\.state)])
                let ids = hundred.assets.map(\.id)
                await hundred.importURLs([extra])
                try check(hundred.assets.map(\.id) == ids && hundred.issue?.contains("100") == true && hundred.accessDiagnostics.heldLeases == 100, "100-plus-one-no-partial-import")
                await hundred.importURLs([]); try check(hundred.assets.map(\.id) == ids, "empty-selection-preserves-assets")
                summaries.append(["imported": hundred.assets.count, "bridge": try object(await hundred.diagnostics()), "access": try object(hundred.accessDiagnostics)])
                try await hundred.shutdown(); try check(hundred.accessDiagnostics.heldLeases == 0, "100-source-accesses-released-after-close")
            }
            for path in config["videos"] as? [String] ?? [] {
                let m = fresh(); model = m
                let source = URL(fileURLWithPath: path), name = URL(fileURLWithPath: path).lastPathComponent
                await m.importURL(source)
                guard let info = m.mediaInfo else { throw CheckFailure(message: "Core did not probe video: \(m.issue ?? "")") }
                try check(PreviewFrameGrid(media: info) != nil, "Core-CFR-grid-\(name)", ["media": try object(info)])
                let grid = PreviewFrameGrid(media: info)!
                try check(m.preview.ready && m.preview.diagnostics.firstVisibleImageConfirmed, "preview-ready-\(name)", ["preview": try object(m.preview.diagnostics), "status": m.preview.status])
                var exact = true
                for index in 0...grid.frameCount {
                    let expected = index == grid.frameCount ? info.durationUs : index * grid.rateDenominator * 1_000_000 / grid.rateNumerator
                    exact = exact && grid.microseconds(at: index) == expected
                }
                try check(exact, "whole-rational-grid-\(name)", ["points": grid.frameCount + 1])
                for index: Int64 in [0, 1, 12, grid.frameCount - 1] {
                    try await m.preview.seek(normalizedUs: 0)
                    for _ in 0..<index { await m.stepPreviewFrame(direction: 1) }
                    let png = work.appendingPathComponent("\(name)-frame-\(index).png")
                    let actual = try await capture(m.preview, at: png)
                    let expected = (m.preview.diagnostics.timelineOffsetUs ?? 0) + grid.microseconds(at: index)
                    try check(abs(actual - expected) <= 1, "decoded-image-time-\(name)-\(index)", ["source": path, "image": png.path, "expected_source_frame": index, "image_actual_item_us": actual, "expected_item_us": expected])
                }
                try await m.preview.seek(normalizedUs: 0)
                for _ in 0..<12 { await m.stepPreviewFrame(direction: 1) }
                try check(m.preview.diagnostics.seekNormalizedUs == grid.microseconds(at: 12) && abs(m.preview.positionUs - grid.microseconds(at: 12)) <= 1, "12-real-frame-seeks-\(name)", ["preview": try object(m.preview.diagnostics)])
                for _ in 0..<12 { await m.stepPreviewFrame(direction: -1) }
                try check(m.preview.positionUs == 0, "frame-round-trip-no-drift-\(name)")
                await m.stepPreviewFrame(direction: -1); try check(m.preview.positionUs == 0, "frame-start-clamp-\(name)")
                for direction in [-1, 1] {
                    try await m.preview.seek(normalizedUs: 123_456)
                    let expected = grid.microseconds(at: grid.nextIndex(from: m.preview.positionUs, direction: direction))
                    await m.stepPreviewFrame(direction: direction)
                    try check(m.preview.diagnostics.seekNormalizedUs == expected, "non-grid-direction-\(direction)-\(name)")
                }
                try await m.preview.seek(normalizedUs: info.durationUs)
                await m.stepPreviewFrame(direction: 1); try check(m.preview.diagnostics.seekNormalizedUs == info.durationUs, "frame-eof-clamp-\(name)")
                await m.stepPreviewFrame(direction: -1)
                try check(m.preview.diagnostics.seekNormalizedUs == grid.microseconds(at: grid.frameCount - 1), "frame-eof-previous-\(name)")
                for text in ["-1", "NaN", "0.0000001", "9223372036854775807", "1;touch x"] {
                    m.startText = text; m.endText = timeText(info.durationUs)
                    try check(m.selectedRange == nil && !m.canExport, "invalid-range-\(name)-\(text)")
                }
                m.startText = "1.000000"; m.endText = "1.000000"
                try check(m.selectedRange == nil && !m.canExport, "zero-range-\(name)")
                m.startText = "0.000001"; m.endText = timeText(info.durationUs)
                try check(m.selectedRange?.startUs == 1 && m.selectedRange?.endUs == info.durationUs && m.canExport, "microsecond-range-recovery-\(name)")
                summaries.append(["source": path, "media": try object(info), "preview": try object(m.preview.diagnostics), "bridge": try object(await m.diagnostics())])
                try await m.shutdown(); try check(m.accessDiagnostics.heldLeases == 0, "video-preview-close-releases-source-\(name)")
            }
            if let paths = config["switch_sources"] as? [String], paths.count >= 2 {
                let m = fresh(); model = m
                await m.importURLs(paths.map { URL(fileURLWithPath: $0) })
                let a = m.assets[0].id, b = m.assets[1].id
                for _ in 0..<6 {
                    let first = Task { await m.selectAsset(id: a) }; await Task.yield()
                    let second = Task { await m.selectAsset(id: b) }
                    await first.value; await second.value
                    let url = (m.preview.player.currentItem?.asset as? AVURLAsset)?.url
                    try check(m.selectedAssetId == b && m.sourceURL == m.assets[1].url && m.mediaInfo?.durationUs == m.assets[1].info?.durationUs && url == m.assets[1].url && m.preview.ready, "switch-generation-isolation", ["player": url?.path ?? "", "preview": try object(m.preview.diagnostics)])
                }
                try await m.shutdown(); try check(m.accessDiagnostics.heldLeases == 0, "racing-preview-loads-safe-close")
            }
            for path in config["audio"] as? [String] ?? [] {
                let m = fresh(); model = m
                await m.importURL(URL(fileURLWithPath: path))
                if observeAudio {
                    let asset = AVURLAsset(url: URL(fileURLWithPath: path))
                    var rows: [[String: Any]] = []
                    for track in try await asset.loadTracks(withMediaType: .audio) {
                        for segment in try await track.load(.segments) where !segment.isEmpty {
                            let map = segment.timeMapping
                            rows.append(["source_start_seconds": CMTimeGetSeconds(map.source.start), "source_duration_seconds": CMTimeGetSeconds(map.source.duration), "target_start_seconds": CMTimeGetSeconds(map.target.start), "target_duration_seconds": CMTimeGetSeconds(map.target.duration)])
                        }
                    }
                    summaries.append(["audio": path, "media": try m.mediaInfo.map(object) ?? NSNull(), "segments": rows, "status": m.preview.status])
                }
                try check(m.mediaInfo?.isAudioOnly == true && m.preview.ready && m.preview.diagnostics.mediaKind == "audio" && !m.canExport && !m.canStepPreviewFrame && !m.canEdit, "real-audio-core-preview-no-video-export", ["source": path, "preview": try object(m.preview.diagnostics), "status": m.preview.status])
                if observeAudio && !m.preview.ready { try await m.shutdown(); continue }
                let proxyRoot = m.audioPreviewDirectory
                let playerURL = (m.preview.player.currentItem?.asset as? AVURLAsset)?.url
                let expectedSource = URL(fileURLWithPath: path).resolvingSymlinksInPath().standardizedFileURL
                try check(m.sourceURL?.path == expectedSource.path, "audio-source-identity-never-replaced-by-proxy", ["source": m.sourceURL?.absoluteString ?? "", "expected": expectedSource.absoluteString])
                if let proxyRoot {
                    try check(m.audioPreviewAccessRetained && playerURL == proxyRoot.appendingPathComponent("preview.wav") && m.accessDiagnostics.heldLeases == 2, "private-proxy-owning-original-access", ["source": path, "root": proxyRoot.path, "preview": try object(m.preview.diagnostics)])
                }
                guard let playerURL, let audioInfo = m.mediaInfo, let rate = audioInfo.streams.first?.sampleRate.flatMap(Int64.init), let channels = audioInfo.streams.first?.channels else { throw CheckFailure(message: "Missing audio capture metadata") }
                let pcmURL = work.appendingPathComponent(URL(fileURLWithPath: path).lastPathComponent + ".av.f32")
                let firstUs = m.preview.diagnostics.timelineOffsetUs ?? 0
                let captured = try await captureAudio(playerURL, startUs: firstUs, durationUs: audioInfo.durationUs, to: pcmURL)
                try check(abs(captured.0 - firstUs) <= 1 && captured.1 == (audioInfo.durationUs * rate + 500_000) / 1_000_000, "real-audio-first-pcm-and-frame-count", ["source": path, "player_url": playerURL.path, "pcm": pcmURL.path, "sample_rate": rate, "channels": channels, "frames": captured.1, "first_item_pts_us": captured.0, "expected_item_pts_us": firstUs, "content_duration_us": audioInfo.durationUs, "source_audio_origin_us": audioInfo.audioOriginUs ?? 0])
                m.preview.togglePlayback(); try await Task.sleep(for: .milliseconds(250)); m.preview.pause()
                try check(m.preview.diagnostics.playbackObserved, "real-audio-playback-observed")
                try await m.preview.seek(normalizedUs: 500_000); try check(m.preview.diagnostics.seekCompleted, "real-audio-seek")
                let duration = m.mediaInfo!.durationUs
                try await m.preview.seek(normalizedUs: duration - 1)
                try check(abs(m.preview.positionUs - (duration - 1)) <= 23 && m.preview.diagnostics.audioEndpointMappingConfirmed, "real-audio-content-eof-not-clamped", ["source": path, "preview": try object(m.preview.diagnostics)])
                try await m.preview.seek(normalizedUs: 0)
                try check(m.preview.positionUs == 0, "real-audio-content-origin-zero")
                try await m.shutdown(); try check(m.accessDiagnostics.heldLeases == 0, "real-audio-source-released")
                if let proxyRoot { try check(!FileManager.default.fileExists(atPath: proxyRoot.path), "private-proxy-deleted-after-stop-confirmation") }
            }
            for path in config["unsupported_preview"] as? [String] ?? [] {
                let m = fresh(); model = m
                await m.importURL(URL(fileURLWithPath: path))
                try check(m.mediaInfo != nil && !m.preview.ready && m.preview.status.contains("时间线") && m.issue == nil, "complex-edit-preview-degraded-core-preserved", ["source": path, "status": m.preview.status])
                try await m.shutdown()
            }
            passed = checks.allSatisfy { $0["result"] as? String == "PASS" }
        } catch { checks.append(["name": "unexpected-failure", "result": "FAIL", "error": error.localizedDescription]); try? await model?.shutdown() }
        let report: [String: Any] = ["passed": passed, "scope": "Production WorkspaceModel/CoreBridge/AVFoundation independent native driver; not GUI or sandbox file-panel grants", "checks": checks, "summaries": summaries]
        let output = try! JSONSerialization.data(withJSONObject: report, options: [.sortedKeys])
        FileHandle.standardOutput.write(output); FileHandle.standardOutput.write(Data("\n".utf8)); exit(passed ? 0 : 1)
    }
}
