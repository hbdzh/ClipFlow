import AppKit
import Foundation
import SwiftUI
import UniformTypeIdentifiers

func timeText(_ us: Int64) -> String {
    let magnitude = us.magnitude
    let fraction = String(magnitude % 1_000_000)
    return "\(us < 0 ? "-" : "")\(magnitude / 1_000_000).\(String(repeating: "0", count: 6 - fraction.count))\(fraction)"
}
func parseTimeText(_ text: String) -> Int64? {
    let parts = text.trimmingCharacters(in: .whitespaces).split(separator: ".", omittingEmptySubsequences: false)
    guard parts.count <= 2, let whole = parts.first, !whole.isEmpty,
          whole.utf8.allSatisfy({ (48...57).contains($0) }), let seconds = Int64(whole) else { return nil }
    let fraction = parts.count == 2 ? String(parts[1]) : ""
    guard fraction.count <= 6, fraction.utf8.allSatisfy({ (48...57).contains($0) }) else { return nil }
    let (base, overflow) = seconds.multipliedReportingOverflow(by: 1_000_000)
    guard !overflow, let tail = Int64(fraction + String(repeating: "0", count: 6 - fraction.count)) else { return nil }
    let (value, additionOverflow) = base.addingReportingOverflow(tail)
    return additionOverflow ? nil : value
}

struct AccessDiagnostics: Codable, Sendable {
    var starts = 0
    var granted = 0
    var stops = 0
    var activeGranted = 0
}
private final class SecurityAccess {
    let url: URL
    private var active: Bool
    let granted: Bool
    init(_ url: URL) { self.url = url; granted = url.startAccessingSecurityScopedResource(); active = granted }
    @discardableResult func release() -> Bool {
        guard active else { return false }
        url.stopAccessingSecurityScopedResource(); active = false; return true
    }
    deinit { if active { url.stopAccessingSecurityScopedResource() } }
}

