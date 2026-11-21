import AppKit
import Foundation
import SwiftUI

struct StorageVolumePreview: Codable, Sendable, Identifiable {
    let volumeIndex: Int
    let itemIndices: [Int]
    let estimatedOutputBytes: UInt64
    let availableStorageBytes: UInt64
    let requiredStorageBytes: UInt64
    let sufficient: Bool
    var id: Int { volumeIndex }
}
struct StoragePreviewResponse: Codable, Sendable {
    let schemaVersion: Int
    let operation: String
    let ready: Bool
    let volumes: [StorageVolumePreview]
    let errors: [QueueRejection]
}
struct ExportSpacePreview: Identifiable {
    let id: String
    let response: StoragePreviewResponse
    let exports: [ActiveExport]
}

extension WorkspaceModel {
    func prepareStoragePreview() async {
        guard let pending = pendingQueue, pendingQueueNeedsConfirmation,
              !isSubmitting, !stopping, !unconfirmedCleanup else { return }
        isSubmitting = true; issue = nil; closed = false
        status = "正在读取目标卷可用空间与 Core 输出预算；尚未提交任务。"
        defer { isSubmitting = false; updatePhase() }
        do {
            let config = pending.exports[0].draft.configuration
            if queueConfiguration != config {
                guard queueRequests.isEmpty else { throw CheckFailure(message: "队列会话配置已冻结；请安全关闭后重新打开工作区更换组件。") }
                try await exportBridge.close(); queueConfiguration = nil
                guard !stopping else { return }
                connection = try await exportBridge.connect(backend: config)
                queueConfiguration = config
            }
            guard !stopping else { return }
            let response = try await exportBridge.storagePreview(items: pending.exports.map(queueRequest))
            guard !stopping, pendingQueue?.token == pending.token else { return }
            if response.errors.contains(where: { $0.code == "CLEANUP_FAILED" }) {
                unconfirmedCleanup = true
                issue = friendlyError(JobFailure(code: "CLEANUP_FAILED", message: ""))
                status = "空间检查未完成；会话与文件访问已保留，请安全关闭。"
                return
            }
            storagePreview = ExportSpacePreview(id: pending.token, response: response, exports: pending.exports)
            status = response.ready ? "请确认输出空间后再开始导出；尚未提交任务。" : "空间检查未通过，尚未提交任务。"
        } catch {
            guard !stopping else { return }
            discardStoragePreview()
            issue = "空间检查未完成：\(error.localizedDescription)"
            status = "本次未提交，请修正原因或选择其他位置。"
        }
    }
    private func discardStoragePreview() {
        guard pendingQueueNeedsConfirmation, let pending = pendingQueue else { return }
        for item in pending.exports { releaseQueueLeases(item.id) }
        pendingQueue = nil; pendingQueueNeedsConfirmation = false; storagePreview = nil
    }
    func cancelStoragePreview() {
        guard !isSubmitting, !stopping, !unconfirmedCleanup, pendingQueueNeedsConfirmation else { return }
        discardStoragePreview()
        status = "已取消这次导出确认；未提交新任务。"
    }
    func confirmStoragePreview() async {
        guard !isSubmitting, !stopping, !unconfirmedCleanup, pendingQueueNeedsConfirmation,
              let value = storagePreview, value.response.ready, pendingQueue?.token == value.id else { return }
        pendingQueueNeedsConfirmation = false; storagePreview = nil
        await resolveQueueSubmission()
    }
}

struct ExportSpaceConfirmation: View {
    @ObservedObject var model: WorkspaceModel
    let preview: ExportSpacePreview
    private func bytes(_ value: UInt64) -> String {
        ByteCountFormatter.string(fromByteCount: Int64(clamping: value), countStyle: .file)
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("确认导出").font(.title2.weight(.semibold))
            Text("\(preview.exports.count) 项导出 · 确认前不会创建任务或输出文件。")
                .foregroundStyle(.secondary)
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    VStack(alignment: .leading, spacing: 10) {
                        Text("导出项目").font(.headline)
                        ForEach(preview.exports, id: \.id) { item in
                            VStack(alignment: .leading, spacing: 4) {
                                Text(item.draft.sourceName).font(.callout.weight(.medium)).lineLimit(2)
                                Text("\(item.draft.mode.title) · \(timeText(item.draft.range.startUs))–\(timeText(item.draft.range.endUs)) 秒")
                                    .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                                if item.draft.mode.transcodePreset != nil {
                                    Text(item.draft.audioSelection.mode == "none" ? "无音轨" : item.draft.audioSelection.mode == "one" ? "音轨：来源轨道 \(item.draft.audioSelection.streamIndex ?? -1)" : "保留全部支持音轨")
                                        .font(.caption).foregroundStyle(.secondary)
                                }
                                if let transform = item.draft.videoTransform {
                                    Text("画面：\(transform.rotationDegrees)° · 缩放 \(transform.scalePercent)% · 位置 X \(transform.offsetX) / Y \(transform.offsetY) 像素")
                                        .font(.caption).foregroundStyle(.secondary)
                                }
                                Text("输出：\(item.destinationURL.lastPathComponent)")
                                    .font(.caption).foregroundStyle(.secondary).lineLimit(2)
                            }.frame(maxWidth: .infinity, alignment: .leading)
                                .padding(10).background(.quaternary.opacity(0.45), in: RoundedRectangle(cornerRadius: 9))
                        }
                    }
                    Divider()
                    ForEach(preview.response.volumes) { volume in
                        VStack(alignment: .leading, spacing: 7) {
                            Text("输出位置 \(volume.volumeIndex + 1)").font(.headline)
                            ForEach(volume.itemIndices, id: \.self) { index in
                                if preview.exports.indices.contains(index) {
                                    Text(preview.exports[index].destinationURL.deletingLastPathComponent().path).font(.caption).textSelection(.enabled)
                                }
                            }
                            LabeledContent("目标卷当前可用", value: bytes(volume.availableStorageBytes))
                                .accessibilityIdentifier("storage-available-\(volume.volumeIndex)")
                            LabeledContent("保守输出空间预算", value: bytes(volume.estimatedOutputBytes))
                                .accessibilityIdentifier("storage-output-budget-\(volume.volumeIndex)")
                            LabeledContent("所需可用空间（含预留与清理余量）", value: bytes(volume.requiredStorageBytes))
                                .accessibilityIdentifier("storage-required-\(volume.volumeIndex)")
                            if !volume.sufficient { Text("目标卷空间不足，请返回并选择其他位置。").foregroundStyle(.orange) }
                        }
                    }
                    ForEach(Array(preview.response.errors.enumerated()), id: \.offset) { _, error in
                        Text("\(model.friendlyError(JobFailure(code: error.code, message: error.message))) [\(error.code)]").foregroundStyle(.orange)
                    }
                }.padding(.trailing, 8)
            }.frame(maxHeight: 330)
            Text("预算由 Core 计算，不是成品大小预测或成功保证。确认后会重新预检；其他程序仍可能占用空间。")
                .font(.callout).foregroundStyle(.secondary)
            HStack {
                Button("返回并选择其他位置") { model.cancelStoragePreview() }.keyboardShortcut(.cancelAction)
                Spacer()
                Button("确认并开始导出") { Task { await model.confirmStoragePreview() } }
                    .keyboardShortcut(.defaultAction)
                    .disabled(!preview.response.ready || model.isSubmitting || model.stopping)
                    .accessibilityIdentifier("confirm-storage-export")
            }
        }.padding(24).frame(width: 660)
    }
}
