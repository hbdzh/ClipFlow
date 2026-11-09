import AppKit
import Foundation
import SwiftUI

@MainActor
final class SpikeModel: ObservableObject {
    static let shared = SpikeModel()
    @Published var connection: CoreConnection?
    @Published var snapshot: JobSnapshot?
    @Published var status = "等待连接 Rust Core"
    @Published var events: [String] = []
    @Published var busy = false
    @Published var connected = false
    private let bridge = CoreBridge()
    private func record(_ text: String) { events.append(text); if events.count > 40 { events.removeFirst() } }
    func connect() async {
        guard !busy else { return }; busy = true; defer { busy = false }
        do {
            connection = try await bridge.connect(); connected = true
            status = "已连接真实 Rust dylib · 仅 mock 媒体能力"
            record("version / capabilities 调用成功，输出已由 Rust 释放")
        } catch { status = error.localizedDescription; record("连接失败：\(error.localizedDescription)") }
    }
    func submit() async {
        guard !busy, connected, snapshot == nil || ["succeeded", "failed", "cancelled"].contains(snapshot?.state ?? "") else { return }
        busy = true; defer { busy = false }
        do { snapshot = try await bridge.submit(outcome: .succeed); status = "Mock 任务已提交；尚未处理任何媒体"; record("submit → queued") }
        catch { status = error.localizedDescription; record(status) }
    }
    func poll() async {
        guard !busy, let id = snapshot?.jobId else { return }; busy = true; defer { busy = false }
        do { snapshot = try await bridge.poll(id: id); status = "Core 返回：\(snapshot?.state ?? "unknown")"; record("snapshot → \(snapshot?.state ?? "unknown")") }
        catch { status = error.localizedDescription; record(status) }
    }
    func cancel() async {
        guard !busy, let id = snapshot?.jobId else { return }; busy = true; defer { busy = false }
        do { try await bridge.cancel(id: id); status = "已请求取消，请轮询等待 Core 确认"; record("cancel OK ≠ cancelled；请轮询确认") }
        catch { status = error.localizedDescription; record(status) }
    }
    func shutdown() async throws {
        try await bridge.close(); connected = false; snapshot = nil
        status = "Core 已关闭，mock 任务已到终态"; record("destroy OK，句柄不再复用")
    }
    func disconnect() async {
        guard !busy else { return }; busy = true; defer { busy = false }
        do { try await shutdown() } catch { status = error.localizedDescription; record(status) }
    }
}

struct SpikeView: View {
    @ObservedObject var model: SpikeModel
    private let features = [("probe", "媒体探测"), ("trim_stream_copy", "快速无损裁剪"), ("accurate_reencode", "精确重编码"), ("ai_separation", "AI 音源分离"), ("mock_job", "Mock 作业")]
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack { VStack(alignment: .leading) { Text("MediaStudio").font(.largeTitle.bold()); Text("MAC-001 · SwiftUI → Rust C ABI").foregroundStyle(.secondary) }; Spacer(); Text("技术验证").font(.caption).padding(8).background(.quaternary, in: Capsule()) }
            Text("真实动态库调用；媒体功能尚未实现。本窗口不打开、上传或导出素材。")
                .font(.callout).foregroundStyle(.secondary)
            GroupBox("Core 会话") {
                VStack(alignment: .leading, spacing: 10) {
                    if let version = model.connection?.version {
                        LabeledContent("版本", value: version.coreVersion)
                        LabeledContent("ABI / Schema", value: "\(version.abiVersion) / \(version.schemaVersion)")
                        LabeledContent("阶段", value: version.stage)
                        LabeledContent("平台", value: model.connection?.capabilities.platform ?? "未知")
                    } else { Text("尚无版本/能力响应").foregroundStyle(.secondary) }
                    HStack { Button("连接 / 刷新 Core") { Task { await model.connect() } }.disabled(model.busy); Button("安全关闭 Core") { Task { await model.disconnect() } }.disabled(model.busy || !model.connected) }
                }.padding(8)
            }
            GroupBox("能力（来自 Core 返回值）") {
                VStack(spacing: 8) {
                    ForEach(features, id: \.0) { item in
                        HStack { Text(item.1); Spacer(); Text(model.connection?.capabilities.features[item.0]?.available == true ? "可用（仅 mock）" : "未实现 / 不可用").foregroundStyle(model.connection?.capabilities.features[item.0]?.available == true ? .green : .secondary) }
                    }
                }.padding(8)
            }
            GroupBox("Mock 作业生命周期") {
                VStack(alignment: .leading, spacing: 12) {
                    Text(model.snapshot.map { "\($0.jobId) · \($0.state)" } ?? "没有任务")
                    HStack { Button("提交 Mock 任务") { Task { await model.submit() } }.disabled(model.busy || !model.connected || model.snapshot.map { !["succeeded", "failed", "cancelled"].contains($0.state) } == true); Button("轮询状态") { Task { await model.poll() } }.disabled(model.busy || !model.connected || model.snapshot == nil); Button("请求取消") { Task { await model.cancel() } }.disabled(model.busy || !model.connected || model.snapshot.map { !["queued", "running", "cancelling"].contains($0.state) } != false) }
                    Text("queued → running → succeeded；取消需观察 cancelling → cancelled。没有真实输出文件。")
                        .font(.caption).foregroundStyle(.secondary)
                }.padding(8)
            }
            Text(model.status).accessibilityIdentifier("core-status")
            ScrollView { VStack(alignment: .leading, spacing: 5) { ForEach(Array(model.events.enumerated()), id: \.offset) { _, line in Text(line).font(.system(.caption, design: .monospaced)).frame(maxWidth: .infinity, alignment: .leading) } } }.frame(minHeight: 70, maxHeight: 130)
        }.padding(24).frame(minWidth: 660, minHeight: 700)
        .task { await model.connect() }
    }
}

@MainActor
final class SpikeDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        Task {
            do { try await SpikeModel.shared.shutdown(); sender.reply(toApplicationShouldTerminate: true) }
            catch { SpikeModel.shared.status = "关闭未确认：\(error.localizedDescription)"; sender.reply(toApplicationShouldTerminate: false) }
        }
        return .terminateLater
    }
}

struct MediaStudioSpikeApp: App {
    @NSApplicationDelegateAdaptor(SpikeDelegate.self) private var delegate
    var body: some Scene { WindowGroup("MediaStudio · MAC-001") { SpikeView(model: .shared) }.windowResizability(.contentMinSize) }
}

@main
struct EntryPoint {
    @MainActor static func main() async {
        if CommandLine.arguments.contains("--self-test") {
            do {
                let report = try await runSelfTest()
                let encoder = JSONEncoder(); encoder.outputFormatting = [.prettyPrinted, .sortedKeys]; encoder.keyEncodingStrategy = .convertToSnakeCase
                let data = try encoder.encode(report)
                FileHandle.standardOutput.write(data); FileHandle.standardOutput.write(Data("\n".utf8))
                exit(0)
            } catch {
                let data = Data("MAC-001 self-test failed: \(error.localizedDescription)\n".utf8)
                FileHandle.standardError.write(data); exit(1)
            }
        }
        MediaStudioSpikeApp.main()
    }
}
