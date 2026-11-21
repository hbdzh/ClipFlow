import AVFoundation
import Combine
import Foundation

/// The model owns audition resources so cleanup can drain async AVFoundation
/// loads before deleting a result directory or releasing its access scope.
@MainActor final class StemPlaybackController: ObservableObject {
    enum Stem: String, CaseIterable { case vocals, accompaniment
        var title: String { self == .vocals ? "人声" : "伴奏" }
        var filename: String { rawValue + ".wav" }
    }
    @Published private(set) var selected = Stem.vocals
    @Published private(set) var playing: Stem?
    @Published private(set) var loading = false
    @Published private(set) var seconds = 0.0
    @Published private(set) var duration = 0.0
    @Published private(set) var issue: String?
    @Published private(set) var isMixPlaying = false
    @Published private(set) var isSourcePlaying = false
    @Published private(set) var mutedStems: Set<Stem> = []
    @Published private(set) var soloedStems: Set<Stem> = []
    @Published private(set) var waveforms: [Stem: [Float]] = [:]
    @Published private(set) var waveformLoading = false
    @Published private(set) var waveformIssue: String?
    @Published private(set) var sourceWaveform: [Float] = []
    @Published private(set) var sourceWaveformLoading = false
    @Published private(set) var sourceWaveformIssue: String?
    @Published private(set) var waveformResetRevision = 0
    private let player = AVPlayer()
    private var loadedURL: URL?
    private var activeAsset: AVURLAsset?
    private var mixAssets: [AVURLAsset] = []
    private var mixTracks: [Stem: AVCompositionTrack] = [:]
    private var mixDirectory: URL?
    private var waveformDirectory: URL?
    private var waveformTask: Task<[[Float]], Error>?
    private var sourceWaveformTask: Task<[[Float]], Error>?
    private var sourceWaveformURL: URL?
    private var loadedSourceURL: URL?
    private var sourceScopeURL: URL?
    private var sourceScopeActive = false
    private var generation = UUID()
    private var waveformGeneration = UUID()
    private var loads = 0
    private var stopping = false
    private var ticker: Task<Void, Never>?

    func toggle(_ stem: Stem, directory: URL) async {
        guard !stopping, !loading else { return }
        selected = stem
        if playing == stem { player.pause(); playing = nil; return }
        loading = true; loads += 1
        defer { loads -= 1; loading = loads > 0 }
        let token = UUID(); generation = token; issue = nil
        do {
            let url = directory.appendingPathComponent(stem.filename)
            if loadedURL != url {
                player.pause(); playing = nil; isMixPlaying = false; isSourcePlaying = false
                activeAsset?.cancelLoading(); player.replaceCurrentItem(with: nil)
                releaseSourceScope()
                mixAssets = []; mixTracks = [:]; mixDirectory = nil
                let asset = AVURLAsset(url: url); activeAsset = asset
                let playable = try await asset.load(.isPlayable)
                guard generation == token, !stopping else { return }
                guard playable else { throw CheckFailure(message: "系统播放器无法播放这条音轨。") }
                let itemDuration = try await asset.load(.duration).seconds
                guard generation == token, !stopping else { return }
                player.replaceCurrentItem(with: AVPlayerItem(asset: asset))
                loadedURL = url; duration = itemDuration.isFinite ? max(0, itemDuration) : 0
                await player.seek(to: CMTime(seconds: min(seconds, duration), preferredTimescale: 44100))
            }
            if let item = player.currentItem, item.duration.isNumeric,
               player.currentTime().seconds >= item.duration.seconds - 0.02 {
                await player.seek(to: .zero)
            }
            guard generation == token, !stopping else { return }
            player.play()
            let deadline = ContinuousClock.now + .seconds(5)
            while player.timeControlStatus != .playing {
                guard generation == token, !stopping else { return }
                guard player.currentItem?.status != .failed, ContinuousClock.now < deadline else {
                    throw CheckFailure(message: "音轨暂时无法播放，请重试。")
                }
                try await Task.sleep(for: .milliseconds(50))
            }
            playing = stem
            ticker?.cancel()
            ticker = Task { @MainActor [weak self] in
                while !Task.isCancelled {
                    do { try await Task.sleep(for: .milliseconds(200)) } catch { return }
                    guard let self, self.generation == token else { return }
                    let value = self.player.currentTime().seconds
                    if value.isFinite { self.seconds = value }
                    if self.player.timeControlStatus == .paused { self.playing = nil }
                }
            }
        } catch {
            guard generation == token else { return }
            player.pause(); playing = nil; issue = error.localizedDescription
        }
    }

