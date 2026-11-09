import AppKit
import Foundation
import SwiftUI

private func stateLabel(_ state: String) -> String {
    ["waiting": "待探测", "probing": "探测中", "queued": "等待处理", "running": "处理与校验中", "cancelling": "等待停止与清理", "cancelled": "已取消", "succeeded": "已完成", "failed": "未完成", "interrupted": "上次会话已中断", "unconfirmed": "结果未确认"][state] ?? state
}
struct WorkspaceView: View {
    @ObservedObject var model: WorkspaceModel
    @ObservedObject var preview: PreviewController
    @State private var showDetails = false
    @State private var showTools = false
    @State private var dropTargeted = false
    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 14) {
                Image(systemName: "scissors").font(.title2).foregroundStyle(.tint)
                VStack(alignment: .leading, spacing: 3) {
                    Text("MediaStudio").font(.title2.bold())
                    Text("macOS 剪辑与转码工作区").font(.callout).foregroundStyle(.secondary)
                }
                Spacer()
                Button { model.chooseInput() } label: { Label("导入媒体", systemImage: "plus") }
                    .disabled(!model.canImport).accessibilityIdentifier("import-media")
                Button { showTools.toggle() } label: { Label("媒体组件", systemImage: "gearshape") }
            }.padding(18)
            Divider()
            HSplitView {
                sourcePanel.frame(minWidth: 190, idealWidth: 220, maxWidth: 290)
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) { previewPanel; rangePanel; exportPanel }.padding(18)
                }.frame(minWidth: 460, maxWidth: .infinity)
                taskPanel.frame(minWidth: 260, idealWidth: 300, maxWidth: 380)
            }
            Divider()
            HStack(alignment: .top, spacing: 12) {
                if model.active { ProgressView().controlSize(.small) }
                VStack(alignment: .leading, spacing: 4) {
                    Text(model.status).accessibilityIdentifier("workspace-status")
                    if let issue = model.issue { Text(issue).font(.callout).foregroundStyle(.orange).textSelection(.enabled).accessibilityIdentifier("workspace-error") }
                }.frame(maxWidth: .infinity, alignment: .leading)
                if ["cleanup_failed", "close_failed"].contains(model.phase) {
                    Button("重试安全关闭") { Task { try? await model.shutdown() } }
                }
                Button("任务详情") { showDetails.toggle() }
            }.padding(14)
        }
        .frame(minWidth: 1000, minHeight: 640)
        .background(dropTargeted ? Color.accentColor.opacity(0.08) : Color.clear)
        .dropDestination(for: URL.self) { urls, _ in
            guard model.canImport else { return false }
            Task { await model.importURLs(urls) }; return true
        } isTargeted: { dropTargeted = $0 }
        .sheet(isPresented: $showDetails) { detailsSheet }
        .sheet(isPresented: $showTools) { toolsSheet }
        .background(PlaybackKeyboard(preview: preview))
    }
    private var sourcePanel: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack { Text("素材").font(.headline); Spacer(); Text("\(model.assets.count) / 100").font(.caption).foregroundStyle(.secondary) }
            if model.assets.isEmpty {
                ContentUnavailableView { Label("导入媒体", systemImage: "film.stack") } description: { Text("选择多个文件，或拖放到窗口。") }
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 5) {
                        ForEach(model.assets) { item in
                            HStack(alignment: .top, spacing: 4) {
                                Toggle("加入批处理", isOn: Binding(get: { item.includedInBatch }, set: { _ in model.toggleBatchAsset(item.id) }))
                                    .labelsHidden().toggleStyle(.checkbox).accessibilityLabel("批处理素材 \(item.url.lastPathComponent)")
                                    .padding(.top, 10)
                                Button { Task { await model.selectAsset(id: item.id) } } label: {
                                    HStack(alignment: .top, spacing: 8) {
                                        Image(systemName: "film")
                                        VStack(alignment: .leading, spacing: 4) {
                                            Text(item.url.lastPathComponent).font(.callout.weight(.medium)).lineLimit(2)
                                            Text(item.info.map { timeText($0.durationUs) + " 秒" } ?? stateLabel(item.state)).font(.caption).foregroundStyle(.secondary)
                                        }
                                        Spacer(minLength: 0)
                                    }.padding(9).frame(maxWidth: .infinity, alignment: .leading)
                                        .background(model.selectedAssetId == item.id ? Color.accentColor.opacity(0.16) : Color.clear, in: RoundedRectangle(cornerRadius: 8))
                                        .contentShape(Rectangle())
                                }.buttonStyle(.plain).accessibilityLabel("选择素材 \(item.url.lastPathComponent)，\(stateLabel(item.state))")
                                    .accessibilityAddTraits(model.selectedAssetId == item.id ? .isSelected : [])
                                    .contextMenu { Button("移除素材") { Task { await model.removeAsset(id: item.id) } }.disabled(model.isImporting || model.activeExport?.draft.assetId == item.id || model.queueLeases.keys.contains(where: { model.queueRequests[$0]?.draft.assetId == item.id })) }
                            }
                        }
                    }
                }
            }
            if let id = model.selectedAssetId {
                Button("移除所选素材") { Task { await model.removeAsset(id: id) } }
                    .disabled(model.isImporting || model.activeExport?.draft.assetId == id || model.queueLeases.keys.contains(where: { model.queueRequests[$0]?.draft.assetId == id })).accessibilityIdentifier("remove-asset")
            }
            Divider()
            Text("每份素材保留独立区间、预设和音轨。勾选素材后可批量提交，Core 依次处理。").font(.caption).foregroundStyle(.secondary)
            Text("AI 音轨分离将在后续版本提供。").font(.caption).foregroundStyle(.secondary)
        }.padding(14)
    }
    private var previewPanel: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(model.sourceURL?.lastPathComponent ?? "预览").font(.headline).lineLimit(1).help(model.sourceURL?.lastPathComponent ?? "导入媒体后预览")
            ZStack {
                RoundedRectangle(cornerRadius: 10).fill(.black)
                NativePreview(player: preview.player).clipShape(RoundedRectangle(cornerRadius: 10))
                if !preview.ready {
                    VStack(spacing: 10) { Image(systemName: "play.rectangle").font(.largeTitle); Text(preview.status).multilineTextAlignment(.center).font(.callout) }
                        .foregroundStyle(.white.opacity(0.8)).padding(24)
                }
            }.aspectRatio(16/9, contentMode: .fit).frame(minHeight: 200, maxHeight: 410)
            HStack {
                Button { preview.togglePlayback() } label: { Label(preview.playing ? "暂停" : "播放", systemImage: preview.playing ? "pause.fill" : "play.fill") }
                    .disabled(!preview.ready).help("空格播放/暂停（文本编辑时保留空格）").accessibilityIdentifier("preview-play")
                Text("\(timeText(preview.positionUs)) / \(timeText(model.mediaInfo?.durationUs ?? 0)) 秒").font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                Spacer()
            }
            if let info = model.mediaInfo, info.durationUs > 0 {
                Slider(value: Binding(get: { Double(preview.positionUs) }, set: { value in
                    guard let us = Int64(exactly: value.rounded()) else { return }; Task { try? await preview.seek(normalizedUs: us) }
                }), in: 0...Double(info.durationUs)).disabled(!preview.ready).accessibilityLabel("预览播放位置")
                Text(info.streams.map { stream in
                    "\(stream.codecType) \(stream.codecName ?? "未知")" + (stream.width.map { " · \($0)×\(stream.height ?? 0)" } ?? "")
                }.joined(separator: " / ")).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
            }
            if let info = model.mediaInfo {
                DisclosureGroup("媒体信息 · Core 探测") {
                    VStack(alignment: .leading, spacing: 5) {
                        Text("容器：\(info.container) · 首视频 PTS：\(info.videoOriginUs) µs · 帧数：\(info.videoFrameCount.map(String.init) ?? "未知")")
                        ForEach(info.streams, id: \.index) { stream in
                            Text("轨道 \(stream.index) · \(stream.codecType) · \(stream.codecName ?? "未知") · time_base \(stream.timeBase ?? "未知") · fps \(stream.avgFrameRate ?? "未知") · SAR \(stream.sampleAspectRatio ?? "未知") · \(stream.language ?? "未标语言")" + (stream.channels.map { " · \($0) 声道 / \(stream.sampleRate ?? "未知") Hz" } ?? ""))
                        }
                    }.font(.caption).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            Text("系统预览能力与 Core 导出能力分别检测；预览不可用时仍显示真实媒体信息。").font(.caption).foregroundStyle(.secondary)
        }
    }
    private var rangePanel: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 10) {
                HStack { Text("选择区间").font(.headline); Spacer(); Button("设为入点") { model.setInPoint() }.disabled(!preview.ready || !model.canEdit); Button("设为出点") { model.setOutPoint() }.disabled(!preview.ready || !model.canEdit) }
                HStack {
                    VStack(alignment: .leading) { Text("开始（秒）").font(.caption); TextField("开始", text: $model.startText).textFieldStyle(.roundedBorder).accessibilityIdentifier("range-start").accessibilityLabel("区间开始，秒") }
                    Text("→").foregroundStyle(.secondary)
                    VStack(alignment: .leading) { Text("结束（秒）").font(.caption); TextField("结束", text: $model.endText).textFieldStyle(.roundedBorder).accessibilityIdentifier("range-end").accessibilityLabel("区间结束，秒") }
                }.monospacedDigit().disabled(!model.canEdit)
                if let info = model.mediaInfo, info.durationUs > 0 {
                    Slider(value: Binding(get: { Double(parseTimeText(model.startText) ?? 0) }, set: { if let value = Int64(exactly: $0.rounded()) { model.startText = timeText(value) } }), in: 0...Double(info.durationUs)).accessibilityLabel("区间入点").disabled(!model.canEdit)
                    Slider(value: Binding(get: { Double(parseTimeText(model.endText) ?? info.durationUs) }, set: { if let value = Int64(exactly: $0.rounded()) { model.endText = timeText(value) } }), in: 0...Double(info.durationUs)).accessibilityLabel("区间出点").disabled(!model.canEdit)
                }
                if let issue = model.rangeIssue { Text(issue).font(.caption).foregroundStyle(.orange) }
                else if let range = model.selectedRange { Text("请求保留 \(timeText(range.endUs - range.startUs)) 秒").font(.caption).foregroundStyle(.secondary) }
            }.padding(6)
        }
    }
    private var exportPanel: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("导出模式").font(.headline)
            Picker("导出模式", selection: $model.strategy) {
                Text("快速 · 无损流复制").tag(ExportStrategy.streamCopyV1)
                Text("精确 · 有损重编码").tag(ExportStrategy.accurateReencode)
                Text("转码 · 标准画质").tag(ExportStrategy.transcodeStandard)
                Text("转码 · 720p 较小体积").tag(ExportStrategy.transcode720p)
            }.pickerStyle(.menu).disabled(!model.canEdit).accessibilityIdentifier("export-strategy").id(model.selectedAssetId)
            Text(model.strategy.transcodePreset != nil ? "固定 H.264 / AAC 有损转码；尺寸与音轨映射由 Core 计算，完成后显示实际结果。" : model.strategy.isReencoded ? "精确 P01：按所选区间重新编码 H.264 / AAC。画质有损，输出经 Core 校验后才交付。" : "快速 copy-v1：保留原编码，实际范围可扩展至关键帧；完成后比较请求与实际范围。")
                .font(.callout).foregroundStyle(.secondary)
            if model.connection?.capabilities.features[model.strategy.capabilityKey]?.available != true {
                Text(model.connection?.capabilities.features[model.strategy.capabilityKey]?.reason ?? "该模式当前不可用；请在“媒体组件”重新检测能力。").font(.caption).foregroundStyle(.orange)
            } else if model.mediaInfo?.core003DisplayMetadataSupported == false {
                Text("此素材的显示元数据不在当前 Core 支持范围内，导出已禁用。").font(.caption).foregroundStyle(.orange)
            }
            if model.strategy.transcodePreset != nil { audioPanel }
            HStack {
                Button { model.chooseOutput() } label: { Label("选择位置并导出…", systemImage: "square.and.arrow.up") }
                    .buttonStyle(.borderedProminent).disabled(!model.canExport).accessibilityIdentifier("export-media")
                if model.isExporting || model.isImporting {
                    Button(model.isExporting ? "取消全部导出" : "停止导入") { Task { await model.cancel() } }.disabled(model.snapshot?.state == "cancelling").accessibilityIdentifier("cancel-job")
                }
            }
            HStack {
                Button("批量导出 \(model.batchCount) 份素材…") { model.chooseBatchOutput() }.disabled(!model.canSubmitBatch).accessibilityIdentifier("batch-export")
                Text("同一队列最多 32 项，已有文件不会替换。").font(.caption).foregroundStyle(.secondary)
            }
            if model.isSubmitting { Text("正在预检本批素材与目标…").font(.caption); Button("取消本批提交") { Task { await model.cancelQueue() } } }
            if model.pendingQueue != nil && !model.isSubmitting && !model.unconfirmedCleanup { Button("重新读取提交回执") { Task { await model.resolveQueueSubmission() } } }
            if let active = model.activeExport {
                Text("正在导出：\(active.draft.sourceName) · \(timeText(active.draft.range.startUs))–\(timeText(active.draft.range.endUs)) 秒 · \(active.draft.mode.title)")
                    .font(.caption).foregroundStyle(.secondary).accessibilityIdentifier("active-export-source")
            }
        }
    }
    private var audioPanel: some View {
        Picker("输出音轨", selection: $model.audioSelection) {
            Text("保留全部支持音轨").tag(AudioSelection.all)
            Text("无音轨").tag(AudioSelection.none)
            ForEach(model.mediaInfo?.streams.filter { $0.codecType == "audio" } ?? [], id: \.index) { stream in
                Text("轨道 \(stream.index) · \(stream.language ?? "未标语言") · \(stream.channels ?? 0) 声道").tag(AudioSelection.one(index: stream.index))
            }
        }.accessibilityIdentifier("audio-selection").id(model.selectedAssetId)
    }
    private var taskPanel: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack { Text("任务与结果").font(.headline); Spacer(); Button("清除记录") { model.clearHistory() }.disabled(!model.tasks.contains(where: { $0.terminal })) }
            if let queue = model.queueSnapshot { Text("队列 \(queue.activeCount) / \(queue.queueCapacity) · 排队 \(queue.queuedJobIds.count) 项").font(.caption).accessibilityIdentifier("queue-summary") }
            Text("本机最近 100 条摘要；重启不续跑任务。").font(.caption).foregroundStyle(.secondary)
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 10) {
                    if model.tasks.isEmpty { Text("导出任务提交到 Core 后显示在这里。").font(.callout).foregroundStyle(.secondary).padding(.vertical, 16) }
                    ForEach(model.displayedTasks) { item in
                        VStack(alignment: .leading, spacing: 7) {
                            Text(item.sourceName).font(.callout.bold()).lineLimit(2)
                            HStack { Image(systemName: item.state == "succeeded" ? "checkmark.circle.fill" : "clock"); Text(stateLabel(item.state)) }.font(.caption).foregroundStyle(item.state == "succeeded" ? Color.green : Color.secondary)
                            Text("请求：" + item.mode.title).font(.caption)
                            if let audio = item.audioSelection, item.mode.transcodePreset != nil { Text(audio.mode == "none" ? "音轨：无" : audio.mode == "one" ? "音轨：来源轨道 \(audio.streamIndex ?? -1)" : "音轨：全部支持轨道").font(.caption) }
                            if let order = model.queueSnapshot?.queuedJobIds.firstIndex(of: item.coreJobId ?? "") {
                                Text("等待顺序 \(order + 1)").font(.caption.bold())
                                HStack { Button("上移") { Task { await model.moveQueuedTask(item.id, offset: -1) } }.disabled(order == 0); Button("下移") { Task { await model.moveQueuedTask(item.id, offset: 1) } }.disabled(order + 1 == model.queuedTasks.count) }.controlSize(.small)
                            }
                            if !item.terminal { Button("取消这项任务") { Task { await model.cancelQueue(taskID: item.id) } }.disabled(item.state == "cancelling") }
                            if ["failed", "cancelled"].contains(item.state), item.sessionId == model.queueSessionId { Button("重试并另存为…") { model.chooseRetryOutput(item.id) }.disabled(model.isSubmitting || model.pendingQueue != nil) }
                            if let result = item.result, let actualMode = result.exportStrategy {
                                Text("Core 结果：" + (actualMode.isReencoded ? "有损重编码" : "无损流复制") + " · " + (result.profile ?? "兼容配置")).font(.caption2).foregroundStyle(.secondary)
                            }
                            if let plan = item.result?.transcodePlan {
                                Text("输出：\(plan.canvasWidth)×\(plan.canvasHeight) · 内容 \(plan.contentWidth)×\(plan.contentHeight)").font(.caption)
                                if plan.paddingRight > 0 || plan.paddingBottom > 0 { Text("右/下黑边：\(plan.paddingRight)/\(plan.paddingBottom) 像素").font(.caption2) }
                                Text(plan.selectedSourceAudioStreamIndices.isEmpty ? "实际输出无音轨" : "实际保留来源音轨：" + plan.selectedSourceAudioStreamIndices.map(String.init).joined(separator: "、")).font(.caption)
                            }
                            if let media = item.result?.outputMediaInfo { Text("实际输出：\(media.streams.filter { $0.codecType == "video" }.count) 视频轨 / \(media.streams.filter { $0.codecType == "audio" }.count) 音轨").font(.caption2) }
                            Text("请求：\(timeText(item.requestedRange.startUs))–\(timeText(item.requestedRange.endUs)) 秒").font(.caption.monospacedDigit())
                            if let actual = item.actualRange { Text("实际：\(timeText(actual.startUs))–\(timeText(actual.endUs)) 秒").font(.caption.monospacedDigit()).accessibilityIdentifier("actual-range") }
                            Text(item.destinationName).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                            if let error = item.failure { Text("\(error.code)：\(model.friendlyError(error))").font(.caption).foregroundStyle(.orange) }
                            if item.state == "succeeded" {
                                HStack { Button("打开") { model.openResult(taskId: item.id) }; Button("在 Finder 显示") { model.revealResult(taskId: item.id) } }.controlSize(.small)
                                Button("重新授权文件…") { model.reauthorizeResult(taskId: item.id) }.controlSize(.small)
                                Text(item.result == nil ? "历史摘要；文件访问需有效授权。" : "已通过 Core 输出校验。").font(.caption2).foregroundStyle(.secondary)
                            }
                            if item.interrupted { Text("上次会话未确认完成；请重新导入并手动提交。").font(.caption).foregroundStyle(.orange) }
                        }.padding(11).frame(maxWidth: .infinity, alignment: .leading)
                            .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 9))
                            .accessibilityElement(children: .contain)
                    }
                }
            }
            Text("校验包含内容、时序与解码检查；人工音画与完整元数据验收另行记录。").font(.caption).foregroundStyle(.secondary)
        }.padding(14)
    }
    private var detailsSheet: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("任务详情").font(.title2.bold())
            if let snapshot = model.snapshot {
                Text("\(snapshot.jobId) · \(snapshot.state)").font(.system(.callout, design: .monospaced))
                if let failure = snapshot.error { Text("\(failure.code)：\(failure.message)").textSelection(.enabled) }
            }
            ScrollView { VStack(alignment: .leading, spacing: 8) { ForEach(Array(model.events.enumerated()), id: \.offset) { _, event in Text(event).font(.caption.monospaced()).frame(maxWidth: .infinity, alignment: .leading) } } }
            Text("失败/取消以 Core 终态为准；清理未确认时保留访问并阻止新任务。").font(.caption).foregroundStyle(.secondary)
            Button("完成") { showDetails = false }.keyboardShortcut(.cancelAction)
        }.padding(24).frame(width: 620, height: 420)
    }
    private var toolsSheet: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("媒体组件").font(.title2.bold())
            Text("本地开发版本使用您已安装的 FFmpeg / ffprobe 7.x；此机只验收过 7.1.1。不会自动下载或随包分发组件。").font(.callout)
            HStack { Text("FFmpeg").frame(width: 70, alignment: .leading); TextField("绝对路径", text: $model.ffmpegPath).disabled(model.active); Button("选择…") { model.chooseTool(ffmpeg: true) }.disabled(model.active) }
            HStack { Text("ffprobe").frame(width: 70, alignment: .leading); TextField("绝对路径", text: $model.ffprobePath).disabled(model.active); Button("选择…") { model.chooseTool(ffmpeg: false) }.disabled(model.active) }
            Text("组件能力由 Core 检测；修改路径后重新检测。应用尚未验收沙盒或正式分发。").font(.caption).foregroundStyle(.secondary)
            if let connection = model.connection { Text("Core \(connection.version.coreVersion) · \(connection.capabilities.mediaBackend.ffmpegVersion ?? "不可用")").font(.caption) }
            HStack { Button("重新检测") { Task { await model.connect() } }.disabled(model.active); Spacer(); Button("完成") { showTools = false }.keyboardShortcut(.cancelAction) }
        }.padding(24).frame(width: 680)
    }
}
// A local monitor leaves text editing and accessibility keyboard navigation with AppKit.
private struct PlaybackKeyboard: NSViewRepresentable {
    let preview: PreviewController
    func makeCoordinator() -> Coordinator { Coordinator(preview) }
    func makeNSView(context: Context) -> NSView { context.coordinator.install(); return NSView() }
    func updateNSView(_ view: NSView, context: Context) {}
    static func dismantleNSView(_ view: NSView, coordinator: Coordinator) { coordinator.remove() }
    @MainActor final class Coordinator {
        private let preview: PreviewController
        private var monitor: Any?
        init(_ preview: PreviewController) { self.preview = preview }
        func install() {
            monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
                let handled = MainActor.assumeIsolated {
                    guard let self, event.keyCode == 49, event.modifierFlags.intersection([.command, .control, .option]).isEmpty,
                          !(NSApp.keyWindow?.firstResponder is NSTextView), self.preview.ready else { return false }
                    self.preview.togglePlayback(); return true
                }
                return handled ? nil : event
            }
        }
        func remove() { if let monitor { NSEvent.removeMonitor(monitor) }; monitor = nil }
    }
}
@MainActor
final class WorkspaceDelegate: NSObject, NSApplicationDelegate {
    private var closingWindow: NSWindow?
    func applicationDidFinishLaunching(_ notification: Notification) {
        NotificationCenter.default.addObserver(self, selector: #selector(windowWillClose(_:)), name: NSWindow.willCloseNotification, object: nil)
    }
    @objc private func windowWillClose(_ notification: Notification) {
        if let window = notification.object as? NSWindow, window.title == "MediaStudio" { closingWindow = window }
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        Task {
            do { try await WorkspaceModel.shared.shutdown(); sender.reply(toApplicationShouldTerminate: true) }
            catch {
                sender.reply(toApplicationShouldTerminate: false)
                let window = closingWindow ?? sender.windows.first { $0.canBecomeMain }
                window?.makeKeyAndOrderFront(nil); sender.activate()
                let alert = NSAlert(); alert.messageText = "关闭尚未确认"; alert.informativeText = WorkspaceModel.shared.issue ?? error.localizedDescription
                alert.addButton(withTitle: "返回工作区")
                if let window { await alert.beginSheetModal(for: window) } else { alert.runModal() }
            }
        }
        return .terminateLater
    }
}
struct MediaStudioSpikeApp: App {
    @ObservedObject private var model = WorkspaceModel.shared
    @NSApplicationDelegateAdaptor(WorkspaceDelegate.self) private var delegate
    var body: some Scene {
        Window("MediaStudio", id: "workspace") { WorkspaceView(model: .shared, preview: WorkspaceModel.shared.preview) }
            .windowResizability(.contentMinSize).defaultSize(width: 1280, height: 800)
            .commands {
                CommandGroup(after: .newItem) {
                    Button("导入媒体…") { WorkspaceModel.shared.chooseInput() }.keyboardShortcut("o", modifiers: .command).disabled(!model.canImport)
                    Button("导出所选区间…") { WorkspaceModel.shared.chooseOutput() }.keyboardShortcut(.return, modifiers: .command).disabled(!model.canExport)
                }
            }
    }
}
@main struct EntryPoint {
    @MainActor static func main() async {
        do {
            let encoder = JSONEncoder(); encoder.outputFormatting = [.prettyPrinted, .sortedKeys]; encoder.keyEncodingStrategy = .convertToSnakeCase
            if CommandLine.arguments.contains("--self-test") {
                let report = try await runSelfTest(); FileHandle.standardOutput.write(try encoder.encode(report)); FileHandle.standardOutput.write(Data("\n".utf8)); exit(0)
            }
            if CommandLine.arguments.contains("--media-self-test") {
                let report = try await runMediaSelfTest(); FileHandle.standardOutput.write(try encoder.encode(report)); FileHandle.standardOutput.write(Data("\n".utf8)); exit(report.passed ? 0 : 1)
            }
            if CommandLine.arguments.contains("--workspace-self-test") || CommandLine.arguments.contains("--queue-self-test") {
                let workspaceEncoder = JSONEncoder(); workspaceEncoder.outputFormatting = [.prettyPrinted, .sortedKeys]
                let report = try await runWorkspaceSelfTest(); FileHandle.standardOutput.write(try workspaceEncoder.encode(report)); FileHandle.standardOutput.write(Data("\n".utf8)); exit(report.passed ? 0 : 1)
            }
        } catch { FileHandle.standardError.write(Data("MediaStudio self-test failed: \(error.localizedDescription)\n".utf8)); exit(1) }
        MediaStudioSpikeApp.main()
    }
}
