import AppKit
import Combine
import Darwin
import Foundation

private struct AIComponents: Sendable {
    let worker: URL
    let graphs: URL
    let runtime: URL
    let model: URL
}

private struct AIExportCleanupFailure: LocalizedError, Sendable {
    let directory: URL
    var errorDescription: String? { "导出临时目录清理尚未确认。" }
}

@MainActor final class AISeparationModel: ObservableObject {
    static let shared = AISeparationModel()
    let audition = StemPlaybackController()
    var canChooseSource: Bool { !stopping && !isRunning && !isConfiguring && !isRestoringComponents && !cleanupUnconfirmed }
    var canStart: Bool { canChooseSource && sourceURL != nil && configurationReady }
    var canExport: Bool { canChooseSource && resultDirectory != nil }
    func stopAudition() async {
        do { try await audition.stop() }
        catch { cleanupUnconfirmed = true; issue = error.localizedDescription }
    }
    @Published private(set) var sourceURL: URL?
    @Published private(set) var resultDirectory: URL?
    @Published private(set) var status = "选择音频后开始分离。"
    @Published private(set) var issue: String?
    private(set) var lastCoreFailure: JobFailure?
    @Published private(set) var isRunning = false
    @Published private(set) var isExporting = false
    @Published private(set) var isMixExporting = false
    @Published private(set) var activeJobState: String?
    @Published private(set) var configurationReady = false
    @Published private(set) var isConfiguring = false
    @Published private(set) var isRestoringComponents = false
    @Published private(set) var componentStatus = "使用随应用安装的分离组件。"
    @Published private(set) var componentIssue: String?
    @Published private(set) var modelFileBytes: Int64?
    private let bridge = CoreBridge()
    private var components: AIComponents?
    private var workspace: URL?
    private var jobID: String?
    private var historyID: UUID?
    @Published private(set) var historyIssue: String?
    @Published private(set) var previousAttemptInterrupted = false
    private static let pendingAttemptKey = "ai.unfinished-local-attempt.v1"
    private func recordHistory(_ action: () throws -> Void) {
        do { try action(); historyIssue = nil }
        catch { historyIssue = "任务结果不受影响，但最近摘要未能保存：\(error.localizedDescription)" }
    }
    func revealHistory(_ id: UUID) {
        let found: Bool? = RecentTaskLedger.shared.withAIResultDirectory(id) { directory in
            NSWorkspace.shared.activateFileViewerSelecting([directory]); return true
        }
        if found != true { historyIssue = "结果已移动、删除或授权失效，请重新选择结果文件夹。" }
    }
    func openHistory(_ id: UUID, stem: StemPlaybackController.Stem) {
        let opened: Bool? = RecentTaskLedger.shared.withAIResultDirectory(id) { directory in
            NSWorkspace.shared.open(directory.appendingPathComponent(stem.filename))
        }
        if opened != true { historyIssue = "结果不可打开，请重新授权或重新选择结果文件夹。" }
    }
    func reauthorizeHistory(_ id: UUID) async {
        guard canChooseSource, !NativeFilePanels.hasPending else { return }
        guard let panel = NativeFilePanels.open() else { return }; panel.message = "重新选择这条记录的原结果文件夹"
        panel.canChooseFiles = false; panel.canChooseDirectories = true
        let selection = await NativeFilePanels.choose(panel)
        guard selection.response == .OK, canChooseSource, let directory = selection.url else { return }
        recordHistory { try RecentTaskLedger.shared.reauthorizeAIResult(id, directory: directory) }
    }
    private var cancelRequested = false
    @Published private(set) var cleanupUnconfirmed = false
    @Published private(set) var stopping = false
    private var componentStore: AIComponentStore?
    private var bundledComponents: AIComponents?
    private var exportFolder: URL?
    private var exportAccess: SecurityAccess?
    private var mixSourceAccess: SecurityAccess?
    private(set) var exportCleanupDirectory: URL?
    var exportAccessRetained: Bool { exportAccess?.retained == true }
    private enum BookmarkKey {
        static let source = "ai.source.bookmark"
        static let worker = "ai.native-worker.bookmark"
        static let graphs = "ai.native-graphs.bookmark"
        static let runtime = "ai.native-runtime.bookmark"
        static let model = "ai.native-model.bookmark"
        static let exportFolder = "ai.export-folder.bookmark"
    }