    /// The two Core-validated output stems share a composition clock. Mix
    /// controls affect audition only; they do not alter the exported WAVs.
    func toggleMix(directory: URL) async {
        guard !stopping, !loading else { return }
        if isMixPlaying { player.pause(); isMixPlaying = false; return }
        loading = true; loads += 1
        defer { loads -= 1; loading = loads > 0 }
        let token = UUID(); generation = token; issue = nil
        do {
            if mixDirectory != directory || mixTracks.count != 2 {
                player.pause(); playing = nil; isMixPlaying = false; isSourcePlaying = false; loadedURL = nil
                activeAsset?.cancelLoading(); activeAsset = nil
                player.replaceCurrentItem(with: nil); releaseSourceScope()
                mixAssets = []; mixTracks = [:]; mixDirectory = nil
                let composition = AVMutableComposition()
                var ranges: [CMTimeRange] = []
                var sources: [AVAssetTrack] = []
                for stem in Stem.allCases {
                    let asset = AVURLAsset(url: directory.appendingPathComponent(stem.filename))
                    mixAssets.append(asset)
                    guard try await asset.load(.isPlayable),
                          let track = try await asset.loadTracks(withMediaType: .audio).first else {
                        throw CheckFailure(message: "系统播放器无法读取分离音轨。")
                    }
                    let range = try await track.load(.timeRange)
                    guard token == generation, !stopping else { return }
                    guard range.duration.isNumeric, CMTimeCompare(range.duration, .zero) > 0 else {
                        throw CheckFailure(message: "分离音轨时长不可用。")
                    }
                    sources.append(track); ranges.append(range)
                }
                let firstSeconds = ranges[0].duration.seconds, secondSeconds = ranges[1].duration.seconds
                guard firstSeconds.isFinite, secondSeconds.isFinite, abs(firstSeconds - secondSeconds) <= 0.001 else {
                    throw CheckFailure(message: "两条分离音轨时长不一致，无法同步试听。")
                }
                let duration = CMTimeMinimum(ranges[0].duration, ranges[1].duration)
                for (index, stem) in Stem.allCases.enumerated() {
                    guard let target = composition.addMutableTrack(withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid) else {
                        throw CheckFailure(message: "无法创建同步试听音轨。")
                    }
                    try target.insertTimeRange(CMTimeRange(start: ranges[index].start, duration: duration), of: sources[index], at: .zero)
                    mixTracks[stem] = target
                }
                guard token == generation, !stopping else { return }
                player.replaceCurrentItem(with: AVPlayerItem(asset: composition))
                mixDirectory = directory; self.duration = duration.seconds
                await player.seek(to: CMTime(seconds: min(seconds, self.duration), preferredTimescale: 44100))
            }
            updateMix()
            if let item = player.currentItem, item.duration.isNumeric,
               player.currentTime().seconds >= item.duration.seconds - 0.02 { await player.seek(to: .zero) }
            guard token == generation, !stopping else { return }
            player.play()
            let deadline = ContinuousClock.now + .seconds(5)
            while player.timeControlStatus != .playing {
                guard token == generation, !stopping else { return }
                guard player.currentItem?.status != .failed, ContinuousClock.now < deadline else {
                    throw CheckFailure(message: "同步试听暂时无法播放，请重试。")
                }
                try await Task.sleep(for: .milliseconds(50))
            }
            playing = nil; isMixPlaying = true
            ticker?.cancel()
            ticker = Task { @MainActor [weak self] in
                while !Task.isCancelled {
                    do { try await Task.sleep(for: .milliseconds(200)) } catch { return }
                    guard let self, self.generation == token else { return }
                    let value = self.player.currentTime().seconds
                    if value.isFinite { self.seconds = value }
                    if self.player.timeControlStatus == .paused { self.isMixPlaying = false }
                }
            }
        } catch {
            guard generation == token else { return }
            player.pause(); playing = nil; isMixPlaying = false; issue = error.localizedDescription
        }
    }

