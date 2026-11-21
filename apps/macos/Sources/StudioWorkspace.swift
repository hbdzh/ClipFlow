import AppKit
import AVFoundation
import SwiftUI

// The production workspace follows the structure of the approved visual prototype.
struct WorkspaceView: View {
    @ObservedObject var model: WorkspaceModel
    @ObservedObject var preview: PreviewController
    @ObservedObject private var navigation = WorkspaceNavigation.shared
    @ObservedObject private var ai = AISeparationModel.shared
    @ObservedObject private var audition = AISeparationModel.shared.audition
    @ObservedObject private var shutdown = ApplicationShutdown.shared
    @State private var visibility: NavigationSplitViewVisibility = .all
    @State private var inspector = true
    @State private var details = false
    @State private var tools = false
    @State private var tab = 0
    @State private var dropTargeted = false
    private var audio: Bool { navigation.selection == 1 }

    var body: some View {
        StudioWorkspaceLayout(visibility: $visibility, inspectorPresented: $inspector) {
            StudioSidebar(model: model, ai: ai, audio: audio)
        } content: {
            if audio { StudioAudioContent(model: ai, audition: audition) }
            else { StudioEditingContent(model: model, preview: preview) }
        } inspector: {
            StudioInspector(model: model, preview: preview, ai: ai, audio: audio, tab: $tab)
        }
        .frame(minWidth: 1050, minHeight: 680)
        .toolbar {
            ToolbarItem(placement: .principal) {
                StudioWorkspacePicker(selection: $navigation.selection)
            }
            ToolbarItemGroup(placement: .primaryAction) {
                Button { navigation.importCurrent() } label: { Label(audio ? "选择音频" : "导入媒体", systemImage: "plus") }
                    .disabled(audio ? !ai.canChooseSource : !model.canImport).accessibilityIdentifier("import-media")
                Button { tools = true } label: { Label("媒体组件", systemImage: "gearshape") }
                Button { details = true } label: { Label("任务详情", systemImage: "list.bullet.rectangle") }
                Button { inspector.toggle() } label: { Label("检查器", systemImage: "sidebar.right") }
            }
        }
        .sheet(isPresented: $details) { StudioTaskSheet(model: model, ai: ai, audio: audio, presented: $details) }
        .sheet(isPresented: $tools) { StudioToolsSheet(model: model, presented: $tools) }
        .sheet(item: $model.storagePreview, onDismiss: { model.cancelStoragePreview() }) { value in
            ExportSpaceConfirmation(model: model, preview: value).interactiveDismissDisabled()
        }
        .dropDestination(for: URL.self) { urls, _ in
            guard !audio, model.canImport else { return false }
            Task { await model.importURLs(urls) }; return true
        } isTargeted: { dropTargeted = $0 }
        .overlay { if dropTargeted { Color.accentColor.opacity(0.08).allowsHitTesting(false) } }
        .background { if audio { AIAuditionKeyboard(model: ai) } else { PlaybackKeyboard(model: model, preview: preview) } }
        .task(id: "\(audio)|\(ai.resultDirectory?.absoluteString ?? "")|\(audition.waveformResetRevision)") {
            if audio, let directory = ai.resultDirectory { await audition.loadWaveforms(directory: directory) }
        }
        .task(id: "\(audio)|\(ai.sourceURL?.absoluteString ?? "")|\(ai.isRunning)|\(audition.waveformResetRevision)") {
            if audio, !ai.isRunning, let source = ai.sourceURL { await audition.loadSourceWaveform(url: source) }
        }
        .onDisappear { Task { await ai.stopAudition() } }
        .onChange(of: navigation.selection) { _, selection in
            if selection == 1 { preview.player.pause(); preview.playing = false }
            else { Task { await ai.stopAudition() } }
        }
        .safeAreaInset(edge: .bottom) {
            if let issue = shutdown.issue {
                HStack { Text(issue).foregroundStyle(.orange); Spacer(); Button("重试安全关闭") { Task { try? await shutdown.shutdown() } } }.padding(12)
            }
        }
    }
}