    init() {
        let env = ProcessInfo.processInfo.environment
        previousAttemptInterrupted = UserDefaults.standard.bool(forKey: Self.pendingAttemptKey)
        var stale = false
        sourceURL = Self.restoreBookmark(BookmarkKey.source, stale: &stale)
        exportFolder = Self.restoreBookmark(BookmarkKey.exportFolder, stale: &stale)
        if let executable = Bundle.main.executableURL, let frameworks = Bundle.main.privateFrameworksURL,
           let resources = Bundle.main.resourceURL {
            let bundled = AIComponents(worker: executable.deletingLastPathComponent().appendingPathComponent("media-ai-worker"),
                graphs: resources.appendingPathComponent("ai/graphs", isDirectory: true),
                runtime: frameworks.appendingPathComponent("libonnxruntime.dylib"),
                model: resources.appendingPathComponent("ai/model.onnx"))
            if [bundled.worker, bundled.graphs, bundled.runtime, bundled.model].allSatisfy({ FileManager.default.fileExists(atPath: $0.path) }) {
                bundledComponents = bundled
                let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
                componentStore = AIComponentStore(root: support.appendingPathComponent("MediaStudio/ai-components-v1", isDirectory: true),
                    referenceGraphs: bundled.graphs, referenceModel: bundled.model)
            }
        }
        if let worker = env["MEDIASTUDIO_AI_NATIVE_WORKER"], let graphs = env["MEDIASTUDIO_AI_NATIVE_GRAPHS"],
           let runtime = env["MEDIASTUDIO_AI_NATIVE_RUNTIME"], let model = env["MEDIASTUDIO_AI_MODEL"] {
            components = AIComponents(worker: URL(fileURLWithPath: worker), graphs: URL(fileURLWithPath: graphs),
                                      runtime: URL(fileURLWithPath: runtime), model: URL(fileURLWithPath: model))
            configurationReady = true
            componentStatus = "使用显式开发环境组件。"
        } else if let componentStore {
            isRestoringComponents = true
            componentStatus = "正在核验本机模型缓存…"
            Task { [weak self] in
                guard let self else { return }
                defer { self.isRestoringComponents = false }
                do {
                    if let selected = try await componentStore.load() { self.useManaged(selected) }
                    else if [BookmarkKey.worker, BookmarkKey.graphs, BookmarkKey.runtime, BookmarkKey.model].contains(where: { UserDefaults.standard.data(forKey: $0) != nil }) {
                        self.componentStatus = "旧版外部组件需要重新选择模型数据。"
                        self.componentIssue = "请导入模型与图数据，确认改用随应用安装的分离程序和运行时；不会自动替换旧配置。"
                    } else {
                        self.components = self.bundledComponents; self.configurationReady = true
                        self.componentStatus = "使用随应用安装的分离组件与模型。"; self.refreshModelSize()
                    }
                } catch {
                    self.configurationReady = false
                    self.componentStatus = "本机模型缓存不可用。"
                    self.componentIssue = error.localizedDescription + " 请重新导入原始模型与图数据。"
                }
            }
        } else {
            componentIssue = "随应用安装的分离组件缺失，请恢复完整应用后重试。"
        }
        if stale { issue = "部分文件授权已失效，请重新选择。" }
        else if sourceURL != nil { status = "已恢复上次选择的音频。" }
        refreshModelSize()
    }
    private func refreshModelSize() {
        modelFileBytes = components.flatMap { try? $0.model.resourceValues(forKeys: [.fileSizeKey]).fileSize }.map(Int64.init)
    }
    func chooseSource() async {
        guard canChooseSource, !NativeFilePanels.hasPending else { return }
        guard let panel = NativeFilePanels.open() else { return }; panel.message = "选择需要分离的音频"
        panel.canChooseFiles = true; panel.canChooseDirectories = false; panel.allowsMultipleSelection = false
        let selection = await NativeFilePanels.choose(panel)
        guard selection.response == .OK, canChooseSource, let url = selection.url else { return }
        await importSource(url)
        if canChooseSource && sourceURL == url && !Self.saveBookmark(url, key: BookmarkKey.source) {
            issue = "本次可继续使用，但音频授权无法保存；重启后请重新选择。"
        }
    }
    func importSource(_ url: URL) async {
        guard canChooseSource else { return }
        isRunning = true
        defer { isRunning = false }
        do { try await audition.stop() }
        catch { cleanupUnconfirmed = true; issue = error.localizedDescription; return }
        guard !stopping, !cleanupUnconfirmed else { return }
        if let root = workspace {
            if url.resolvingSymlinksInPath().path.hasPrefix(root.path + "/") {
                issue = "请先导出结果，再将其作为新音频导入。"; return
            }
            isRunning = true; resultDirectory = nil
            do { try await Self.remove(root); workspace = nil }
            catch { cleanupUnconfirmed = true; isRunning = false; issue = "上次结果清理尚未确认。"; return }
            isRunning = false
        }
        guard !stopping else { return }
        sourceURL = url; issue = nil; status = "音频已选择。"
    }
    func configure() async {
        guard canChooseSource, !NativeFilePanels.hasPending, let componentStore, bundledComponents != nil else { return }
        isConfiguring = true
        defer { isConfiguring = false }
        func select(_ message: String, directories: Bool = false) async -> URL? {
            guard let panel = NativeFilePanels.open() else { return nil }; panel.message = message
            panel.canChooseFiles = !directories; panel.canChooseDirectories = directories
            let selection = await NativeFilePanels.choose(panel)
            return selection.response == .OK && !stopping ? selection.url : nil
        }
        guard let graphs = await select("导入本包版本的模型图文件夹；使用随应用安装的程序与运行时", directories: true) else { return }
        let graphsAccess = SecurityAccess(graphs); defer { graphsAccess.release() }
        guard let model = await select("导入本包版本的 UMX-HQ 模型；将保存到本机私有缓存"), !stopping else { return }
        let modelAccess = SecurityAccess(model); defer { modelAccess.release() }
        componentStatus = "正在校验并导入模型缓存…"
        do {
            let selection = try await componentStore.install(graphs: graphs, model: model)
            useManaged(selection)
            for key in [BookmarkKey.worker, BookmarkKey.graphs, BookmarkKey.runtime, BookmarkKey.model] { UserDefaults.standard.removeObject(forKey: key) }
            componentIssue = nil
        } catch {
            // A publication may have succeeded before retirement cleanup failed.
            // Re-read the committed selection; never claim it was rolled back.
            if let selected = try? await componentStore.load() { useManaged(selected) }
            componentIssue = error.localizedDescription
            componentStatus = configurationReady ? "保留当前可用配置；请检查模型缓存提示。" : "模型缓存未就绪，请重新导入。"
        }
    }
    private func useManaged(_ selection: AIComponentStore.Selection) {
        guard let bundled = bundledComponents else { return }
        components = AIComponents(worker: bundled.worker, graphs: selection.graphs, runtime: bundled.runtime, model: selection.model)
        modelFileBytes = selection.modelBytes; configurationReady = true
        componentStatus = "包内程序与运行时 · 本机模型缓存已核验。"
    }
    func start() async {
        guard canStart, let sourceURL, let components else { return }
        isRunning = true; cancelRequested = false
        do { try await audition.stop() }
        catch {
            cleanupUnconfirmed = true; isRunning = false
            issue = error.localizedDescription; status = "试听停止尚未确认，已保留结果。"
            return
        }
        guard !stopping else { isRunning = false; return }
        guard !cancelRequested else { isRunning = false; status = "已取消，原试听结果保留。"; return }
        jobID = nil; historyID = nil; historyIssue = nil; issue = nil; lastCoreFailure = nil; resultDirectory = nil; activeJobState = nil
        UserDefaults.standard.set(true, forKey: Self.pendingAttemptKey)
        previousAttemptInterrupted = false
        status = "正在准备音频…"
        do {
            if let old = workspace { try await Self.remove(old); workspace = nil }
            let root = try Self.makeWorkspace(); workspace = root
            let input = root.appendingPathComponent("input." + (sourceURL.pathExtension.isEmpty ? "audio" : sourceURL.pathExtension))
            let result = root.appendingPathComponent("result", isDirectory: true)
            let scoped = sourceURL.startAccessingSecurityScopedResource()
            do { try await Self.copy(sourceURL, to: input) }
            catch { if scoped { sourceURL.stopAccessingSecurityScopedResource() }; throw error }
            if scoped { sourceURL.stopAccessingSecurityScopedResource() }
            if cancelRequested { status = "已取消。"; try await Self.remove(root); workspace = nil; isRunning = false; UserDefaults.standard.removeObject(forKey: Self.pendingAttemptKey); return }
            let media = WorkspaceModel.shared.backendConfiguration
            let ai = AIBackendConfiguration(nativeWorker: components.worker.path, nativeGraphs: components.graphs.path,
                                            nativeRuntime: components.runtime.path, model: components.model.path,
                                            timeoutMs: 3_600_000, developmentRoot: root.path)
            let config = BackendConfiguration(ffmpeg: media.ffmpeg, ffprobe: media.ffprobe, timeoutMs: media.timeoutMs,
                maxOutputBytes: media.maxOutputBytes, core003Enabled: true, core004Enabled: true, aiBackend: ai)
            _ = try await bridge.connect(backend: config)
            if cancelRequested {
                try await bridge.close(); try await Self.remove(root); workspace = nil
                status = "已取消。"; isRunning = false; UserDefaults.standard.removeObject(forKey: Self.pendingAttemptKey); return
            }
            status = "正在检查并提交分离任务…"
            let reply = try await bridge.submitAI(source: input, destination: result)
            guard let id = reply.jobId else {
                let failure = reply.errors?.first
                if let failure { lastCoreFailure = JobFailure(code: failure.code, message: failure.message) }
                if failure?.code == "CLEANUP_FAILED" { cleanupUnconfirmed = true }
                throw CheckFailure(message: Self.message(failure?.code, fallback: failure?.message))
            }
            jobID = id
            recordHistory { historyID = try RecentTaskLedger.shared.beginAI(sourceName: sourceURL.lastPathComponent) }
            if cancelRequested { try await bridge.cancel(id: id) }
            status = "正在分离音频…"
            while true {
                let snapshot = try await bridge.poll(id: id)
                if activeJobState != snapshot.state, let historyID {
                    recordHistory { try RecentTaskLedger.shared.updateAI(historyID, state: snapshot.state, failure: snapshot.error) }
                }
                activeJobState = snapshot.state
                if snapshot.state == "succeeded" {
                    guard let value = snapshot.aiResult, value.modelId == "umx_hq_vocals_onnx_v1",
                          value.stems == ["vocals.wav", "accompaniment.wav"], value.directoryName == "result",
                          value.frames > 0, value.sampleRate == 44100, value.channels == 2 else {
                        throw CheckFailure(message: "分离结果未能确认。")
                    }
                    resultDirectory = result; cleanupUnconfirmed = value.cleanupUnconfirmed
                    status = value.cleanupUnconfirmed ? "分离完成，但清理尚未确认。" : "分离完成，可以试听或导出。"
                    break
                }
                if snapshot.state == "failed" || snapshot.state == "cancelled" {
                    lastCoreFailure = snapshot.error
                    if snapshot.error?.code == "CLEANUP_FAILED" { cleanupUnconfirmed = true }
                    if snapshot.state == "failed" { issue = Self.message(snapshot.error?.code, fallback: snapshot.error?.message) }
                    status = snapshot.state == "cancelled" ? "已取消。" : "分离未完成。"
                    break
                }
                try await Task.sleep(for: .milliseconds(100))
            }
            jobID = nil
            if !cleanupUnconfirmed { try await bridge.close() }
            if resultDirectory == nil && !cleanupUnconfirmed { try await Self.remove(root); workspace = nil }
        } catch {
            issue = error.localizedDescription; status = "分离未完成。"
            if !cleanupUnconfirmed {
                do {
                    try await bridge.close()
                    if let root = workspace { try await Self.remove(root); workspace = nil }
                } catch { cleanupUnconfirmed = true; issue = "任务清理尚未确认，请保留当前窗口。" }
            }
        }
        if let historyID {
            let state = cleanupUnconfirmed ? "unconfirmed" : resultDirectory != nil ? "succeeded" : activeJobState == "cancelled" ? "cancelled" : "failed"
            recordHistory { try RecentTaskLedger.shared.updateAI(historyID, state: state, failure: lastCoreFailure) }
        }
        if !cleanupUnconfirmed { UserDefaults.standard.removeObject(forKey: Self.pendingAttemptKey) }
        isRunning = false
    }
    func cancel() async {
        guard isRunning else { return }
        cancelRequested = true; status = jobID == nil ? "正在取消，等待文件准备结束后清理…" : "正在取消并清理…"
        if let id = jobID {
            do { try await bridge.cancel(id: id) }
            catch { issue = "取消请求尚未确认：\(error.localizedDescription)" }
        }
    }
    func exportResult() async {
        guard canExport, !NativeFilePanels.hasPending, let resultDirectory else { return }
        guard let folderPanel = NativeFilePanels.open() else { return }; folderPanel.message = "选择导出文件夹"
        folderPanel.canChooseFiles = false; folderPanel.canChooseDirectories = true
        folderPanel.canCreateDirectories = true; folderPanel.directoryURL = exportFolder
        let folderSelection = await NativeFilePanels.choose(folderPanel)
        guard folderSelection.response == .OK, canChooseSource, let folder = folderSelection.url else { return }
        let access = SecurityAccess(folder)
        // Transfer this exact system-selected URL's scope to the export owner.
        // On an unconfirmed cleanup it remains held after this panel returns.
        defer { if exportAccess !== access { access.release() } }
        exportFolder = folder
        let saved = Self.saveBookmark(folder, key: BookmarkKey.exportFolder)
        guard let panel = NativeFilePanels.save() else { return }; panel.title = "为两条音轨选择新目录名称"
        panel.directoryURL = folder; panel.nameFieldStringValue = "分离结果"
        let selection = await NativeFilePanels.choose(panel)
        guard selection.response == .OK, canChooseSource, let destination = selection.url else { return }
        guard destination.deletingLastPathComponent().standardizedFileURL == folder.standardizedFileURL else {
            issue = "请选择刚刚授权的导出文件夹。"; return
        }
        await export(to: destination, resultDirectory: resultDirectory, access: access)
        if !saved && issue == nil { issue = "本次已导出，但文件夹授权无法保存；重启后请重新选择。" }
    }
    func exportMixResult() async {
        guard canExport, !NativeFilePanels.hasPending, let directory = resultDirectory else { return }
        let solo = audition.soloedStems
        let muted = audition.mutedStems
        let vocals = !muted.contains(.vocals) && (solo.isEmpty || solo.contains(.vocals))
        let accompaniment = !muted.contains(.accompaniment) && (solo.isEmpty || solo.contains(.accompaniment))
        guard vocals || accompaniment else { issue = "请至少保留一条可听音轨后再导出混音。"; return }
        guard let folderPanel = NativeFilePanels.open() else { return }
        folderPanel.message = "选择混音导出文件夹"
        folderPanel.canChooseFiles = false; folderPanel.canChooseDirectories = true
        folderPanel.canCreateDirectories = true; folderPanel.directoryURL = exportFolder
        let folderSelection = await NativeFilePanels.choose(folderPanel)
        guard folderSelection.response == .OK, canExport, resultDirectory == directory, let folder = folderSelection.url else { return }
        let access = SecurityAccess(folder)
        defer { if exportAccess !== access { access.release() } }
        guard let panel = NativeFilePanels.save() else { return }
        panel.title = "导出混音"; panel.message = "请选择尚不存在的 WAV 文件名；已有文件不会被替换。"
        panel.directoryURL = folder; panel.nameFieldStringValue = "混音.wav"
        let selection = await NativeFilePanels.choose(panel)
        guard selection.response == .OK, canExport, resultDirectory == directory, let destination = selection.url else { return }
        guard destination.deletingLastPathComponent().standardizedFileURL == folder.standardizedFileURL,
              destination.pathExtension == "wav" else {
            issue = "请选择刚刚授权的文件夹，并使用小写 .wav 扩展名。"; return
        }
        await exportMix(directory: directory, destination: destination, access: access,
                        includeVocals: vocals, includeAccompaniment: accompaniment)
    }
    private func exportMix(directory: URL, destination: URL, access: SecurityAccess,
                           includeVocals: Bool, includeAccompaniment: Bool) async {
        guard canExport, resultDirectory == directory else { access.release(); return }
        let sourceAccess = SecurityAccess(directory)
        isRunning = true; isExporting = true; isMixExporting = true; cancelRequested = false; issue = nil
        activeJobState = nil; jobID = nil; exportAccess = access; status = "正在准备混音导出…"
        do {
            let media = WorkspaceModel.shared.backendConfiguration
            let config = BackendConfiguration(ffmpeg: media.ffmpeg, ffprobe: media.ffprobe,
                timeoutMs: media.timeoutMs, maxOutputBytes: media.maxOutputBytes,
                core003Enabled: true, core004Enabled: true, mixStemsEnabled: true)
            let connection = try await bridge.connect(backend: config)
            guard connection.capabilities.features["mix_stems_v1"]?.available == true else {
                throw CheckFailure(message: "当前媒体组件不支持混音导出。")
            }
            if cancelRequested { status = "已取消。" }
            else {
                let reply = try await bridge.submitMixStems(directory: directory, destination: destination,
                    includeVocals: includeVocals, includeAccompaniment: includeAccompaniment)
                guard reply.schemaVersion == 0, reply.operation == "mix_stems", reply.state == "queued",
                      !reply.mockOnly, !reply.jobId.isEmpty, reply.jobId.utf8.count <= 128 else {
                    throw CheckFailure(message: "混音任务回执未能确认。")
                }
                let id = reply.jobId
                jobID = id
                if cancelRequested { try await bridge.cancel(id: id) }
                status = "正在导出混音…"
                while true {
                    let snapshot = try await bridge.poll(id: id)
                    guard snapshot.schemaVersion == 0, snapshot.jobId == id,
                          snapshot.operation == "mix_stems", !snapshot.mockOnly else {
                        throw CheckFailure(message: "混音任务状态未能确认。")
                    }
                    activeJobState = snapshot.state
                    if snapshot.state == "succeeded" {
                        guard let result = snapshot.mixStemsResult, result.fileCreated,
                              result.profile == "wav_f32_stereo_44100_v1", result.sampleRate == 44100,
                              result.channels == 2, result.sampleCount > 0,
                              result.includeVocals == includeVocals, result.includeAccompaniment == includeAccompaniment else {
                            throw CheckFailure(message: "混音结果未能确认。")
                        }
                        status = "混音已导出。"; break
                    }
                    if snapshot.state == "failed" || snapshot.state == "cancelled" {
                        if snapshot.error?.code == "CLEANUP_FAILED" { cleanupUnconfirmed = true }
                        if snapshot.state == "failed" { issue = Self.message(snapshot.error?.code, fallback: snapshot.error?.message) }
                        status = snapshot.state == "cancelled" ? "已取消混音导出。" : "混音导出未完成。"
                        break
                    }
                    try await Task.sleep(for: .milliseconds(100))
                }
                jobID = nil
            }
            if !cleanupUnconfirmed { try await bridge.close() }
        } catch {
            if let rejected = error as? CoreSubmissionRejection {
                if rejected.failure.code == "CLEANUP_FAILED" { cleanupUnconfirmed = true }
                issue = Self.message(rejected.failure.code, fallback: rejected.failure.message)
            } else { issue = error.localizedDescription }
            status = "混音导出未完成。"
            if !cleanupUnconfirmed {
                do { try await bridge.close() }
                catch { cleanupUnconfirmed = true }
            }
        }
        if cleanupUnconfirmed {
            issue = "混音导出清理尚未确认，已保留文件访问并停止新任务。请保留当前窗口。"
            // Retain both scopes until cleanup is independently confirmed.
            mixSourceAccess = sourceAccess
        } else { sourceAccess.release(); access.release(); exportAccess = nil }
        isMixExporting = false; isExporting = false; isRunning = false
    }
    func export(to destination: URL) async {
        guard canExport, let resultDirectory else { return }
        await export(to: destination, resultDirectory: resultDirectory,
                     access: SecurityAccess(destination.deletingLastPathComponent()))
    }
    private func export(to destination: URL, resultDirectory: URL, access: SecurityAccess) async {
        guard !stopping, !isRunning, !cleanupUnconfirmed else { access.release(); return }
        isRunning = true; isExporting = true; issue = nil; status = "正在导出…"
        exportAccess = access
        do {
            try await Self.publish(resultDirectory, to: destination); status = "两条音轨已导出。"
            if let historyID { recordHistory { try RecentTaskLedger.shared.recordAIExport(historyID, directory: destination) } }
        }
        catch let failure as AIExportCleanupFailure {
            cleanupUnconfirmed = true; exportCleanupDirectory = failure.directory
            issue = "导出清理尚未确认，已保留目标文件访问并停止新任务。请保留当前窗口。"
            status = "导出未完成，不能确认已安全清理。"
        }
        catch { issue = "导出未完成：\(error.localizedDescription)"; status = "请更换目标后重试。" }
        if !cleanupUnconfirmed { access.release(); exportAccess = nil }
        isExporting = false; isRunning = false
    }
    func beginShutdown() {
        stopping = true; cancelRequested = true; status = "正在停止任务并清理…"
    }
    func shutdown() async throws {
        beginShutdown()
        if isRunning { await cancel() }
        while isRunning || isConfiguring || isRestoringComponents { try await Task.sleep(for: .milliseconds(50)) }
        guard !cleanupUnconfirmed else { throw CheckFailure(message: "分离任务清理尚未确认。") }
        try await audition.stop()
        try await bridge.close()
        if let root = workspace { try await Self.remove(root); workspace = nil }
        resultDirectory = nil
    }
    private static func message(_ code: String?, fallback: String?) -> String {
        switch code {
        case "DEPENDENCY_MISSING": return "分离组件缺失或不匹配，请重新配置。"
        case "MODEL_INVALID": return "模型文件不匹配，请选择已审核的模型。"
        case "UNSUPPORTED_FORMAT", "INVALID_INPUT": return "音频格式或内容不受支持，请检查后重试。"
        case "OUTPUT_VALIDATION_FAILED": return "音轨或导出结果未通过完整性检查，请重新分离后重试。"
        case "OUTPUT_EXISTS": return "目标文件已存在，请更换名称；原文件未被覆盖。"
        case "OUT_OF_DISK", "RESOURCE_LIMIT": return "空间或处理资源不足，请检查后重试。"
        case "CLEANUP_FAILED": return "任务清理尚未确认，请保留当前窗口。"
        default: return fallback ?? "任务未能完成，请检查音频和组件配置。"
        }
    }
    private static func saveBookmark(_ url: URL, key: String) -> Bool {
        do {
            let data = try NativeHeartbeat.shared.measure(.aiBookmark) {
                try url.bookmarkData(options: .withSecurityScope, includingResourceValuesForKeys: nil, relativeTo: nil)
            }
            UserDefaults.standard.set(data, forKey: key)
            return true
        } catch {
            UserDefaults.standard.removeObject(forKey: key)
            return false
        }
    }
    private static func restoreBookmark(_ key: String, stale: inout Bool) -> URL? {
        guard let data = UserDefaults.standard.data(forKey: key) else { return nil }
        do {
            var isStale = false
            let url = try URL(resolvingBookmarkData: data, options: .withSecurityScope, relativeTo: nil, bookmarkDataIsStale: &isStale)
            guard !isStale else { UserDefaults.standard.removeObject(forKey: key); stale = true; return nil }
            return url
        } catch {
            UserDefaults.standard.removeObject(forKey: key); stale = true; return nil
        }
    }
    private static func makeWorkspace() throws -> URL {
        let temporaryPath = FileManager.default.temporaryDirectory.path
        guard let resolved = realpath(temporaryPath, nil) else {
            throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno))
        }
        defer { free(resolved) }
        let base = URL(fileURLWithPath: String(cString: resolved), isDirectory: true)
        let root = base.appendingPathComponent("mediastudio-ai-" + UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        return root
    }
    private static func copy(_ source: URL, to destination: URL) async throws {
        try await Task.detached { try FileManager.default.copyItem(at: source, to: destination) }.value
    }
    private static func remove(_ url: URL) async throws {
        try await Task.detached { try FileManager.default.removeItem(at: url) }.value
    }
    private static func publish(_ source: URL, to destination: URL) async throws {
        try await Task.detached {
            let manager = FileManager.default
            let stage = destination.deletingLastPathComponent().appendingPathComponent(".mediastudio-export-" + UUID().uuidString)
            try manager.createDirectory(at: stage, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
            do {
                for name in ["vocals.wav", "accompaniment.wav"] {
                    try manager.copyItem(at: source.appendingPathComponent(name), to: stage.appendingPathComponent(name))
                }
                let rc = stage.path.withCString { from in destination.path.withCString { to in renamex_np(from, to, UInt32(RENAME_EXCL)) } }
                guard rc == 0 else { throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno)) }
            } catch {
                let original = error
                do { try manager.removeItem(at: stage) }
                catch { throw AIExportCleanupFailure(directory: stage) }
                throw original
            }
        }.value
    }
}
