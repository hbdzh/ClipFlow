import AppKit
import Foundation
import SwiftUI

struct WorkspaceView: View {
    @ObservedObject var model: WorkspaceModel
    @ObservedObject var preview: PreviewController
    @State private var showDetails = false
    @State private var showTools = false
    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 14) {
                Image(systemName: "scissors").font(.title2).foregroundStyle(.tint)
                VStack(alignment: .leading, spacing: 3) {
                    Text("MediaStudio").font(.title2.bold())
                    Text("选择片段，保留原始编码").font(.callout).foregroundStyle(.secondary)
                }
                Spacer()
                Button { model.chooseInput() } label: { Label("导入媒体", systemImage: "plus") }
                    .keyboardShortcut("o", modifiers: .command).disabled(model.active)
                    .accessibilityIdentifier("import-media")
                Button { showTools.toggle() } label: { Label("媒体组件", systemImage: "gearshape") }
                    .disabled(model.active)
            }.padding(20)
            Divider()
            ScrollView {
                HStack(alignment: .top, spacing: 18) {
                    sourcePanel.frame(width: 205)
                    VStack(alignment: .leading, spacing: 16) { previewPanel; rangePanel }.frame(maxWidth: .infinity)
                    exportPanel.frame(width: 270)
                }.padding(20)
            }
            Divider()
            HStack(alignment: .top, spacing: 12) {
                if model.active { ProgressView().controlSize(.small) }
                VStack(alignment: .leading, spacing: 5) {
                    Text(model.status).accessibilityIdentifier("workspace-status")
                    if let issue = model.issue { Text(issue).font(.callout).foregroundStyle(.orange).textSelection(.enabled).accessibilityIdentifier("workspace-error") }
                }.frame(maxWidth: .infinity, alignment: .leading)
                if ["cleanup_failed", "close_failed"].contains(model.phase) {
                    Button("重试安全关闭") { Task { try? await model.shutdown() } }
                }
                Button("任务详情") { showDetails.toggle() }
            }.padding(16)
        }
        .frame(minWidth: 1000, minHeight: 640)
        .sheet(isPresented: $showDetails) { detailsSheet }
        .sheet(isPresented: $showTools) { toolsSheet }
    }
    private var sourcePanel: some View {
        VStack(alignment: .leading, spacing: 15) {
            Text("素材").font(.headline)
            if let url = model.sourceURL {
                Label(url.lastPathComponent, systemImage: "film").font(.callout.bold()).textSelection(.enabled)
                if let info = model.mediaInfo {
                    Text("\(timeText(info.durationUs)) 秒").monospacedDigit()
                    Text(info.container).font(.caption).foregroundStyle(.secondary)
                    Divider()
                    ForEach(info.streams, id: \.index) { stream in
                        VStack(alignment: .leading, spacing: 3) {
                            Text(stream.codecType == "video" ? "视频轨道 \(stream.index)" : (stream.codecType == "audio" ? "音频轨道 \(stream.index)" : "\(stream.codecType) 轨道 \(stream.index)")).font(.caption.bold())
                            Text(stream.codecName ?? "未知编码").font(.caption).foregroundStyle(.secondary)
                            if let width = stream.width, let height = stream.height { Text("\(width) × \(height)").font(.caption) }
                        }
                    }
                } else { Text("等待媒体信息").foregroundStyle(.secondary) }
                Button("重新导入此文件") { Task { await model.importURL(url) } }.disabled(model.active)
            } else {
                Image(systemName: "film.stack").font(.system(size: 34)).foregroundStyle(.secondary).padding(.vertical, 12)
                Text("导入 MP4 或 MOV 视频。读取媒体信息后可预览和选区。").font(.callout).foregroundStyle(.secondary)
                Button("选择文件…") { model.chooseInput() }.disabled(model.active)
            }
            Divider()
            Text("保留全部受支持的音轨；当前不提供字幕、转码或 AI 分离。系统预览不等于导出能力。").font(.caption).foregroundStyle(.secondary)
        }.frame(maxWidth: .infinity, alignment: .leading)
    }
    private var previewPanel: some View {
        VStack(alignment: .leading, spacing: 10) {
            ZStack {
                RoundedRectangle(cornerRadius: 12).fill(.black)
                NativePreview(player: preview.player).clipShape(RoundedRectangle(cornerRadius: 12))
                if !preview.ready {
                    VStack(spacing: 10) { Image(systemName: "play.rectangle").font(.largeTitle); Text(preview.status).multilineTextAlignment(.center).font(.callout) }
                        .foregroundStyle(.white.opacity(0.8)).padding(24)
                }
            }.frame(minHeight: 260, idealHeight: 320, maxHeight: 400).aspectRatio(16/9, contentMode: .fit)
            HStack {
                Button { preview.togglePlayback() } label: { Label(preview.playing ? "暂停" : "播放", systemImage: preview.playing ? "pause.fill" : "play.fill") }.disabled(!preview.ready)
                Text("\(timeText(preview.positionUs)) / \(timeText(model.mediaInfo?.durationUs ?? 0)) 秒").font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                Spacer()
            }
            if let info = model.mediaInfo, info.durationUs > 0 {
                Slider(value: Binding(get: { Double(preview.positionUs) }, set: { value in
                    guard let us = Int64(exactly: value.rounded()) else { return }
                    Task { try? await preview.seek(normalizedUs: us) }
                }), in: 0...Double(info.durationUs)).disabled(!preview.ready).accessibilityLabel("预览播放位置")
            }
            Text("预览位置仅用于选区；流复制的实际起止点由关键帧决定。").font(.caption).foregroundStyle(.secondary)
        }
    }
    private var rangePanel: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("选择区间").font(.headline)
                    Spacer()
                    Button("设为入点") { model.setInPoint() }.disabled(!preview.ready || model.active)
                    Button("设为出点") { model.setOutPoint() }.disabled(!preview.ready || model.active)
                }
                HStack {
                    VStack(alignment: .leading) { Text("开始（秒）").font(.caption); TextField("开始", text: $model.startText).textFieldStyle(.roundedBorder).accessibilityIdentifier("range-start") }
                    Text("→").foregroundStyle(.secondary)
                    VStack(alignment: .leading) { Text("结束（秒）").font(.caption); TextField("结束", text: $model.endText).textFieldStyle(.roundedBorder).accessibilityIdentifier("range-end") }
                }.monospacedDigit().disabled(model.mediaInfo == nil || model.active)
                if let info = model.mediaInfo, info.durationUs > 0 {
                    Slider(value: Binding(get: { Double(parseTimeText(model.startText) ?? 0) }, set: { value in
                        if let us = Int64(exactly: value.rounded()) { model.startText = timeText(us) }
                    }), in: 0...Double(info.durationUs)).accessibilityLabel("区间入点").disabled(model.active)
                    Slider(value: Binding(get: { Double(parseTimeText(model.endText) ?? info.durationUs) }, set: { value in
                        if let us = Int64(exactly: value.rounded()) { model.endText = timeText(us) }
                    }), in: 0...Double(info.durationUs)).accessibilityLabel("区间出点").disabled(model.active)
                }
                if let issue = model.rangeIssue { Text(issue).font(.caption).foregroundStyle(.orange) }
                else if let range = model.selectedRange { Text("请求保留 \(timeText(range.endUs - range.startUs)) 秒").font(.caption).foregroundStyle(.secondary) }
            }.padding(8)
        }
    }
    private var exportPanel: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("导出").font(.headline)
            Label("快速无损 · 流复制", systemImage: "bolt.fill").font(.callout.bold())
            Text("不重新编码。实际区间可能向前、向后扩展至完整关键帧；完成后会显示实际范围。").font(.callout).foregroundStyle(.secondary)
            Text("精确裁剪需要重编码，当前未开放。").font(.caption).foregroundStyle(.secondary)
            Button { model.chooseOutput() } label: { Label("选择位置并导出…", systemImage: "square.and.arrow.up").frame(maxWidth: .infinity) }
                .buttonStyle(.borderedProminent).keyboardShortcut(.return, modifiers: .command)
                .disabled(!model.canExport).accessibilityIdentifier("export-media")
            if model.active {
                Button("取消任务") { Task { await model.cancel() } }
                    .disabled(!["probing", "exporting"].contains(model.phase) || model.snapshot?.state == "cancelling")
                    .accessibilityIdentifier("cancel-job")
            }
            Divider()
            if let snapshot = model.snapshot {
                Text(snapshot.operation == "probe" ? "探测任务" : "导出任务").font(.caption.bold())

                Text(stateLabel(snapshot.state)).font(.callout)
                if let failure = snapshot.error { Text(failure.code).font(.caption).foregroundStyle(.secondary) }
                if snapshot.state == "succeeded", let result = snapshot.result, result.fileCreated {
                    Label("输出已通过校验", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                    if let requested = result.requestedRangeUs { Text("请求：\(timeText(requested.startUs))–\(timeText(requested.endUs)) 秒").font(.caption.monospacedDigit()) }
                    if let actual = result.actualRangeUs { Text("实际：\(timeText(actual.startUs))–\(timeText(actual.endUs)) 秒").font(.caption.monospacedDigit()).accessibilityIdentifier("actual-range") }
                    if let destination = model.destinationURL { Text(destination.lastPathComponent).font(.caption).textSelection(.enabled) }
                    HStack { Button("打开结果") { model.openResult() }; Button("在 Finder 显示") { model.revealResult() } }
                    Text("已检查轨道、解码、帧/包连续性与时序；不代表已完成人工音画或所有元数据验收。").font(.caption).foregroundStyle(.secondary)
                }
            } else { Text("提交后可在这里查看任务状态、取消和结果。").font(.callout).foregroundStyle(.secondary) }
        }.frame(maxWidth: .infinity, alignment: .leading)
    }
    private func stateLabel(_ state: String) -> String {
        ["queued": "等待处理", "running": "处理中与校验中", "cancelling": "等待停止与清理", "cancelled": "已取消", "succeeded": "已完成", "failed": "未完成"][state] ?? state
    }
    private var detailsSheet: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("任务详情").font(.title2.bold())
            if let snapshot = model.snapshot {
                Text("\(snapshot.jobId) · \(snapshot.state)").font(.system(.callout, design: .monospaced))
                if let failure = snapshot.error { Text("\(failure.code)：\(failure.message)").textSelection(.enabled) }
            }
            ScrollView { VStack(alignment: .leading, spacing: 8) { ForEach(Array(model.events.enumerated()), id: \.offset) { _, event in Text(event).font(.caption.monospaced()).frame(maxWidth: .infinity, alignment: .leading) } } }
            Text("失败/取消以 Core 终态为准；清理失败不会显示已取消。").font(.caption).foregroundStyle(.secondary)
            Button("完成") { showDetails = false }.keyboardShortcut(.cancelAction)
        }.padding(24).frame(width: 620, height: 420)
    }
    private var toolsSheet: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("媒体组件").font(.title2.bold())
            Text("本地开发版本使用您已安装的 FFmpeg / ffprobe 7.x；此机只验收过 7.1.1。不会自动下载或随包分发组件。").font(.callout)
            HStack { Text("FFmpeg").frame(width: 70, alignment: .leading); TextField("绝对路径", text: $model.ffmpegPath).disabled(model.active); Button("选择…") { model.chooseTool(ffmpeg: true) } }
            HStack { Text("ffprobe").frame(width: 70, alignment: .leading); TextField("绝对路径", text: $model.ffprobePath).disabled(model.active); Button("选择…") { model.chooseTool(ffmpeg: false) } }
            Text("首次默认检查 /opt/homebrew/bin，或使用启动时的组件环境配置；修改后请重新检测。应用尚未验收沙盒或正式分发。").font(.caption).foregroundStyle(.secondary)
            if let connection = model.connection { Text("Core \(connection.version.coreVersion) · \(connection.capabilities.mediaBackend.ffmpegVersion ?? "不可用")").font(.caption) }
            HStack { Button("重新检测") { Task { await model.connect() } }.disabled(model.active); Spacer(); Button("完成") { showTools = false }.keyboardShortcut(.cancelAction) }
        }.padding(24).frame(width: 680)
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
                // Last-window close can arrive before termination. Keep a strong
                // reference to restore the actual workspace on cleanup failure.
                let window = closingWindow ?? sender.windows.first { $0.canBecomeMain }
                window?.makeKeyAndOrderFront(nil)
                sender.activate()
                let alert = NSAlert()
                alert.messageText = "关闭尚未确认"
                alert.informativeText = WorkspaceModel.shared.issue ?? error.localizedDescription
                alert.addButton(withTitle: "返回工作区")
                if let window { await alert.beginSheetModal(for: window) }
                else { alert.runModal() }
            }
        }
        return .terminateLater
    }
}
struct MediaStudioSpikeApp: App {
    @NSApplicationDelegateAdaptor(WorkspaceDelegate.self) private var delegate
    var body: some Scene {
        Window("MediaStudio", id: "workspace") { WorkspaceView(model: .shared, preview: WorkspaceModel.shared.preview) }
            .windowResizability(.contentMinSize).defaultSize(width: 1280, height: 800)
    }
}

@main
struct EntryPoint {
    @MainActor static func main() async {
        do {
            let encoder = JSONEncoder(); encoder.outputFormatting = [.prettyPrinted, .sortedKeys]; encoder.keyEncodingStrategy = .convertToSnakeCase
            if CommandLine.arguments.contains("--self-test") {
                let report = try await runSelfTest()
                FileHandle.standardOutput.write(try encoder.encode(report)); FileHandle.standardOutput.write(Data("\n".utf8)); exit(0)
            }
            if CommandLine.arguments.contains("--media-self-test") {
                let report = try await runMediaSelfTest()
                FileHandle.standardOutput.write(try encoder.encode(report)); FileHandle.standardOutput.write(Data("\n".utf8)); exit(report.passed ? 0 : 1)
            }
        } catch {
            FileHandle.standardError.write(Data("MediaStudio self-test failed: \(error.localizedDescription)\n".utf8)); exit(1)
        }
        MediaStudioSpikeApp.main()
    }
}