struct StudioSidebar: View {
    @ObservedObject var model: WorkspaceModel
    @ObservedObject var ai: AISeparationModel
    @ObservedObject private var ledger = RecentTaskLedger.shared
    let audio: Bool
    var body: some View {
        List(selection: Binding<UUID?>(
            get: { model.selectedAssetId },
            set: { id in
                guard let id, id != model.selectedAssetId else { return }
                Task { await model.selectAsset(id: id) }
            }
        )) {
            Section {
                if audio {
                    Label("音频项目", systemImage: "music.note.list")
                } else {
                    Label("我的项目", systemImage: "folder").help("当前导入的素材")
                    Label("媒体库", systemImage: "square.stack.3d.up")
                        .foregroundStyle(.secondary).help("媒体库尚未提供")
                    Label("最近使用", systemImage: "clock")
                        .foregroundStyle(.secondary).help("最近使用尚未提供")
                }
            }
            if audio {
                Section("当前音频") {
                    Button { Task { await ai.chooseSource() } } label: {
                        StudioSidebarRow(name: ai.sourceURL?.lastPathComponent ?? "选择音频…", detail: ai.sourceURL == nil ? "导入音频以开始" : ai.status, icon: "waveform", selected: ai.sourceURL != nil)
                    }.buttonStyle(.plain).disabled(!ai.canChooseSource).accessibilityIdentifier("ai-import")
                }
                Section("最近项目") {
                    ForEach(ledger.aiTasks) { item in
                        DisclosureGroup {
                            if item.resultDirectoryName != nil {
                                Button("打开人声") { ai.openHistory(item.id, stem: .vocals) }
                                Button("打开伴奏") { ai.openHistory(item.id, stem: .accompaniment) }
                                Button("在 Finder 显示") { ai.revealHistory(item.id) }
                                Button("重新选择结果文件夹…") { Task { await ai.reauthorizeHistory(item.id) } }
                            }
                        } label: { StudioSidebarRow(name: item.sourceName, detail: studioState(item.state), icon: "waveform", selected: false) }
                    }
                }
            } else {
                Section("素材") {
                    if model.assets.isEmpty {
                        Button { model.chooseInput() } label: { Label("导入媒体…", systemImage: "plus") }.disabled(!model.canImport)
                    }
                    ForEach(model.assets) { item in
                        StudioSidebarRow(name: item.url.lastPathComponent, detail: item.info.map { studioTime($0.durationUs) } ?? studioState(item.state), icon: item.info?.isAudioOnly == true ? "waveform" : "film", selected: model.selectedAssetId == item.id)
                            .tag(item.id)
                            .contextMenu {
                                Button(item.includedInBatch ? "从批处理中移除" : "加入批处理") { model.toggleBatchAsset(item.id) }
                                Button("移除素材") { Task { await model.removeAsset(id: item.id) } }
                                    .disabled(model.isImporting || model.activeExport?.draft.assetId == item.id || model.queueLeases.keys.contains(where: { model.queueRequests[$0]?.draft.assetId == item.id }))
                            }
                    }
                }
                Section("最近任务") {
                    ForEach(model.displayedTasks.prefix(5)) { item in
                        StudioRecentTaskRow(item: item, model: model)
                    }
                }
            }
        }.listStyle(.sidebar)
        .safeAreaInset(edge: .bottom) {
            HStack { Text(audio ? "音频项目" : "右键设置批处理"); Spacer(); if !audio { Text("\(model.batchCount) / \(model.assets.count)") } }
                .font(.caption).foregroundStyle(.secondary).padding(14)
        }
    }
}

private struct StudioRecentTaskRow: View {
    let item: WorkspaceTask
    @ObservedObject var model: WorkspaceModel
    private var title: String { item.state == "succeeded" ? item.destinationName : item.sourceName }