@MainActor
final class WorkspaceModel: ObservableObject {
    static let shared = WorkspaceModel()
    let preview = PreviewController()
    private let bridge = CoreBridge()
    @Published var connection: CoreConnection?
    @Published var sourceURL: URL?
    @Published var mediaInfo: MediaInfo?
    @Published var snapshot: JobSnapshot?
    @Published var status = "导入一份视频，选择区间后导出。"
    @Published var issue: String?
    @Published var phase = "idle"
    @Published var startText = "0.000000"
    @Published var endText = "0.000000"
    @Published var ffmpegPath = BackendConfiguration.hostDefault().ffmpeg
    @Published var ffprobePath = BackendConfiguration.hostDefault().ffprobe
    @Published var destinationURL: URL?
    @Published var events: [String] = []
    @Published var closed = false
    private(set) var trace: [JobSnapshot] = []
    private(set) var probeSnapshot: JobSnapshot?
    private(set) var accessDiagnostics = AccessDiagnostics()
    private var sourceAccess: SecurityAccess?
    private var outputAccess: SecurityAccess?
    private var stopping = false
    private var taskToken = UUID()
    private var currentJob: String?
    private var pendingCancel = false
    private var configured: BackendConfiguration?
    private var unconfirmedCleanup = false
    var active: Bool { ["connecting", "probing", "exporting", "closing", "cleanup_failed", "close_failed"].contains(phase) }
    var hasRunningJob: Bool { snapshot.map { !["succeeded", "failed", "cancelled"].contains($0.state) } ?? false }
    var selectedRange: MediaRange? {
        guard let mediaInfo, let start = parseTimeText(startText), let end = parseTimeText(endText),
              start >= 0, end > start, end <= mediaInfo.durationUs else { return nil }
        return MediaRange(startUs: start, endUs: end)
    }
    var canExport: Bool {
        !active && !stopping && configured == backendConfiguration && mediaInfo != nil && selectedRange != nil
        && connection?.capabilities.features["trim_stream_copy"]?.available == true
        && mediaInfo?.trimDisplayMetadataSupported != false
    }
    var rangeIssue: String? {
        mediaInfo != nil && selectedRange == nil ? "结束时间必须晚于开始时间，并在媒体时长内；最多输入 6 位小数。" : nil
    }
    var backendConfiguration: BackendConfiguration {
        let host = BackendConfiguration.hostDefault()
        return BackendConfiguration(ffmpeg: ffmpegPath, ffprobe: ffprobePath, timeoutMs: host.timeoutMs, maxOutputBytes: host.maxOutputBytes)
    }
    private func record(_ event: String) { events.append(event); if events.count > 80 { events.removeFirst() } }
    private func hold(_ url: URL) -> SecurityAccess {
        let access = SecurityAccess(url)
        accessDiagnostics.starts += 1
        if access.granted { accessDiagnostics.granted += 1; accessDiagnostics.activeGranted += 1 }
        return access
    }
    private func release(_ access: SecurityAccess?) {
        if access?.release() == true { accessDiagnostics.stops += 1; accessDiagnostics.activeGranted -= 1 }
    }
    private func observe(_ value: JobSnapshot) {
        snapshot = value
        if trace.last?.jobId != value.jobId || trace.last?.state != value.state {
            trace.append(value)
            if trace.count > 512 { trace.removeFirst() }
            record("\(value.operation) · \(value.jobId) → \(value.state)")
        }
        switch value.state {
        case "queued": status = "任务已排队，等待处理。"
        case "running": status = value.operation == "probe" ? "正在读取媒体信息…" : "正在导出并校验媒体…"
        case "cancelling": status = "已请求取消，正在等待停止与临时文件清理。"
        case "cancelled": status = "任务已取消，处理已停止并完成清理。"
        case "failed": issue = friendlyError(value.error); status = "任务未完成，请按提示重试。"
        case "succeeded": status = value.operation == "probe" ? "媒体信息已读取，可预览和选择区间。" : "导出已完成，文件已通过校验。"
        default: status = "正在读取任务状态…"
        }
    }
    private func friendlyError(_ failure: JobFailure?) -> String {
        switch failure?.code {
        case "DEPENDENCY_MISSING", "UNSUPPORTED_BACKEND": return "媒体处理组件不可用。请在“媒体组件”中选择已安装且受支持的工具，再重新导入。"
        case "INVALID_MEDIA": return "无法读取媒体信息。文件可能不完整或格式不受支持，请选择其他文件。"
        case "INVALID_INPUT": return "文件或区间无效。请检查来源、起止时间并重试。"
        case "PERMISSION_DENIED": return "无法读取媒体、运行组件或写入所选位置。请重新选择文件、组件或保存位置。"
        case "OUTPUT_EXISTS": return "目标已存在；为保护已有文件，请使用新的文件名。"
        case "UNSUPPORTED_FORMAT": return "当前快速裁剪不支持这份素材的编码、轨道或显示元数据。未删除轨道，也未转为重编码。"
        case "SEEK_NOT_EXACT", "OUTPUT_VALIDATION_FAILED": return "未能验证完整的关键帧区间，未交付可用结果。请换用其他区间或素材。"
        case "TIMEOUT": return "处理超过时限并已停止，请缩小素材或区间后重试。"
        case "RESOURCE_LIMIT": return "素材超出当前有界分析能力，请使用更小的素材。"
        case "CLEANUP_FAILED": return "处理清理尚未确认，请查看任务详情；不要把临时文件当成结果。"
        case "SOURCE_CHANGED": return "源文件在处理期间发生变化，未交付结果。请重新导入。"
        default: return "处理未完成。请查看任务详情，修正文件或保存位置后重试。"
        }
    }
    func connect() async {
        guard !active, !stopping else { return }
        phase = "connecting"; issue = nil; closed = false
        defer { if !stopping { phase = "idle" } }
        do {
            try await bridge.close(); connection = nil; configured = nil
            try await ensureConnection()
        }
        catch { issue = "媒体组件连接失败：\(error.localizedDescription)"; status = "检查组件路径后重试。" }
    }
    private func ensureConnection() async throws {
        let config = backendConfiguration
        if configured != config && configured != nil {
            try await bridge.close(); connection = nil; configured = nil
        }
        let value = try await bridge.connect(backend: config)
        guard !stopping else { return }
        connection = value; configured = config
        guard value.capabilities.features["probe"]?.available == true else {
            throw CheckFailure(message: "组件不可用，请检查已安装的 FFmpeg / ffprobe 路径与版本。")
        }
        record("Core \(value.version.coreVersion) · \(value.version.stage) 已连接")
    }
    private func waitForTerminal(_ submitted: JobSnapshot, token: UUID) async throws -> JobSnapshot {
        currentJob = submitted.jobId; observe(submitted)
        if pendingCancel { try await bridge.cancel(id: submitted.jobId) }
        while true {
            let value = try await bridge.poll(id: submitted.jobId)
            guard token == taskToken, !stopping else { return value }
            observe(value)
            if ["succeeded", "failed", "cancelled"].contains(value.state) { currentJob = nil; return value }
            try await Task.sleep(for: .milliseconds(40))
        }
    }
    private func recoverAfterBridgeFailure() async -> Bool {
        phase = "closing"
        let target = bridge
        do {
            // A caller Task may already be cancelled. Cleanup runs independently
            // so Task.sleep inside the actor can still wait for actual termination.
            try await Task.detached { try await target.close() }.value
            configured = nil; connection = nil; currentJob = nil
            return true
        } catch {
            phase = "cleanup_failed"
            issue = "清理尚未确认，已保留文件访问并阻止新任务。请重试安全关闭。"
            return false
        }
    }
    func importURL(_ url: URL) async {
        guard !active, !stopping else { return }
        phase = "probing"; issue = nil; closed = false; pendingCancel = false
        let token = UUID(); taskToken = token
        preview.clear()
        release(sourceAccess); sourceAccess = nil
        sourceURL = url; sourceAccess = hold(url)
        mediaInfo = nil; snapshot = nil; probeSnapshot = nil; destinationURL = nil
        var safeToRelease = false
        defer { if !stopping && token == taskToken && safeToRelease { phase = "idle" } }
        do {
            // No job is active when import starts. Reclaim retained history before
            // creating the new media session; Core max_jobs is not an endless log.
            try await bridge.close(); connection = nil; configured = nil
            try await ensureConnection()
            guard !stopping, token == taskToken else { return }
            let submitted = try await bridge.submitProbe(source: url)
            let done = try await waitForTerminal(submitted, token: token)
            guard !stopping, token == taskToken else { return }
            probeSnapshot = done
            safeToRelease = ["succeeded", "failed", "cancelled"].contains(done.state)
            if done.error?.code == "CLEANUP_FAILED" {
                unconfirmedCleanup = true; phase = "cleanup_failed"; safeToRelease = false
                return
            }
            guard done.state == "succeeded", let info = done.result?.mediaInfo else { return }
            mediaInfo = info; startText = "0.000000"; endText = timeText(info.durationUs)
            do { try await preview.load(url: url, media: info) }
            catch { preview.status = "当前预览不可用；媒体信息与导出能力分别判断。"; record("系统预览未就绪") }
            if info.trimDisplayMetadataSupported == false { issue = "这份媒体含旋转或明确颜色等显示元数据，当前仅可探测/预览，快速裁剪尚未支持。" }
        } catch {
            guard !stopping else { return }
            let original = error.localizedDescription
            safeToRelease = await recoverAfterBridgeFailure()
            if safeToRelease { issue = original; status = "导入未完成，请检查媒体或组件后重试。" }
        }
    }
    func export(to url: URL) async {
        guard canExport, let sourceURL, let range = selectedRange else {
            issue = rangeIssue ?? "当前媒体或组件尚不可导出。"; return
        }
        phase = "exporting"; issue = nil; pendingCancel = false; destinationURL = url
        let token = UUID(); taskToken = token
        release(outputAccess); outputAccess = hold(url.deletingLastPathComponent())
        var safeToRelease = false
        defer {
            if !stopping && token == taskToken && safeToRelease {
                phase = "idle"; release(outputAccess); outputAccess = nil
            }
        }
        do {
            let submitted = try await bridge.submitTrim(source: sourceURL, destination: url, range: range)
            let done = try await waitForTerminal(submitted, token: token)
            safeToRelease = ["succeeded", "failed", "cancelled"].contains(done.state)
            if done.error?.code == "CLEANUP_FAILED" {
                unconfirmedCleanup = true; phase = "cleanup_failed"; safeToRelease = false
            }
        } catch {
            guard !stopping else { return }
            let original = error.localizedDescription
            safeToRelease = await recoverAfterBridgeFailure()
            if safeToRelease { issue = original; status = "导出未完成，请重新检测组件后重试。" }
        }
    }
    func cancel() async {
        guard phase == "probing" || phase == "exporting", !stopping else { return }
        if currentJob == nil && mediaInfo != nil && phase == "probing" {
            do { try await preview.shutdown() }
            catch { phase = "cleanup_failed"; issue = error.localizedDescription; return }
            status = "预览准备已停止，媒体信息仍可使用。"
            return
        }
        pendingCancel = true
        guard let id = currentJob else { status = "已请求取消，等待提交确认。"; return }
        do { try await bridge.cancel(id: id); status = "已请求取消，等待处理停止并清理。" }
        catch { issue = "取消未确认：\(error.localizedDescription)" }
    }
    func shutdown() async throws {
        guard !closed else { return }
        guard !stopping else { throw BridgeError(code: -7, operation: "closing") }
        stopping = true; phase = "closing"; status = "正在停止任务并清理…"
        preview.pause()
        do {
            try await bridge.close()
            guard !unconfirmedCleanup else {
                throw CheckFailure(message: "处理已报告清理失败，不能确认临时文件已移除。请保留现场并检查任务详情；当前不能确认安全关闭。")
            }
            let closedSnapshots = await bridge.closedJobSnapshots()
            if let currentJob, let terminal = closedSnapshots.first(where: { $0.jobId == currentJob }) { observe(terminal) }
            taskToken = UUID(); currentJob = nil
            try await preview.shutdown()
            release(outputAccess); outputAccess = nil
            release(sourceAccess); sourceAccess = nil
            closed = true; phase = "closed"; connection = nil; configured = nil
            status = "任务已停止，媒体会话与文件访问已关闭。"
        } catch {
            // Keep leases and ownership alive until a later cleanup retry succeeds.
            phase = "close_failed"; issue = "关闭未确认：\(error.localizedDescription)"
            stopping = false; throw error
        }
    }
    func diagnostics() async -> BridgeDiagnostics { await bridge.diagnostics() }
    func setInPoint() { startText = timeText(preview.positionUs) }
    func setOutPoint() { endText = timeText(preview.positionUs) }
    func chooseInput() {
        guard !active, !stopping else { return }
        let panel = NSOpenPanel()
        panel.title = "导入媒体"; panel.canChooseDirectories = false; panel.allowsMultipleSelection = false
        panel.allowedContentTypes = [.movie, .mpeg4Movie, .quickTimeMovie]
        panel.begin { [weak self] response in
            guard response == .OK, let url = panel.url else { return }
            Task { @MainActor in await self?.importURL(url) }
        }
    }
    func chooseOutput() {
        guard canExport else { return }
        let panel = NSSavePanel()
        panel.title = "导出为新的 MP4"; panel.allowedContentTypes = [.mpeg4Movie]
        panel.nameFieldStringValue = (sourceURL?.deletingPathExtension().lastPathComponent ?? "视频") + "-片段.mp4"
        panel.message = "流复制将按关键帧保留完整区间。已有文件不会被替换，请选择新文件名。"
        panel.begin { [weak self] response in
            guard response == .OK, let url = panel.url else { return }
            Task { @MainActor in await self?.export(to: url) }
        }
    }
    func chooseTool(ffmpeg: Bool) {
        guard !active, !stopping else { return }
        let panel = NSOpenPanel(); panel.title = ffmpeg ? "选择 FFmpeg 可执行文件" : "选择 ffprobe 可执行文件"
        panel.canChooseDirectories = false; panel.allowsMultipleSelection = false
        panel.begin { [weak self] response in
            guard response == .OK, let url = panel.url else { return }
            Task { @MainActor in
                if ffmpeg { self?.ffmpegPath = url.path } else { self?.ffprobePath = url.path }
                await self?.connect()
            }
        }
    }
    func revealResult() {
        guard snapshot?.state == "succeeded", snapshot?.result?.fileCreated == true, let destinationURL else { return }
        NSWorkspace.shared.activateFileViewerSelecting([destinationURL])
    }
    func openResult() {
        guard snapshot?.state == "succeeded", snapshot?.result?.fileCreated == true, let destinationURL else { return }
        NSWorkspace.shared.open(destinationURL)
    }
}
