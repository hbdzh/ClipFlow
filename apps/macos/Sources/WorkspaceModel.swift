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
    var heldLeases = 0
}
struct ShutdownDiagnostics: Codable, Sendable {
    var cleanupAttempts = 0
    var watchdogTimeouts = 0
    var cleanupPending = false
    var cleanupCompleted = false
    var cleanupFailed = false
    var cleanupElapsedMs = 0.0
    var watchdogElapsedMs: Double?
}
private final class SecurityAccess {
    let url: URL
    private var active: Bool
    private(set) var retained = true
    let granted: Bool
    init(_ url: URL) { self.url = url; granted = url.startAccessingSecurityScopedResource(); active = granted }
    @discardableResult func release() -> Bool {
        guard retained else { return false }
        retained = false
        guard active else { return false }
        url.stopAccessingSecurityScopedResource(); active = false; return true
    }
    deinit { if active { url.stopAccessingSecurityScopedResource() } }
}

@MainActor
final class WorkspaceModel: ObservableObject {
    static let shared = WorkspaceModel()
    let preview = PreviewController()
    private let probeBridge = CoreBridge()
    private let exportBridge = CoreBridge()
    private let legacyCompatibility: Bool
    private let recentStore: RecentTaskStore
    @Published var assets: [WorkspaceAsset] = []
    @Published var selectedAssetId: UUID?
    @Published var tasks: [WorkspaceTask]
    @Published var selectedTaskId: UUID?
    @Published var activeExport: ActiveExport?
    @Published var connection: CoreConnection?
    @Published var sourceURL: URL?
    @Published var mediaInfo: MediaInfo?
    @Published var snapshot: JobSnapshot?
    @Published var status = "导入视频，选择素材与区间，然后选择快速或精确模式。"
    @Published var issue: String?
    @Published var phase = "idle"
    @Published var startText = "0.000000" { didSet { saveSelectedEdits() } }
    @Published var endText = "0.000000" { didSet { saveSelectedEdits() } }
    @Published var strategy: ExportStrategy = .streamCopyV1 { didSet { saveSelectedEdits() } }
    @Published var ffmpegPath = BackendConfiguration.hostDefault().ffmpeg
    @Published var ffprobePath = BackendConfiguration.hostDefault().ffprobe
    @Published var destinationURL: URL?
    @Published var events: [String] = []
    @Published var closed = false
    @Published private(set) var isImporting = false
    @Published private(set) var isExporting = false
    @Published private(set) var isConnecting = false
    private(set) var trace: [JobSnapshot] = []
    private(set) var probeSnapshot: JobSnapshot?
    private(set) var accessDiagnostics = AccessDiagnostics()
    private var leases: [UUID: SecurityAccess] = [:]
    private var exportSourceAccess: SecurityAccess?
    private var outputAccess: SecurityAccess?
    private var resultURLs: [UUID: URL] = [:]
    private var stopping = false
    private var updatingSelection = false
    private var selectionToken = UUID()
    private var importToken = UUID()
    private var exportJob: String?
    private var probeJob: String?
    private var pendingCancel = false
    private var stopImportRequested = false
    private var configured: BackendConfiguration?
    private var unconfirmedCleanup = false
    private var shutdownTask: Task<Void, Never>?
    private var shutdownResult: Result<Void, Error>?
    private(set) var shutdownDiagnostics = ShutdownDiagnostics()
    private(set) var cachedBridgeDiagnostics = BridgeDiagnostics()