    var body: some View {
        HStack(spacing: 9) {
            Image(systemName: "film")
                .font(.system(size: 13))
                .foregroundStyle(.secondary)
                .frame(width: 30, height: 30)
                .background(Color.accentColor.opacity(0.06), in: RoundedRectangle(cornerRadius: 7))
            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.callout)
                    .lineLimit(1)
                    .truncationMode(.middle)
                HStack(spacing: 4) {
                    Text(studioState(item.state))
                    Text("·")
                    Text(item.createdAt, format: .dateTime.hour().minute())
                }
                .font(.caption2)
                .foregroundStyle(.secondary)
                .lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            if item.state == "succeeded" {
                Menu {
                    actions
                } label: {
                    Image(systemName: "ellipsis")
                        .font(.system(size: 13, weight: .medium))
                        .foregroundStyle(.secondary)
                        .frame(width: 24, height: 24)
                        .contentShape(Rectangle())
                }
                .menuStyle(.borderlessButton)
                .menuIndicator(.hidden)
                .fixedSize()
                .help("任务操作")
                .accessibilityLabel("任务操作，\(title)")
            }
        }
        .padding(.vertical, 3)
        .help("素材：" + item.sourceName + "\n输出：" + item.destinationName)
        .contextMenu { if item.state == "succeeded" { actions } }
    }

    @ViewBuilder private var actions: some View {
        Button("打开结果", systemImage: "arrow.up.forward.app") { model.openResult(taskId: item.id) }
        Button("在 Finder 显示", systemImage: "folder") { model.revealResult(taskId: item.id) }
        Divider()
        Button("重新授权…", systemImage: "key") { model.reauthorizeResult(taskId: item.id) }
    }
}

struct StudioEditingContent: View {
    @ObservedObject var model: WorkspaceModel
    @ObservedObject var preview: PreviewController
    private var geometry: VideoPreviewGeometry? {
        guard model.strategy == .transcodeStandard, model.connection?.capabilities.features["video_transform_v1"]?.available == true,
              model.mediaInfo?.core003DisplayMetadataSupported == true,
              let stream = model.mediaInfo?.streams.first(where: { $0.codecType == "video" }), stream.sampleAspectRatio == "1:1",
              let width = stream.width, let height = stream.height else { return nil }
        return VideoPreviewGeometry(sourceWidth: width, sourceHeight: height, transform: model.videoTransform)
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(model.sourceURL?.lastPathComponent ?? "开始创作").font(.title2.bold()).lineLimit(1).truncationMode(.middle)
                    Text(model.sourceURL == nil ? "导入素材后开始剪辑与转码" : "视频素材 · \(studioTime(model.mediaInfo?.durationUs ?? 0))").font(.callout).foregroundStyle(.secondary)
                }
                Spacer()
                if let info = model.mediaInfo {
                    Text(info.streams.filter { $0.codecType == "video" }.map { stream in (stream.codecName ?? "视频").uppercased() + (stream.width.map { "   \($0) × \(stream.height ?? 0)" } ?? "") }.joined(separator: " "))
                        .font(.caption).foregroundStyle(.secondary)
                }
            }.padding(.bottom, 4)
            StudioVideoStage {
                ZStack {
                    Color.black
                    if model.mediaInfo?.isAudioOnly == true {
                        Label("原音试听", systemImage: "waveform").foregroundStyle(.white)
                    } else if let geometry {
                        TransformedNativePreview(player: preview.player, geometry: geometry, transform: model.videoTransform)
                    } else { NativePreview(player: preview.player) }
                    if !preview.ready { Label(preview.status, systemImage: "play.rectangle").foregroundStyle(.white.opacity(0.7)) }
                }
            } controls: {
                HStack(spacing: 16) {
                    Button { preview.togglePlayback() } label: { Image(systemName: preview.playing ? "pause.fill" : "play.fill") }
                        .buttonStyle(.plain).disabled(!preview.ready || model.stopping || model.unconfirmedCleanup).accessibilityIdentifier("preview-play")
                    Text("\(studioTime(preview.positionUs)) / \(studioTime(model.mediaInfo?.durationUs ?? 0))").font(.caption.monospacedDigit()).accessibilityIdentifier("preview-position")
                    if let info = model.mediaInfo, info.durationUs > 0 {
                        Slider(value: Binding(get: { Double(preview.positionUs) }, set: { value in
                            guard let us = Int64(exactly: value.rounded()) else { return }; Task { try? await preview.seek(normalizedUs: us) }
                        }), in: 0...Double(info.durationUs)).tint(.white).disabled(!preview.ready).accessibilityLabel("预览播放位置")
                    } else { Spacer() }
                    Button { Task { await model.stepPreviewFrame(direction: -1) } } label: { Image(systemName: "backward.frame") }.disabled(!model.canStepPreviewFrame)
                    Button { Task { await model.stepPreviewFrame(direction: 1) } } label: { Image(systemName: "forward.frame") }.disabled(!model.canStepPreviewFrame)
                }.buttonStyle(.plain)
            }
            if model.mediaInfo?.isAudioOnly != true { timeline }
            if model.active || model.issue != nil {
                HStack { if model.active { ProgressView().controlSize(.small) }; Text(model.issue ?? model.status); Spacer(); Button("取消") { Task { await model.cancel() } }.disabled(!model.active) }
                    .font(.caption).foregroundStyle(model.issue == nil ? Color.secondary : Color.orange)
            }
        }
    }
    private var timeline: some View {
        StudioContentCard {
            HStack {
                Label("时间线", systemImage: "film").font(.headline); Spacer()
                Button { model.setInPoint() } label: { Image(systemName: "scissors") }.help("设为入点").disabled(!model.canEdit || !preview.ready)
                Button { model.setOutPoint() } label: { Image(systemName: "scissors.badge.ellipsis") }.help("设为出点").disabled(!model.canEdit || !preview.ready)
                Text(timeText(preview.positionUs)).font(.caption.monospacedDigit()).padding(.horizontal, 10).padding(.vertical, 5).background(.quaternary, in: Capsule())
            }.buttonStyle(.plain)
            if let info = model.mediaInfo, info.durationUs > 0 {
                TimelineRangeControl(duration: info.durationUs,
                    start: Binding(get: { parseTimeText(model.startText) ?? 0 }, set: { model.startText = timeText($0) }),
                    end: Binding(get: { parseTimeText(model.endText) ?? info.durationUs }, set: { model.endText = timeText($0) }),
                    position: preview.positionUs, enabled: model.canEdit, thumbnails: preview.timelineThumbnails)
                Slider(value: Binding(get: { Double(preview.positionUs) }, set: { value in
                    guard let us = Int64(exactly: value.rounded()) else { return }; Task { try? await preview.seek(normalizedUs: us) }
                }), in: 0...Double(info.durationUs)).disabled(!preview.ready).accessibilityLabel("时间线播放位置")
                HStack {
                    Text("入点  \(model.startText)"); Spacer()
                    if let range = model.selectedRange { Text("选中  \(studioTime(range.endUs - range.startUs))") }
                    Spacer(); Text("出点  \(model.endText)")
                }.font(.caption.monospacedDigit()).foregroundStyle(.secondary)
            } else { Text("导入视频后可定位播放位置并选择区间。").font(.callout).foregroundStyle(.secondary) }
        }.accessibilityIdentifier("editing-timeline")
    }
}