    /// Original-source playback owns its own security scope until the player
    /// has released the item and any asynchronous load has drained.
    func toggleSource(url: URL) async {
        guard !stopping, !loading else { return }
        if isSourcePlaying && loadedSourceURL == url {
            player.pause(); isSourcePlaying = false; return
        }
        loading = true; loads += 1
        defer { loads -= 1; loading = loads > 0 }
        let token = UUID(); generation = token; issue = nil
        do {
            if loadedSourceURL != url {
                player.pause(); playing = nil; isMixPlaying = false; isSourcePlaying = false
                activeAsset?.cancelLoading(); player.replaceCurrentItem(with: nil)
                releaseSourceScope()
                loadedURL = nil; mixDirectory = nil; mixTracks = [:]; mixAssets = []
                let scoped = url.startAccessingSecurityScopedResource()
                sourceScopeURL = url; sourceScopeActive = scoped
                let asset = AVURLAsset(url: url); activeAsset = asset
                guard try await asset.load(.isPlayable) else {
                    throw CheckFailure(message: "系统播放器无法播放原始音频。")
                }
                guard generation == token, !stopping else { return }
                let itemDuration = try await asset.load(.duration).seconds
                guard generation == token, !stopping else { return }
                player.replaceCurrentItem(with: AVPlayerItem(asset: asset))
                loadedSourceURL = url; duration = itemDuration.isFinite ? max(0, itemDuration) : 0
                await player.seek(to: CMTime(seconds: min(seconds, duration), preferredTimescale: 44100))
            }
            if let item = player.currentItem, item.duration.isNumeric,
               player.currentTime().seconds >= item.duration.seconds - 0.02 { await player.seek(to: .zero) }
            guard generation == token, !stopping else { return }
            player.play()
            let deadline = ContinuousClock.now + .seconds(5)
            while player.timeControlStatus != .playing {
                guard generation == token, !stopping else { return }
                guard player.currentItem?.status != .failed, ContinuousClock.now < deadline else {
                    throw CheckFailure(message: "原始音频暂时无法播放，请重试。")
                }
                try await Task.sleep(for: .milliseconds(50))
            }
            playing = nil; isMixPlaying = false; isSourcePlaying = true
            ticker?.cancel()
            ticker = Task { @MainActor [weak self] in
                while !Task.isCancelled {
                    do { try await Task.sleep(for: .milliseconds(200)) } catch { return }
                    guard let self, self.generation == token else { return }
                    let value = self.player.currentTime().seconds
                    if value.isFinite { self.seconds = value }
                    if self.player.timeControlStatus == .paused { self.isSourcePlaying = false }
                }
            }
        } catch {
            guard generation == token else { return }
            player.pause(); player.replaceCurrentItem(with: nil)
            activeAsset?.cancelLoading(); activeAsset = nil
            releaseSourceScope(); isSourcePlaying = false; issue = error.localizedDescription
        }
    }

    func seek(to value: Double) async {
        guard !stopping, !loading, player.currentItem != nil, duration > 0, value.isFinite else { return }
        let target = min(max(value, 0), duration)
        await player.seek(to: CMTime(seconds: target, preferredTimescale: 44100), toleranceBefore: .zero, toleranceAfter: .zero)
        let actual = player.currentTime().seconds
        if actual.isFinite { seconds = actual }
    }

    private func releaseSourceScope() {
        if sourceScopeActive { sourceScopeURL?.stopAccessingSecurityScopedResource() }
        sourceScopeURL = nil; sourceScopeActive = false; loadedSourceURL = nil
    }

    func setMuted(_ value: Bool, for stem: Stem) {
        if value { mutedStems.insert(stem) } else { mutedStems.remove(stem) }
        updateMix()
    }
    func setSoloed(_ value: Bool, for stem: Stem) {
        if value { soloedStems.insert(stem) } else { soloedStems.remove(stem) }
        updateMix()
    }
    private func updateMix() {
        guard mixDirectory != nil, let item = player.currentItem else { return }
        let mix = AVMutableAudioMix()
        mix.inputParameters = Stem.allCases.compactMap { stem in
            guard let track = mixTracks[stem] else { return nil }
            let parameter = AVMutableAudioMixInputParameters(track: track)
            let audible = !mutedStems.contains(stem) && (soloedStems.isEmpty || soloedStems.contains(stem))
            // Half gain reduces peak risk; it cannot prevent clipping for
            // over-full-scale floating-point input.
            parameter.setVolume(audible ? 0.5 : 0, at: .zero)
            return parameter
        }
        item.audioMix = mix
    }