    init(legacyCompatibility: Bool = false, historyPath: URL? = nil) {
        self.legacyCompatibility = legacyCompatibility
        recentStore = RecentTaskStore(path: historyPath)
        tasks = legacyCompatibility ? [] : recentStore.load()
        strategy = legacyCompatibility ? .legacyStreamCopy : .streamCopyV1
    }
    var active: Bool { isImporting || isExporting || isConnecting || stopping || unconfirmedCleanup || phase == "close_failed" }
    var canImport: Bool { !isImporting && !isConnecting && !stopping && !unconfirmedCleanup }
    var canEdit: Bool { mediaInfo != nil && !stopping && !unconfirmedCleanup }
    var hasRunningJob: Bool { isExporting || probeJob != nil }
    var selectedRange: MediaRange? {
        guard let mediaInfo, let start = parseTimeText(startText), let end = parseTimeText(endText),
              start >= 0, end > start, end <= mediaInfo.durationUs else { return nil }
        return MediaRange(startUs: start, endUs: end)
    }
    var canExport: Bool {
        guard !isExporting, !isConnecting, !stopping, !unconfirmedCleanup,
              configured == backendConfiguration, mediaInfo != nil, selectedRange != nil,
              connection?.capabilities.features[strategy.capabilityKey]?.available == true else { return false }
        return strategy == .legacyStreamCopy ? mediaInfo?.trimDisplayMetadataSupported != false
            : mediaInfo?.core003DisplayMetadataSupported != false
    }
    var rangeIssue: String? {
        mediaInfo != nil && selectedRange == nil ? "结束时间必须晚于开始时间，并在媒体时长内；最多输入 6 位小数。" : nil
    }
    var backendConfiguration: BackendConfiguration {
        let host = BackendConfiguration.hostDefault(core003Enabled: !legacyCompatibility)
        return BackendConfiguration(ffmpeg: ffmpegPath, ffprobe: ffprobePath, timeoutMs: host.timeoutMs,
                                    maxOutputBytes: host.maxOutputBytes, core003Enabled: !legacyCompatibility,
                                    accurateReencodeTimeoutMs: host.accurateReencodeTimeoutMs)
    }
    private func updatePhase() {
        guard !closed else { phase = "closed"; return }
        if stopping { phase = "closing" }
        else if unconfirmedCleanup { phase = "cleanup_failed" }
        else if isExporting { phase = "exporting" }
        else if isImporting { phase = "probing" }
        else if isConnecting { phase = "connecting" }
        else { phase = "idle" }
    }
    private func record(_ text: String) { events.append(text); if events.count > 80 { events.removeFirst() } }
    private func hold(_ url: URL) -> SecurityAccess {
        let access = SecurityAccess(url); accessDiagnostics.starts += 1; accessDiagnostics.heldLeases += 1
        if access.granted { accessDiagnostics.granted += 1; accessDiagnostics.activeGranted += 1 }
        return access
    }
    private func release(_ access: SecurityAccess?) {
        guard let access, access.retained else { return }
        accessDiagnostics.heldLeases -= 1
        if access.release() == true { accessDiagnostics.stops += 1; accessDiagnostics.activeGranted -= 1 }
    }
    private func saveSelectedEdits() {
        guard !updatingSelection, let id = selectedAssetId, let i = assets.firstIndex(where: { $0.id == id }) else { return }
        assets[i].startText = startText; assets[i].endText = endText; assets[i].mode = strategy
    }
    private func persistTasks() {
        guard !legacyCompatibility else { return }
        do { try recentStore.save(tasks) }
        catch { record("最近任务摘要未保存，可稍后重试。") }
    }
    func clearHistory() {
        tasks.removeAll { $0.terminal }; resultURLs = resultURLs.filter { key, _ in tasks.contains { $0.id == key } }
        selectedTaskId = tasks.first?.id; persistTasks(); status = "已清除已完成的最近任务；活动任务仍保留。"
    }
    private func observe(_ value: JobSnapshot, exportId: UUID? = nil) {
        if exportId != nil || (snapshot?.operation != "trim" && !isExporting) { snapshot = value }
        if trace.last?.jobId != value.jobId || trace.last?.state != value.state {
            trace.append(value); if trace.count > 512 { trace.removeFirst() }
            record("\(value.operation) · \(value.jobId) → \(value.state)")
        }
        if let exportId, let i = tasks.firstIndex(where: { $0.id == exportId }) {
            tasks[i].coreJobId = value.jobId; tasks[i].state = value.state
            tasks[i].failure = value.error; tasks[i].result = value.result
            tasks[i].actualRange = value.result?.actualRangeUs
            persistTasks()
        }
        guard exportId != nil || !isExporting else { return }
        switch value.state {
        case "queued": status = "任务已排队，等待 Core 处理。"
        case "running": status = value.operation == "probe" ? "正在读取媒体信息…" : "正在导出并校验媒体；可以切换素材，已提交的设置保持不变。"
        case "cancelling": status = "已请求取消，正在等待停止与临时文件清理。"
        case "cancelled": status = "任务已取消，处理已停止并完成清理。"
        case "failed": issue = friendlyError(value.error); status = "任务未完成，请按提示修正后重试。"
        case "succeeded": status = value.operation == "probe" ? "媒体信息已读取，可预览和选区。" : "导出已完成，文件已通过 Core 校验。"
        default: status = "正在读取任务状态…"
        }
    }
    private func friendlyError(_ error: JobFailure?) -> String {
        switch error?.code {
        case "DEPENDENCY_MISSING", "UNSUPPORTED_BACKEND": return "媒体组件或所选编码能力不可用。请检查已安装组件与模式后重新检测。"
        case "INVALID_MEDIA": return "无法读取媒体，文件可能损坏或不完整，请选择其他文件。"
        case "INVALID_INPUT": return "文件或区间无效，请检查来源、起止时间并重试。"
        case "PERMISSION_DENIED": return "无法读取媒体、运行组件或写入位置，请重新授权或选择其他位置。"
        case "OUTPUT_EXISTS": return "目标已存在；已有文件不会被替换，请选择新的文件名。"
        case "UNSUPPORTED_FORMAT": return "Core 不支持这份素材与所选模式的组合。未丢弃轨道，也未静默切换模式。"
        case "SEEK_NOT_EXACT", "OUTPUT_VALIDATION_FAILED": return "输出内容、范围或时序未通过验证，未交付可用结果。请更换区间或素材。"
        case "TIMEOUT": return "处理超过时限，已请求停止；请在清理确认后缩小区间重试。"
        case "RESOURCE_LIMIT": return "素材超出当前有界处理能力，请使用更小的素材或区间。"
        case "OUT_OF_DISK": return "输出位置空间不足，未交付结果。请释放空间或选择其他位置。"
        case "SOURCE_CHANGED": return "源文件在处理期间变化，请重新导入。"
        case "CLEANUP_FAILED": return "清理尚未确认，已保留文件访问并阻止新任务，请保留现场并重试安全关闭。"
        default: return "处理未完成，请查看详情并修正文件、组件或保存位置。"
        }
    }
    func connect() async {
        guard !active else { return }
        isConnecting = true; updatePhase(); issue = nil; closed = false
        defer { isConnecting = false; updatePhase() }
        do {
            try await probeBridge.close(); connection = nil; configured = nil
            let value = try await probeBridge.connect(backend: backendConfiguration)
            connection = value; configured = backendConfiguration
            status = "Core 已连接，请导入或重新选择素材。"
        } catch { issue = "媒体组件连接失败：\(error.localizedDescription)" }
    }
    private func canonicalIdentity(_ url: URL) throws -> (URL, String) {
        guard url.isFileURL else { throw CheckFailure(message: "只接受用户选择的本地文件，不读取网络或目录。") }
        let canonical = url.resolvingSymlinksInPath().standardizedFileURL
        let info = try canonical.resourceValues(forKeys: [.isRegularFileKey])
        guard info.isRegularFile == true else { throw CheckFailure(message: "只接受普通媒体文件，不递归扫描目录。") }
        let attrs = try FileManager.default.attributesOfItem(atPath: canonical.path)
        let dev = (attrs[.systemNumber] as? NSNumber)?.stringValue
        let ino = (attrs[.systemFileNumber] as? NSNumber)?.stringValue
        return (canonical, dev.flatMap { d in ino.map { "\(d):\($0)" } } ?? canonical.path)
    }
    func importURL(_ url: URL) async { await importURLs([url]) }
    func importURLs(_ urls: [URL]) async {
        guard canImport else { issue = "正在导入或关闭，请稍后再试。"; return }
        issue = nil; closed = false
        var candidates: [(URL, String, SecurityAccess)] = []
        var identities = Set(assets.map(\.identity))
        do {
            for url in urls {
                let access = hold(url)
                do {
                    let pair = try canonicalIdentity(url)
                    if identities.insert(pair.1).inserted { candidates.append((pair.0, pair.1, access)) }
                    else { release(access) }
                } catch { release(access); throw error }
            }
            guard assets.count + candidates.count <= 100 else {
                throw CheckFailure(message: "最多保留 100 份素材；本批未导入任何文件，请移除部分素材后重试。")
            }
        } catch {
            for candidate in candidates { release(candidate.2) }
            issue = error.localizedDescription; return
        }
        guard !candidates.isEmpty else { status = "这些素材已在列表中，未重复导入。"; return }
        let token = UUID(); importToken = token; stopImportRequested = false; isImporting = true; updatePhase()
        let newItems = candidates.map { pair in WorkspaceAsset(id: UUID(), identity: pair.1, url: pair.0, mode: legacyCompatibility ? .legacyStreamCopy : .streamCopyV1) }
        for (item, candidate) in zip(newItems, candidates) { leases[item.id] = candidate.2 }
        assets.append(contentsOf: newItems)
        if selectedAssetId == nil, let first = newItems.first { await selectAsset(id: first.id) }
        defer { isImporting = false; probeJob = nil; updatePhase() }
        for item in newItems {
            guard !stopping, token == importToken, !stopImportRequested else { break }
            guard let index = assets.firstIndex(where: { $0.id == item.id }) else { continue }
            assets[index].state = "probing"
            do {
                try await probeBridge.close()
                let config = backendConfiguration
                let connected = try await probeBridge.connect(backend: config)
                guard !stopping, token == importToken, !stopImportRequested else { break }
                connection = connected; configured = config
                let submitted = try await probeBridge.submitProbe(source: item.url)
                probeJob = submitted.jobId; observe(submitted)
                var done = submitted
                repeat {
                    done = try await probeBridge.poll(id: submitted.jobId)
                    guard !stopping, token == importToken else { break }
                    observe(done)
                    if !["succeeded", "failed", "cancelled"].contains(done.state) { try await Task.sleep(for: .milliseconds(30)) }
                } while !["succeeded", "failed", "cancelled"].contains(done.state)
                guard !stopping, token == importToken else { break }
                probeSnapshot = done; probeJob = nil
                guard let i = assets.firstIndex(where: { $0.id == item.id }) else { continue }
                assets[i].state = done.state
                assets[i].info = done.result?.mediaInfo
                assets[i].failure = done.error.map { friendlyError($0) }
                if let info = assets[i].info { assets[i].endText = timeText(info.durationUs) }
                if done.error?.code == "CLEANUP_FAILED" { unconfirmedCleanup = true; issue = friendlyError(done.error); break }
                if selectedAssetId == item.id { await selectAsset(id: item.id, saveCurrent: false) }
            } catch {
                guard !stopping else { break }
                if let i = assets.firstIndex(where: { $0.id == item.id }) { assets[i].state = "failed"; assets[i].failure = error.localizedDescription }
                issue = error.localizedDescription
                do { try await Task.detached { [probeBridge] in try await probeBridge.close() }.value }
                catch { unconfirmedCleanup = true; break }
            }
        }
        if stopImportRequested && !unconfirmedCleanup {
            for i in assets.indices where assets[i].state == "waiting" || assets[i].state == "probing" {
                assets[i].state = "cancelled"; assets[i].failure = "探测已停止，可移除后重新导入。"
            }
        }
        if !stopping && !unconfirmedCleanup {
            do { try await probeBridge.close() }
            catch { unconfirmedCleanup = true; issue = "素材探测关闭尚未确认，请重试安全关闭。" }
        }
    }
    func selectAsset(id: UUID, saveCurrent: Bool = true) async {
        guard !stopping, let item = assets.first(where: { $0.id == id }) else { return }
        if saveCurrent { saveSelectedEdits() }; selectionToken = UUID(); let token = selectionToken
        selectedAssetId = id; updatingSelection = true
        sourceURL = item.url; mediaInfo = item.info; startText = item.startText; endText = item.endText; strategy = item.mode
        updatingSelection = false; saveSelectedEdits(); issue = item.failure
        preview.clear()
        if let info = item.info {
            do { try await preview.load(url: item.url, media: info) }
            catch { if token == selectionToken && !stopping { preview.status = "系统预览不可用；导出能力由 Core 判断。"; record("系统预览未就绪") } }
        }
        guard token == selectionToken, !stopping else { return }
        if let failure = item.failure { issue = failure }
    }
    func removeAsset(id: UUID) async {
        guard !isImporting, !stopping, !unconfirmedCleanup else { issue = "素材仍在使用或清理中，请稍后移除。"; return }
        guard activeExport?.draft.assetId != id else { issue = "该素材仍用于活动导出，请等待任务结束后移除。"; return }
        do { try await preview.shutdown() } catch { issue = error.localizedDescription; return }
        let wasSelected = selectedAssetId == id
        release(leases.removeValue(forKey: id)); assets.removeAll { $0.id == id }
        if wasSelected {
            selectedAssetId = nil; sourceURL = nil; mediaInfo = nil
            if let first = assets.first { await selectAsset(id: first.id) }
        } else if let selectedAssetId { await selectAsset(id: selectedAssetId) }
    }
    private func currentDraft() -> ExportDraft? {
        guard canExport, let id = selectedAssetId, let sourceURL, let range = selectedRange else { return nil }
        return ExportDraft(assetId: id, sourceURL: sourceURL, sourceName: sourceURL.lastPathComponent, range: range,
                           mode: strategy, configuration: backendConfiguration)
    }
    func export(to url: URL) async {
        guard let draft = currentDraft() else { issue = rangeIssue ?? "当前媒体、模式或组件尚不可导出。"; return }
        await export(draft: draft, to: url)
    }
    private func export(draft: ExportDraft, to url: URL) async {
        guard !isExporting, !stopping, !unconfirmedCleanup, assets.contains(where: { $0.id == draft.assetId }) else { return }
        let localId = UUID(); isExporting = true; pendingCancel = false; closed = false; issue = nil
        destinationURL = url; snapshot = nil
        activeExport = ActiveExport(id: localId, draft: draft, destinationURL: url)
        exportSourceAccess = hold(draft.sourceURL); outputAccess = hold(url.deletingLastPathComponent()); updatePhase()
        var safe = false
        defer {
            isExporting = false
            if safe { release(exportSourceAccess); exportSourceAccess = nil; release(outputAccess); outputAccess = nil; activeExport = nil }
            updatePhase()
        }
        do {
            try await exportBridge.close()
            let connected = try await exportBridge.connect(backend: draft.configuration)
            guard !stopping else { return }
            connection = connected
            let submitted = try await exportBridge.submitTrim(source: draft.sourceURL, destination: url, range: draft.range, strategy: draft.mode)
            exportJob = submitted.jobId; activeExport?.coreJobId = submitted.jobId
            tasks.insert(WorkspaceTask(id: localId, sourceAssetId: draft.assetId, sourceName: draft.sourceName,
                         destinationName: url.lastPathComponent, mode: draft.mode, requestedRange: draft.range, createdAt: Date(),
                         coreJobId: submitted.jobId, state: submitted.state, destinationBookmark: RecentTaskStore.bookmark(url)), at: 0)
            if tasks.count > 100 { tasks.removeLast(tasks.count - 100) }; selectedTaskId = localId
            resultURLs = resultURLs.filter { key, _ in tasks.contains { $0.id == key } }
            resultURLs[localId] = url; observe(submitted, exportId: localId)
            if pendingCancel { try await exportBridge.cancel(id: submitted.jobId) }
            while !stopping {
                let value = try await exportBridge.poll(id: submitted.jobId); observe(value, exportId: localId)
                if ["succeeded", "failed", "cancelled"].contains(value.state) {
                    exportJob = nil
                    if value.error?.code == "CLEANUP_FAILED" { unconfirmedCleanup = true; issue = friendlyError(value.error) }
                    else {
                        safe = true
                        if value.state == "succeeded", value.result?.fileCreated == true, let i = tasks.firstIndex(where: { $0.id == localId }) {
                            tasks[i].destinationBookmark = RecentTaskStore.bookmark(url); persistTasks()
                        }
                    }
                    break
                }
                try await Task.sleep(for: .milliseconds(30))
            }
        } catch {
            guard !stopping else { return }
            issue = error.localizedDescription
            do {
                try await Task.detached { [exportBridge] in try await exportBridge.close() }.value
                if let exportJob, let value = await exportBridge.closedJobSnapshots().first(where: { $0.jobId == exportJob }) { observe(value, exportId: localId) }
                safe = true; exportJob = nil
            }
            catch { unconfirmedCleanup = true; issue = "导出清理未确认，已保留访问并阻止新任务。" }
        }
    }
    func cancel() async {
        guard !stopping else { return }
        if isExporting {
            pendingCancel = true
            if let exportJob { do { try await exportBridge.cancel(id: exportJob) } catch { issue = error.localizedDescription } }
        } else if let probeJob {
            do { try await probeBridge.cancel(id: probeJob); stopImportRequested = true }
            catch { issue = error.localizedDescription }
        } else if isImporting {
            stopImportRequested = true; status = "已请求停止剩余素材探测。"
        }
    }
    private func elapsedMilliseconds(since start: ContinuousClock.Instant) -> Double {
        let value = start.duration(to: .now).components
        return Double(value.seconds) * 1000 + Double(value.attoseconds) / 1_000_000_000_000_000
    }
    func shutdown() async throws {
        guard !closed else { return }
        if shutdownTask == nil {
            stopping = true; updatePhase(); status = "正在停止任务并清理…"
            selectionToken = UUID(); importToken = UUID(); preview.pause()
            shutdownResult = nil
            shutdownDiagnostics.cleanupAttempts += 1
            shutdownDiagnostics.cleanupPending = true
            shutdownDiagnostics.cleanupCompleted = false
            shutdownDiagnostics.cleanupFailed = false
            let started = ContinuousClock.now
            // Keep the cleanup task owned by this model after a caller's watchdog
            // expires. It is never cancelled or discarded merely to return to UI.
            shutdownTask = Task { @MainActor [self] in
                do {
                    try await finishShutdown()
                    shutdownResult = .success(())
                    shutdownDiagnostics.cleanupCompleted = true
                } catch {
                    shutdownResult = .failure(error)
                    shutdownDiagnostics.cleanupFailed = true
                    // Retain stopping/owners/leases. A later explicit shutdown may
                    // retry a recoverable close; CLEANUP_FAILED stays sticky in CoreBridge.
                    phase = "close_failed"; issue = "关闭尚未确认：\(error.localizedDescription)"
                }
                shutdownDiagnostics.cleanupElapsedMs = elapsedMilliseconds(since: started)
                shutdownDiagnostics.cleanupPending = false
                shutdownTask = nil
            }
        }
        let waiting = ContinuousClock.now
        let deadline = waiting + .seconds(15)
        while shutdownResult == nil {
            guard ContinuousClock.now < deadline else {
                shutdownDiagnostics.watchdogTimeouts += 1
                shutdownDiagnostics.watchdogElapsedMs = elapsedMilliseconds(since: waiting)
                phase = "close_failed"
                status = "关闭超过 15 秒仍未确认；后台继续安全清理，当前不能退出或启动新任务。"
                issue = "已保留会话与文件访问。可以等待或重试安全关闭；只有确认清理后才会释放。"
                throw CheckFailure(message: "Application shutdown not confirmed within 15 seconds; cleanup remains owned and pending")
            }
            _ = await Task.detached { try? await Task.sleep(for: .milliseconds(20)) }.value
        }
        try shutdownResult?.get()
    }
    private func finishShutdown() async throws {
        try await exportBridge.close(); try await probeBridge.close()
        let snapshots = await exportBridge.closedJobSnapshots()
        if let exportJob, let value = snapshots.first(where: { $0.jobId == exportJob }), let id = activeExport?.id { observe(value, exportId: id) }
        guard !unconfirmedCleanup else { throw CheckFailure(message: "清理失败仍未确认，请保留现场；不能宣布已安全关闭。") }
        try await preview.shutdown()
        release(exportSourceAccess); exportSourceAccess = nil; release(outputAccess); outputAccess = nil
        for lease in leases.values { release(lease) }; leases.removeAll()
        activeExport = nil; exportJob = nil; probeJob = nil; isExporting = false; isImporting = false
        closed = true; phase = "closed"; configured = nil; connection = nil; persistTasks()
        status = "会话、预览和文件访问已安全关闭。"
    }
    func diagnostics() async -> BridgeDiagnostics {
        var value = await probeBridge.diagnostics(); value.accumulate(await exportBridge.diagnostics())
        cachedBridgeDiagnostics = value; return value
    }
    func setInPoint() { startText = timeText(preview.positionUs) }
    func setOutPoint() { endText = timeText(preview.positionUs) }
    func chooseInput() {
        guard canImport else { return }
        let panel = NSOpenPanel(); panel.title = "导入媒体"; panel.canChooseDirectories = false; panel.allowsMultipleSelection = true
        panel.allowedContentTypes = [.movie, .mpeg4Movie, .quickTimeMovie]
        panel.begin { [weak self] response in
            guard response == .OK else { return }; let urls = panel.urls
            Task { @MainActor in await self?.importURLs(urls) }
        }
    }
    func chooseOutput() {
        guard let draft = currentDraft() else { return }
        let panel = NSSavePanel(); panel.title = "导出为新的 MP4"; panel.allowedContentTypes = [.mpeg4Movie]
        panel.nameFieldStringValue = draft.sourceURL.deletingPathExtension().lastPathComponent + (draft.mode.isReencoded ? "-精确片段.mp4" : "-快速片段.mp4")
        panel.message = draft.mode.isReencoded ? "精确模式会重新编码，属于有损导出。已有文件不会替换。" : "快速模式不重新编码，实际范围可扩展至关键帧。已有文件不会替换。"
        panel.begin { [weak self] response in
            guard response == .OK, let url = panel.url else { return }
            Task { @MainActor in await self?.export(draft: draft, to: url) }
        }
    }
    func chooseTool(ffmpeg: Bool) {
        guard !active else { return }
        let panel = NSOpenPanel(); panel.title = ffmpeg ? "选择 FFmpeg" : "选择 ffprobe"; panel.canChooseDirectories = false
        panel.begin { [weak self] response in
            guard response == .OK, let url = panel.url else { return }
            Task { @MainActor in
                if ffmpeg { self?.ffmpegPath = url.path } else { self?.ffprobePath = url.path }
                await self?.connect()
            }
        }
    }
    private func resultURL(_ id: UUID?) -> URL? {
        guard let id, let item = tasks.first(where: { $0.id == id }), item.state == "succeeded" else { return nil }
        return resultURLs[id].flatMap { FileManager.default.isReadableFile(atPath: $0.path) ? $0 : nil }
            ?? RecentTaskStore.resolve(item.destinationBookmark)
    }
    func reauthorizeResult(taskId: UUID) {
        guard let item = tasks.first(where: { $0.id == taskId }), item.state == "succeeded" else { return }
        let panel = NSOpenPanel(); panel.title = "重新授权结果文件"; panel.canChooseDirectories = false
        panel.message = "请选择结果文件：\(item.destinationName)。文件移动后需要重新授权；不会重新运行导出。"
        panel.begin { [weak self] response in
            guard response == .OK, let url = panel.url else { return }
            Task { @MainActor in
                guard let self, let index = self.tasks.firstIndex(where: { $0.id == taskId }) else { return }
                let access = self.hold(url)
                defer { self.release(access) }
                guard url.lastPathComponent == item.destinationName,
                      (try? url.resourceValues(forKeys: [.isRegularFileKey]).isRegularFile) == true else {
                    self.issue = "请选择与摘要同名的普通结果文件。"; return
                }
                self.tasks[index].destinationBookmark = RecentTaskStore.bookmark(url)
                self.resultURLs[taskId] = url; self.persistTasks(); self.status = "已更新结果文件授权。"
            }
        }
    }
    func revealResult(taskId: UUID? = nil) {
        guard let url = resultURL(taskId ?? selectedTaskId) else { issue = "结果已移动或授权失效，请重新选择文件。"; return }
        let access = hold(url); NSWorkspace.shared.activateFileViewerSelecting([url]); release(access)
    }
    func openResult(taskId: UUID? = nil) {
        guard let url = resultURL(taskId ?? selectedTaskId) else { issue = "结果已移动或授权失效，请重新选择文件。"; return }
        let access = hold(url); let opened = NSWorkspace.shared.open(url); release(access)
        if !opened { issue = "无法打开结果，请在 Finder 检查文件。" }
    }
}