private func studioTime(_ us: Int64) -> String {
    let seconds = max(0, us / 1_000_000)
    return String(format: "%02lld:%02lld", seconds / 60, seconds % 60)
}

struct StudioInspector: View {
    @ObservedObject var model: WorkspaceModel
    @ObservedObject var preview: PreviewController
    @ObservedObject var ai: AISeparationModel
    let audio: Bool
    @Binding var tab: Int
    var body: some View {
        StudioInspectorLayout(audio: audio, tab: $tab) {
            if audio { audioSettings }
            else {
                if tab == 0 { videoSettings; Divider(); rangeSettings }
                else { outputAudioSettings }
                Divider(); outputSettings
            }
        } action: {
            Button {
                if audio { Task { await ai.start() } } else { model.chooseOutput() }
            } label: { Label(audio ? "开始分离" : "选择位置并导出…", systemImage: audio ? "waveform" : "square.and.arrow.up").frame(maxWidth: .infinity) }
                .buttonStyle(.borderedProminent).controlSize(.large)
                .disabled(audio ? !ai.canStart : !model.canExport)
                .accessibilityIdentifier(audio ? "ai-start" : "export-media")
        }
    }
    private var videoSettings: some View {
        StudioTransformControls(rotation: $model.videoTransform.rotationDegrees,
                                scale: $model.videoTransform.scalePercent,
                                offsetX: $model.videoTransform.offsetX,
                                offsetY: $model.videoTransform.offsetY,
                                controlsEnabled: model.canEdit && model.strategy == .transcodeStandard && model.connection?.capabilities.features["video_transform_v1"]?.available == true,
                                resetEnabled: model.canEdit && !model.videoTransform.isIdentity,
                                showModeWarning: !model.videoTransform.isIdentity && model.strategy != .transcodeStandard) {
            model.videoTransform = .identity
        }
    }
    private var rangeSettings: some View {
        StudioTrimControls(start: $model.startText, end: $model.endText,
                           editingEnabled: model.canEdit, pointsEnabled: preview.ready && model.canEdit,
                           issue: model.rangeIssue, setInPoint: model.setInPoint, setOutPoint: model.setOutPoint)
    }
    private var outputAudioSettings: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("音频设置").font(.headline)
            if model.strategy.transcodePreset != nil {
                Picker("输出音轨", selection: $model.audioSelection) {
                    Text("保留全部支持音轨").tag(AudioSelection.all); Text("无音轨").tag(AudioSelection.none)
                    ForEach(model.mediaInfo?.streams.filter { $0.codecType == "audio" } ?? [], id: \.index) { stream in
                        Text("轨道 \(stream.index) · \(stream.language ?? "未标语言")").tag(AudioSelection.one(index: stream.index))
                    }
                }.accessibilityIdentifier("audio-selection")
            } else { Text("转码模式可选择输出音轨；剪辑模式保留全部支持的音轨。 ").font(.callout).foregroundStyle(.secondary) }
        }
    }
    private var outputSettings: some View {
        StudioExportControls(selection: $model.strategy,
                             fast: .streamCopyV1, accurate: .accurateReencode,
                             standard: .transcodeStandard, compact: .transcode720p,
                             editingEnabled: model.canEdit, isReencoded: model.strategy.isReencoded) {
            Button("批量导出 \(model.batchCount) 份素材…") { model.chooseBatchOutput() }
                .disabled(!model.canSubmitBatch).accessibilityIdentifier("batch-export")
            if model.isSubmitting { Button("取消本批提交") { Task { await model.cancelQueue() } } }
            if model.pendingQueue != nil && !model.isSubmitting && !model.unconfirmedCleanup {
                Button("重新读取提交回执") { Task { await model.resolveQueueSubmission() } }
            }
        }
    }
    private var audioSettings: some View {
        StudioSeparationSettings(modelName: "人声与伴奏 · UMX-HQ") {
                Text(ai.componentStatus).font(.caption).foregroundStyle(ai.configurationReady ? Color.secondary : Color.orange)
                Button("导入模型数据…") { Task { await ai.configure() } }.disabled(!ai.canChooseSource).accessibilityIdentifier("ai-configure")
                Link("模型来源与许可", destination: URL(string: "https://zenodo.org/records/3370489")!).font(.caption)
                if let issue = ai.componentIssue { Text(issue).font(.caption).foregroundStyle(.orange) }
        } status: {
            if ai.isRunning { ProgressView(); Button("取消") { Task { await ai.cancel() } } }
            Text(ai.status).font(.caption).foregroundStyle(.secondary).accessibilityIdentifier("ai-status")
            if let issue = ai.issue { Text(issue).font(.caption).foregroundStyle(.orange).textSelection(.enabled) }
        }
    }
}