    func loadWaveforms(directory: URL) async {
        while waveformLoading {
            guard !stopping, !Task.isCancelled else { return }
            do { try await Task.sleep(for: .milliseconds(20)) } catch { return }
        }
        guard !stopping, !Task.isCancelled else { return }
        if waveformDirectory == directory && waveforms.count == 2 { return }
        waveformLoading = true; waveformIssue = nil; waveforms = [:]; loads += 1
        let token = waveformGeneration
        let urls = Stem.allCases.map { directory.appendingPathComponent($0.filename) }
        let task = Task.detached(priority: .utility) { try StemWaveforms.read(urls: urls) }
        waveformTask = task
        defer { loads -= 1; waveformLoading = false; waveformTask = nil }
        do {
            let values = try await withTaskCancellationHandler { try await task.value } onCancel: { task.cancel() }
            guard waveformGeneration == token, !stopping, !Task.isCancelled, values.count == 2 else { return }
            waveforms = Dictionary(uniqueKeysWithValues: zip(Stem.allCases, values))
            waveformDirectory = directory
        } catch is CancellationError { return }
        catch {
            guard waveformGeneration == token, !stopping else { return }
            waveformIssue = "无法读取音轨波形：\(error.localizedDescription)"
        }
    }

    /// The source has its own security-scope lease for the whole background
    /// read. stop() cancels and drains this task before the source can change.
    func loadSourceWaveform(url: URL) async {
        while sourceWaveformLoading {
            guard !stopping, !Task.isCancelled else { return }
            do { try await Task.sleep(for: .milliseconds(20)) } catch { return }
        }
        guard !stopping, !Task.isCancelled else { return }
        if sourceWaveformURL == url && !sourceWaveform.isEmpty { return }
        sourceWaveformLoading = true; sourceWaveformIssue = nil; sourceWaveform = []; loads += 1
        let token = waveformGeneration
        let task = Task.detached(priority: .utility) {
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            return try StemWaveforms.read(urls: [url])
        }
        sourceWaveformTask = task
        defer { loads -= 1; sourceWaveformLoading = false; sourceWaveformTask = nil }
        do {
            let values = try await withTaskCancellationHandler { try await task.value } onCancel: { task.cancel() }
            guard waveformGeneration == token, !stopping, !Task.isCancelled, let values = values.first else { return }
            sourceWaveform = values; sourceWaveformURL = url
        } catch is CancellationError { return }
        catch {
            guard waveformGeneration == token, !stopping else { return }
            sourceWaveformIssue = "无法读取原音波形：\(error.localizedDescription)"
        }
    }
    func stop() async throws {
        stopping = true; generation = UUID(); waveformGeneration = UUID()
        let token = generation
        player.pause(); playing = nil; isMixPlaying = false; isSourcePlaying = false
        activeAsset?.cancelLoading(); player.replaceCurrentItem(with: nil)
        for asset in mixAssets { asset.cancelLoading() }
        waveformTask?.cancel(); sourceWaveformTask?.cancel()
        ticker?.cancel(); ticker = nil
        let deadline = ContinuousClock.now + .seconds(5)
        while loads > 0 {
            guard ContinuousClock.now < deadline else {
                throw CheckFailure(message: "试听停止尚未确认，已保留结果和文件访问。")
            }
            _ = await Task.detached { try? await Task.sleep(for: .milliseconds(20)) }.value
        }
        guard generation == token else { return }
        activeAsset = nil; releaseSourceScope(); loadedURL = nil; mixAssets = []; mixTracks = [:]; mixDirectory = nil
        waveformDirectory = nil; waveforms = [:]; waveformIssue = nil
        sourceWaveformURL = nil; sourceWaveform = []; sourceWaveformIssue = nil
        mutedStems = []; soloedStems = []; seconds = 0; duration = 0; issue = nil; stopping = false
        waveformResetRevision &+= 1
    }
}
