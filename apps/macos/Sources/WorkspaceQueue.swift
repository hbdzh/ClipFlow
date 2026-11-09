import AppKit
import Foundation

// Main-actor records own immutable request snapshots and security scope leases.
// Core alone owns queue capacity, scheduling and the media plan.
struct PendingQueueSubmission {
    let token: String
    let exports: [ActiveExport]
    let retryCoreID: String?
}

extension WorkspaceModel {
    var queuedTasks: [WorkspaceTask] {
        let ids = queueSnapshot?.queuedJobIds ?? []
        return ids.compactMap { id in tasks.first { $0.sessionId == queueSessionId && $0.coreJobId == id } }
    }
    var displayedTasks: [WorkspaceTask] {
        let running = tasks.filter { $0.sessionId == queueSessionId && $0.coreJobId == queueSnapshot?.runningJobId && !$0.terminal }
        let ordered = running + queuedTasks
        let included = Set(ordered.map(\.id))
        return ordered + tasks.filter { !included.contains($0.id) }
    }
    var batchCount: Int { assets.filter { $0.includedInBatch }.count }
    var canSubmitBatch: Bool { queueEnabled && !isSubmitting && pendingQueue == nil && !stopping && !unconfirmedCleanup && batchCount > 0 }
    func toggleBatchAsset(_ id: UUID) {
        guard let i = assets.firstIndex(where: { $0.id == id }) else { return }
        assets[i].includedInBatch.toggle()
    }
    func queueDraft(asset: WorkspaceAsset) -> ExportDraft? {
        guard asset.info != nil, let start = parseTimeText(asset.startText), let end = parseTimeText(asset.endText),
              start < end else { return nil }
        return ExportDraft(assetId: asset.id, sourceURL: asset.url, sourceName: asset.url.lastPathComponent,
            range: MediaRange(startUs: start, endUs: end), mode: asset.mode, configuration: backendConfiguration,
            audioSelection: asset.mode.transcodePreset == nil ? .all : asset.audioSelection,
            expectedAudioIndices: asset.info?.streams.filter { $0.codecType == "audio" }.map(\.index))
    }
    func submitBatch(assetIDs: [UUID], destinations: [URL]) async {
        guard assetIDs.count == destinations.count else { issue = "每份素材需要一个目标。"; return }
        saveSelectedEdits()
        let drafts = assetIDs.compactMap { id in assets.first(where: { $0.id == id }).flatMap(queueDraft) }
        guard drafts.count == assetIDs.count else {
            issue = assetIDs.compactMap { id in assets.first(where: { $0.id == id }) }.filter { queueDraft(asset: $0) == nil }.map { "\($0.url.lastPathComponent)：\($0.failure ?? "尚未探测成功或区间无效")" }.joined(separator: "\n") + "\n本批未提交。"
            return
        }
        await submitQueue(drafts: drafts, destinations: destinations)
    }
    func queueRequest(_ value: ActiveExport) -> QueueMediaRequest {
        if let preset = value.draft.mode.transcodePreset {
            return .transcode(source: value.draft.sourceURL, destination: value.destinationURL,
                range: value.draft.range, preset: preset, audio: value.draft.audioSelection)
        }
        return .trim(source: value.draft.sourceURL, destination: value.destinationURL,
            range: value.draft.range, strategy: value.draft.mode)
    }
    func releaseQueueLeases(_ id: UUID) {
        for lease in queueLeases.removeValue(forKey: id) ?? [] { release(lease) }
    }
    func submitQueue(drafts: [ExportDraft], destinations: [URL]) async {
        guard queueEnabled, !isSubmitting, pendingQueue == nil, !stopping, !unconfirmedCleanup,
              !drafts.isEmpty, drafts.count == destinations.count else { return }
        let exports = zip(drafts, destinations).map { ActiveExport(id: UUID(), draft: $0.0, destinationURL: $0.1) }
        // Hold every source and output folder before the synchronous Core preflight.
        for item in exports {
            queueLeases[item.id] = [hold(item.draft.sourceURL), hold(item.destinationURL.deletingLastPathComponent())]
        }
        pendingQueue = PendingQueueSubmission(token: UUID().uuidString, exports: exports, retryCoreID: nil)
        queueCancelRequested = false
        await resolveQueueSubmission()
    }
    func resolveQueueSubmission() async {
        guard let pending = pendingQueue, !isSubmitting, !stopping, !unconfirmedCleanup else { return }
        isSubmitting = true; issue = nil; submissionErrors = []; closed = false
        status = "正在检查本批素材与目标；接收前不会创建排队任务。"
        defer { isSubmitting = false; isExporting = !queueLeases.isEmpty; updatePhase() }
        var submissionAttempted = false
        var acceptedReceipt = false
        do {
            let config = pending.exports[0].draft.configuration
            if queueConfiguration != config {
                guard queueRequests.isEmpty else { throw CheckFailure(message: "队列会话配置已冻结；请安全关闭后重新打开工作区更换组件。") }
                try await exportBridge.close(); queueConfiguration = nil
                await queueSubmissionObserver?("before-connect")
                guard !stopping else { throw CheckFailure(message: "正在关闭，本批尚未提交。") }
                let value = try await exportBridge.connect(backend: config)
                connection = value; queueConfiguration = config
                await queueSubmissionObserver?("before-submit")
                guard !stopping else { throw CheckFailure(message: "正在关闭，本批尚未提交。") }
            }
            guard !stopping else { throw CheckFailure(message: "正在关闭，本批尚未提交。") }
            let reply: QueueControlResponse
            submissionAttempted = true
            if let retryCoreID = pending.retryCoreID {
                reply = try await exportBridge.retry(jobID: retryCoreID, newDestination: pending.exports[0].destinationURL, submissionId: pending.token)
            } else {
                reply = try await exportBridge.batchSubmit(items: pending.exports.map(queueRequest), submissionId: pending.token)
            }
            guard reply.accepted else {
                submissionErrors = reply.errors ?? []
                if submissionErrors.contains(where: { $0.code == "CLEANUP_FAILED" }) {
                    unconfirmedCleanup = true
                    issue = friendlyError(JobFailure(code: "CLEANUP_FAILED", message: ""))
                    status = "本批未接收，但预检清理未确认；已保留请求、会话与文件访问，请保留现场。"
                    return
                }
                issue = submissionErrors.map { error in
                    let name = pending.exports.indices.contains(error.index) ? pending.exports[error.index].draft.sourceName : "本批"
                    return "\(name)：\(friendlyError(JobFailure(code: error.code, message: error.message))) [\(error.code)]"
                }.joined(separator: "\n")
                if issue?.isEmpty != false { issue = "Core 未接收本批任务。" }
                for item in pending.exports { releaseQueueLeases(item.id) }
                pendingQueue = nil; await refreshQueue(); status = "本批未接收，请修正原因后重试。"; return
            }
            acceptedReceipt = true
            guard let ids = reply.jobIds, ids.count == pending.exports.count, Set(ids).count == ids.count else {
                throw CheckFailure(message: "接收回执不完整；保留请求身份与文件访问，等待重新读取原回执。")
            }
            for (item, coreID) in zip(pending.exports, ids) {
                var request = item; request.coreJobId = coreID; queueRequests[item.id] = request
                if !tasks.contains(where: { $0.id == item.id }) {
                    tasks.insert(WorkspaceTask(id: item.id, sourceAssetId: item.draft.assetId,
                        sourceName: item.draft.sourceName, destinationName: item.destinationURL.lastPathComponent,
                        mode: item.draft.mode, requestedRange: item.draft.range, createdAt: Date(), coreJobId: coreID,
                        state: "queued", destinationBookmark: RecentTaskStore.bookmark(item.destinationURL),
                        sessionId: queueSessionId, audioSelection: item.draft.audioSelection), at: 0)
                }
                resultURLs[item.id] = item.destinationURL
            }
            selectedTaskId = pending.exports.first?.id
            pendingQueue = nil; isExporting = true; persistTasks()
            if queueCancelRequested || stopping { for id in ids { try await exportBridge.cancel(id: id) } }
            await refreshQueue()
            beginQueuePolling()
        } catch {
            if !submissionAttempted || (!acceptedReceipt && error is BridgeError && (-9 ... -1).contains((error as? BridgeError)?.code ?? 0)) {
                for item in pending.exports { releaseQueueLeases(item.id) }; pendingQueue = nil
                issue = error.localizedDescription; status = "本批未接收，请修正原因后重试。"; return
            }
            if acceptedReceipt && pendingQueue == nil {
                issue = "任务已接收，后续控制尚未确认：\(error.localizedDescription)"
                beginQueuePolling(); return
            }
            // The call may have been accepted before decoding failed. Do not
            // create a new token, release borrowed URLs or abandon the engine.
            issue = "提交结果尚未确认：\(error.localizedDescription) 可重新读取原回执，或安全关闭。"
            status = "已保留本批请求与访问；再次读取使用同一提交身份。"
        }
    }
    func beginQueuePolling() {
        guard queuePollTask == nil, !stopping else { return }
        queuePollTask = Task { @MainActor [self] in
            defer { queuePollTask = nil }
            while !stopping && !Task.isCancelled {
                await refreshQueue()
                if queueLeases.isEmpty || unconfirmedCleanup { break }
                do { try await Task.sleep(for: .milliseconds(80)) } catch { break }
            }
        }
    }
    func refreshQueue() async {
        guard queueConfiguration != nil, !stopping, !queueRefreshInProgress else { return }
        queueRefreshInProgress = true
        defer { queueRefreshInProgress = false }
        do {
            let value = try await exportBridge.queueSnapshot()
            guard !stopping else { return }
            queueSnapshot = value
            if value.cleanupUnconfirmed { unconfirmedCleanup = true; issue = friendlyError(JobFailure(code: "CLEANUP_FAILED", message: "")) }
            for item in tasks where item.sessionId == queueSessionId && !item.terminal {
                guard let coreID = item.coreJobId else { continue }
                do {
                    let snapshot = try await exportBridge.poll(id: coreID)
                    guard !stopping else { return }
                    observeQueue(snapshot, taskID: item.id)
                    if ["succeeded", "failed", "cancelled"].contains(snapshot.state) {
                        if snapshot.error?.code == "CLEANUP_FAILED" { unconfirmedCleanup = true }
                        if !unconfirmedCleanup { releaseQueueLeases(item.id) }
                    }
                } catch let error as BridgeError where error.code == -6 {
                    // Bounded Core history may evict a terminal result before a
                    // slow observer reads it. Preserve honest interruption.
                    if let i = tasks.firstIndex(where: { $0.id == item.id }) {
                        tasks[i].state = "interrupted"; tasks[i].interrupted = true
                        tasks[i].failure = JobFailure(code: "NOT_FOUND", message: "Core记录已清除或过期，结果未独立确认。")
                    }
                    if !value.cleanupUnconfirmed && !value.queuedJobIds.contains(coreID) && value.runningJobId != coreID { releaseQueueLeases(item.id) }
                }
            }
            if queueLeases.isEmpty && value.activeCount > 0 && pendingQueue == nil {
                let latest = try await exportBridge.queueSnapshot()
                guard !stopping else { return }; queueSnapshot = latest
            }
            isExporting = !queueLeases.isEmpty; updatePhase(); trimQueueHistory(); persistTasks()
        } catch { guard !stopping else { return }; issue = "队列状态暂未确认：\(error.localizedDescription)" }
    }
    func observeQueue(_ value: JobSnapshot, taskID: UUID) {
        observe(value, exportId: taskID)
        guard value.state == "succeeded", let request = queueRequests[taskID] else { return }
        let result = value.result
        var matches = result?.exportStrategy == request.draft.mode
            && result?.requestedRangeUs?.startUs == request.draft.range.startUs
            && result?.requestedRangeUs?.endUs == request.draft.range.endUs
        if request.draft.mode.transcodePreset != nil {
            let actual = result?.transcodePlan?.selectedSourceAudioStreamIndices
            let expected: [Int]?
            switch request.draft.audioSelection.mode {
            case "none": expected = []
            case "one": expected = request.draft.audioSelection.streamIndex.map { [$0] }
            default: expected = request.draft.expectedAudioIndices
            }
            matches = matches && expected != nil && actual == expected
        }
        if !matches, let index = tasks.firstIndex(where: { $0.id == taskID }) {
            tasks[index].state = "unconfirmed"; tasks[index].result = nil
            tasks[index].failure = JobFailure(code: "OUTPUT_CONTRACT_MISMATCH", message: "Core结果与本项冻结请求不一致，未确认可用结果。")
            issue = "输出结果与原请求不一致，未交付可打开结果；请保留现场检查。"
        }
    }
    func trimQueueHistory() {
        if tasks.count > 100 {
            let excess = tasks.count - 100
            let removed = Set(tasks.reversed().filter { $0.terminal }.prefix(excess).map(\.id))
            tasks.removeAll { removed.contains($0.id) }
            for id in removed { queueRequests.removeValue(forKey: id); resultURLs.removeValue(forKey: id) }
        }
    }
    func awaitQueue() async {
        while (!queueLeases.isEmpty || isSubmitting) && !unconfirmedCleanup && !stopping && pendingQueue == nil {
            await refreshQueue()
            try? await Task.sleep(for: .milliseconds(40))
        }
    }
    func cancelQueue(taskID: UUID? = nil) async {
        if pendingQueue != nil && taskID == nil { queueCancelRequested = true; status = "已记录取消；收到本批真实任务身份后将请求 Core 取消。" }
        let ids = tasks.filter { $0.sessionId == queueSessionId && !$0.terminal && (taskID == nil || $0.id == taskID) }.compactMap(\.coreJobId)
        for id in ids { do { try await exportBridge.cancel(id: id) } catch { issue = error.localizedDescription } }
        await refreshQueue()
    }
    func reorderQueue(taskIDs: [UUID]) async {
        guard !isSubmitting, !stopping, !unconfirmedCleanup else { return }
        let ids = taskIDs.compactMap { id in tasks.first(where: { $0.id == id && $0.sessionId == queueSessionId })?.coreJobId }
        do {
            let response = try await exportBridge.reorderQueue(jobIDs: ids)
            if !response.accepted { issue = "队列已变化，Core 未接受排序；已重新读取实际顺序。" }
        } catch { issue = "排序未被确认：\(error.localizedDescription)" }
        await refreshQueue()
    }
    func moveQueuedTask(_ id: UUID, offset: Int) async {
        var order = queuedTasks.map(\.id)
        guard let index = order.firstIndex(of: id), order.indices.contains(index + offset) else { return }
        order.swapAt(index, index + offset); await reorderQueue(taskIDs: order)
    }
    func retryTask(_ id: UUID, destination: URL) async {
        guard pendingQueue == nil, !isSubmitting, !stopping, !unconfirmedCleanup,
              let old = tasks.first(where: { $0.id == id && $0.sessionId == queueSessionId }),
              ["failed", "cancelled"].contains(old.state), let coreID = old.coreJobId,
              let request = queueRequests[id], assets.contains(where: { $0.id == request.draft.assetId }) else { issue = "这条记录不可重试。历史摘要需重新导入素材后手动提交。"; return }
        let new = ActiveExport(id: UUID(), draft: request.draft, destinationURL: destination)
        queueLeases[new.id] = [hold(new.draft.sourceURL), hold(destination.deletingLastPathComponent())]
        pendingQueue = PendingQueueSubmission(token: UUID().uuidString, exports: [new], retryCoreID: coreID)
        queueCancelRequested = false
        await resolveQueueSubmission()
    }
    func clearQueueHistory() async {
        guard !isSubmitting, pendingQueue == nil, !stopping else { return }
        if queueConfiguration != nil {
            do {
                let reply = try await exportBridge.clearHistory()
                guard reply.accepted else { issue = "Core 未确认清除历史。"; return }
            } catch { issue = error.localizedDescription; return }
        }
        let ids = Set(tasks.filter(\.terminal).map(\.id))
        tasks.removeAll { ids.contains($0.id) }
        for id in ids { queueRequests.removeValue(forKey: id); resultURLs.removeValue(forKey: id) }
        persistTasks(); await refreshQueue(); status = "已清除已完成记录；运行与排队任务保留。"
    }
    func chooseBatchOutput() {
        guard canSubmitBatch else { return }
        saveSelectedEdits()
        let selected = assets.filter { $0.includedInBatch }
        let frozenDrafts = selected.compactMap(queueDraft)
        guard frozenDrafts.count == selected.count else { issue = selected.filter { queueDraft(asset: $0) == nil }.map { "\($0.url.lastPathComponent)：\($0.failure ?? "尚未探测成功或区间无效")" }.joined(separator: "\n") + "\n本批未提交。"; return }
        let panel = NSOpenPanel(); panel.title = "选择批量输出文件夹"; panel.canChooseFiles = false
        panel.canChooseDirectories = true; panel.canCreateDirectories = true
        panel.message = "每项使用新的独立 MP4 文件名；已有文件不会替换。"
        panel.begin { [weak self] response in
            guard response == .OK, let folder = panel.url else { return }
            Task { @MainActor in
                guard let self else { return }
                let destinations = selected.enumerated().map { index, item in
                    folder.appendingPathComponent(item.url.deletingPathExtension().lastPathComponent + "-导出-\(index + 1)-\(UUID().uuidString.prefix(8)).mp4")
                }
                await self.submitQueue(drafts: frozenDrafts, destinations: destinations)
            }
        }
    }
    func chooseRetryOutput(_ id: UUID) {
        guard let item = tasks.first(where: { $0.id == id }) else { return }
        let panel = NSSavePanel(); panel.title = "重试并保存为新文件"; panel.allowedContentTypes = [.mpeg4Movie]
        panel.nameFieldStringValue = URL(fileURLWithPath: item.destinationName).deletingPathExtension().lastPathComponent + "-重试-\(UUID().uuidString.prefix(8)).mp4"
        panel.message = "Core 会保留原请求设置并重新检查源文件；原目标和已有文件不会替换。"
        panel.begin { [weak self] response in
            guard response == .OK, let url = panel.url else { return }
            Task { @MainActor in await self?.retryTask(id, destination: url) }
        }
    }
}