struct StudioAudioContent: View {
    @ObservedObject var model: AISeparationModel
    @ObservedObject var audition: StemPlaybackController
    @State private var sourceDuration = 0.0
    private var displayedDuration: Double { audition.duration > 0 ? audition.duration : sourceDuration }
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            StudioAudioHeading(title: model.sourceURL?.lastPathComponent ?? "音源分离",
                               subtitle: model.sourceURL == nil ? "选择音频后开始分离" : "人声与伴奏 · UMX-HQ")
            StudioWaveformPanel {
                HStack {
                    if displayedDuration > 0 {
                        ForEach(0..<5, id: \.self) { index in
                            if index > 0 { Spacer(minLength: 0) }
                            Text(displayedDuration < 4
                                 ? String(format: "%.1f", displayedDuration * Double(index) / 4)
                                 : studioSeconds(displayedDuration * Double(index) / 4))
                        }
                    } else {
                        Text("00:00"); Spacer()
                    }
                }
                    .font(.caption2.monospacedDigit()).foregroundStyle(.white.opacity(0.65))
                    .padding(.leading, 135).padding(.horizontal, 18).padding(.top, 18).padding(.bottom, 10)
                sourceTrack
                track(.vocals, color: .accentColor)
                track(.accompaniment, color: .mint)
            }
            HStack(spacing: 16) {
                if let source = model.sourceURL {
                    Button { Task { await audition.toggleSource(url: source) } } label: {
                        Label(audition.isSourcePlaying ? "暂停原音" : "播放原音", systemImage: audition.isSourcePlaying ? "pause.fill" : "play.fill")
                    }.disabled(audition.loading || !model.canChooseSource).accessibilityIdentifier("ai-play-source")
                }
                if let directory = model.resultDirectory {
                    Button { Task { await audition.toggleMix(directory: directory) } } label: {
                        Label(audition.isMixPlaying ? "暂停混音" : "播放混音", systemImage: audition.isMixPlaying ? "pause.fill" : "play.fill")
                    }.disabled(audition.loading || !model.canExport).accessibilityIdentifier("ai-play-mix")
                }
                Spacer()
                Text("\(studioSeconds(audition.seconds)) / \(studioSeconds(displayedDuration))").font(.callout.monospacedDigit()).foregroundStyle(.secondary)
                if audition.duration > 0 {
                    Slider(value: Binding(get: { min(audition.seconds, audition.duration) }, set: { value in Task { await audition.seek(to: value) } }), in: 0...audition.duration)
                        .frame(width: 120).disabled(audition.loading).accessibilityLabel("试听播放位置").accessibilityIdentifier("ai-seek")
                }
            }.padding(.horizontal, 10)
            results
            if let issue = audition.issue ?? audition.waveformIssue ?? audition.sourceWaveformIssue {
                Text(issue).font(.caption).foregroundStyle(.orange)
            }
        }
        .task(id: model.sourceURL) {
            sourceDuration = 0
            guard let url = model.sourceURL else { return }
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            let value = try? await AVURLAsset(url: url).load(.duration).seconds
            guard !Task.isCancelled, model.sourceURL == url, let value, value.isFinite, value > 0 else { return }
            sourceDuration = value
        }
    }
    private var sourceTrack: some View {
        HStack(spacing: 0) {
            Label("原始音频", systemImage: "waveform").font(.callout.weight(.medium)).foregroundStyle(.white)
                .frame(width: 110, alignment: .leading).padding(.leading, 18)
            StudioWaveform(peaks: audition.sourceWaveform, color: .white.opacity(0.7), progress: progress) { fraction in
                Task { await audition.seek(to: audition.duration * fraction) }
            }.padding(.trailing, 18)
        }.frame(height: 79).background(.white.opacity(0.025))
    }
    private func track(_ stem: StemPlaybackController.Stem, color: Color) -> some View {
        HStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 7) {
                Label(stem.title, systemImage: stem == .vocals ? "mic" : "music.note").font(.callout.weight(.medium)).foregroundStyle(.white)
                HStack {
                    Toggle("M", isOn: Binding(get: { audition.mutedStems.contains(stem) }, set: { audition.setMuted($0, for: stem) })).accessibilityLabel("静音\(stem.title)")
                    Toggle("S", isOn: Binding(get: { audition.soloedStems.contains(stem) }, set: { audition.setSoloed($0, for: stem) })).accessibilityLabel("独奏\(stem.title)")
                }.toggleStyle(.button).controlSize(.mini).environment(\.colorScheme, .dark).disabled(!model.canExport)
            }.frame(width: 110, alignment: .leading).padding(.leading, 18)
            StudioWaveform(peaks: audition.waveforms[stem] ?? [], color: color, progress: progress) { fraction in
                Task { await audition.seek(to: audition.duration * fraction) }
            }.padding(.trailing, 18)
        }.frame(height: 79).background(.white.opacity(0.045))
            .overlay(alignment: .bottom) { Rectangle().fill(.white.opacity(0.08)).frame(height: 1) }
    }
    private var progress: Double { audition.duration > 0 ? min(max(audition.seconds / audition.duration, 0), 1) : 0 }
    private var results: some View {
        StudioContentCard {
            HStack {
                Text("分离结果").font(.headline)
                if model.resultDirectory != nil { Label("分离完成", systemImage: "checkmark.circle.fill").font(.caption).foregroundStyle(.green) }
                Spacer()
                Button("导出两条音轨…") { Task { await model.exportResult() } }.disabled(!model.canExport).accessibilityIdentifier("ai-export")
            }
            if let directory = model.resultDirectory {
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 12) { result(.vocals, directory: directory); result(.accompaniment, directory: directory) }
                    VStack(spacing: 12) { result(.vocals, directory: directory); result(.accompaniment, directory: directory) }
                }
                HStack {
                    Button("导出混音…") { Task { await model.exportMixResult() } }.disabled(!model.canExport || audition.loading).accessibilityIdentifier("ai-export-mix")
                    if model.isMixExporting { ProgressView().controlSize(.small); Text(model.status).font(.caption); Button("取消") { Task { await model.cancel() } } }
                }
                Text("混音遵循静音和独奏设置；每轨音量为 50%。").font(.caption).foregroundStyle(.secondary)
            } else { Text("分离完成后可试听并导出结果。").font(.callout).foregroundStyle(.secondary) }
        }
    }
    private func result(_ stem: StemPlaybackController.Stem, directory: URL) -> some View {
        Button { Task { await audition.toggle(stem, directory: directory) } } label: {
            HStack(spacing: 12) {
                Image(systemName: stem == .vocals ? "mic" : "music.note").font(.title3).foregroundStyle(stem == .vocals ? Color.accentColor : .mint)
                    .frame(width: 42, height: 42).background((stem == .vocals ? Color.accentColor : .mint).opacity(0.12), in: RoundedRectangle(cornerRadius: 9))
                VStack(alignment: .leading, spacing: 4) { Text(stem.title).font(.callout.weight(.semibold)); Text("WAV · 44.1 kHz").font(.caption).foregroundStyle(.secondary) }
                Spacer(); Image(systemName: audition.playing == stem ? "pause.circle.fill" : "play.circle.fill").font(.title2).foregroundStyle(.secondary)
            }.padding(12).frame(maxWidth: .infinity).background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 9))
        }.buttonStyle(.plain).disabled(audition.loading || !model.canExport)
            .accessibilityIdentifier(stem == .vocals ? "ai-play-vocals" : "ai-play-accompaniment")
    }
}

struct StudioWaveform: View {
    let peaks: [Float]; let color: Color; let progress: Double; let seek: (Double) -> Void
    var body: some View {
        GeometryReader { geometry in
            Canvas { context, size in
                var path = Path()
                for (index, peak) in peaks.enumerated() {
                    let x = (CGFloat(index) + 0.5) * size.width / CGFloat(max(peaks.count, 1))
                    let h = max(1, CGFloat(min(max(peak, 0), 1)) * size.height * 0.48)
                    path.move(to: CGPoint(x: x, y: size.height / 2 - h)); path.addLine(to: CGPoint(x: x, y: size.height / 2 + h))
                }
                context.stroke(path, with: .color(color), lineWidth: max(0.5, min(2, size.width / CGFloat(max(peaks.count, 1)) * 0.65)))
                if !peaks.isEmpty {
                    var line = Path(); let x = size.width * progress
                    line.move(to: CGPoint(x: x, y: 0)); line.addLine(to: CGPoint(x: x, y: size.height))
                    context.stroke(line, with: .color(.white.opacity(0.8)), lineWidth: 1)
                }
            }.contentShape(Rectangle()).gesture(DragGesture(minimumDistance: 0).onEnded { value in
                guard !peaks.isEmpty, geometry.size.width > 0 else { return }; seek(min(max(value.location.x / geometry.size.width, 0), 1))
            })
            if peaks.isEmpty { Text("暂无波形").font(.caption).foregroundStyle(.white.opacity(0.45)).frame(maxWidth: .infinity, maxHeight: .infinity) }
        }.frame(height: 66)
    }
}
private func studioSeconds(_ value: Double) -> String {
    guard value.isFinite, value >= 0 else { return "00:00" }
    let seconds = Int(value); return String(format: "%02d:%02d", seconds / 60, seconds % 60)
}

struct StudioTaskSheet: View {
    @ObservedObject var model: WorkspaceModel
    @ObservedObject var ai: AISeparationModel
    let audio: Bool
    @Binding var presented: Bool
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack { Text("任务详情").font(.title2.bold()); Spacer(); Button("完成") { presented = false }.keyboardShortcut(.cancelAction) }
            if audio {
                Text(ai.status).font(.callout)
                if let issue = ai.issue { Text(issue).foregroundStyle(.orange).textSelection(.enabled) }
                if ai.isRunning { Button("取消任务") { Task { await ai.cancel() } } }
            } else {
                HStack { Text(model.status).font(.callout); Spacer(); Button("清除记录") { model.clearHistory() }.disabled(!model.tasks.contains(where: { $0.terminal })) }
                if let issue = model.issue { Text(issue).foregroundStyle(.orange).textSelection(.enabled) }
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 10) {
                        if model.displayedTasks.isEmpty { Label("暂无进行中的任务", systemImage: "checkmark.circle").foregroundStyle(.secondary) }
                        ForEach(model.displayedTasks) { item in
                            VStack(alignment: .leading, spacing: 7) {
                                HStack { Text(item.sourceName).font(.headline); Spacer(); Text(studioState(item.state)).font(.caption).foregroundStyle(.secondary) }
                                Text("\(item.mode.title) · 请求 \(timeText(item.requestedRange.startUs))–\(timeText(item.requestedRange.endUs)) 秒").font(.caption)
                                if let actual = item.actualRange { Text("实际 \(timeText(actual.startUs))–\(timeText(actual.endUs)) 秒").font(.caption.monospacedDigit()) }
                                if let error = item.failure { Text(model.friendlyError(error)).font(.caption).foregroundStyle(.orange) }
                                HStack {
                                    if let order = model.queueSnapshot?.queuedJobIds.firstIndex(of: item.coreJobId ?? "") {
                                        Button("上移") { Task { await model.moveQueuedTask(item.id, offset: -1) } }.disabled(order == 0)
                                        Button("下移") { Task { await model.moveQueuedTask(item.id, offset: 1) } }.disabled(order + 1 == model.queuedTasks.count)
                                    }
                                    if !item.terminal { Button("取消任务") { Task { await model.cancelQueue(taskID: item.id) } }.disabled(item.state == "cancelling") }
                                    if ["failed", "cancelled"].contains(item.state), item.sessionId == model.queueSessionId {
                                        Button("重试并另存为…") { model.chooseRetryOutput(item.id) }.disabled(model.isSubmitting || model.pendingQueue != nil || model.stopping || model.unconfirmedCleanup)
                                    }
                                    if item.state == "succeeded" {
                                        Button("打开") { model.openResult(taskId: item.id) }
                                        Button("在 Finder 显示") { model.revealResult(taskId: item.id) }
                                        Button("重新授权…") { model.reauthorizeResult(taskId: item.id) }
                                    }
                                }.controlSize(.small)
                            }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
                                .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 9))
                        }
                    }
                }
            }
            Spacer(minLength: 0)
        }.padding(24).frame(width: 600, height: 480)
    }
}
struct StudioToolsSheet: View {
    @ObservedObject var model: WorkspaceModel
    @Binding var presented: Bool
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack { Text("媒体组件").font(.title2.bold()); Spacer(); Button("完成") { presented = false }.keyboardShortcut(.cancelAction) }
            Text("选择媒体组件并重新检测可用能力。 ").font(.callout).foregroundStyle(.secondary)
            Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 12) {
                GridRow { Text("FFmpeg"); TextField("路径", text: $model.ffmpegPath).disabled(model.active); Button("选择…") { model.chooseTool(ffmpeg: true) }.disabled(model.active) }
                GridRow { Text("ffprobe"); TextField("路径", text: $model.ffprobePath).disabled(model.active); Button("选择…") { model.chooseTool(ffmpeg: false) }.disabled(model.active) }
            }
            if let connection = model.connection { Text("Core \(connection.version.coreVersion) · \(connection.capabilities.mediaBackend.ffmpegVersion ?? "不可用")").font(.caption).foregroundStyle(.secondary) }
            HStack { Button("重新检测") { Task { await model.connect() } }.disabled(model.active); Button("恢复包内组件") { Task { await model.restoreBundledTools() } }.disabled(model.active) }
        }.padding(24).frame(width: 620)
    }
}

private func studioState(_ state: String) -> String {
    ["waiting": "待探测", "probing": "探测中", "queued": "等待处理", "running": "处理中",
     "cancelling": "正在取消", "cancelled": "已取消", "succeeded": "已完成", "failed": "未完成",
     "interrupted": "上次会话已中断", "unconfirmed": "结果未确认"][state] ?? state
}
