import Combine
import Darwin
import Foundation

struct AIHistoryRecord: Identifiable, Codable, Sendable {
    let id: UUID
    let sourceName: String
    let createdAt: Date
    var state: String
    var interrupted = false
    var failure: JobFailure?
    var resultDirectoryName: String?
    var resultBookmark: Data?
    var terminal: Bool { ["succeeded", "failed", "cancelled", "interrupted", "unconfirmed"].contains(state) }
}

private struct RecentHistoryDocument: Codable {
    var schemaVersion = 2
    var videos: [WorkspaceTask] = []
    var ai: [AIHistoryRecord] = []
}

/// Native, bounded local summaries. No engine, pointer or recoverable Core job.
@MainActor
final class RecentTaskLedger: ObservableObject {
    static let shared = RecentTaskLedger()
    @Published private(set) var videoTasks: [WorkspaceTask] = []
    @Published private(set) var aiTasks: [AIHistoryRecord] = []
    @Published private(set) var persistenceIssue: String?
    let videoIDsDidChange = PassthroughSubject<Set<UUID>, Never>()
    private let path: URL
    private let defaults: UserDefaults
    private let legacyKey = "MediaStudio.mac002.recentTasks.v1"
    private var legacyMigrationPending = false
    private var loadFailed = false
    private var lastSuccessfulBytes: Data?
    private struct FileIdentity: Equatable {
        let device: Int64
        let inode: UInt64
        let size: Int64
        let modifiedSeconds: Int64
        let modifiedNanoseconds: Int64
    }
    private var lastSuccessfulFile: FileIdentity?
    private(set) var activeResultAccesses = 0
    private static let states: Set<String> = ["queued", "running", "cancelling", "cancelled", "succeeded", "failed", "interrupted", "unconfirmed"]

    init(path: URL? = nil, defaults: UserDefaults = .standard) {
        self.defaults = defaults
        self.path = path ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("MediaStudio", isDirectory: true).appendingPathComponent("recent-history-v2.json")
        var document = RecentHistoryDocument()
        do {
            if FileManager.default.fileExists(atPath: self.path.path) || (try? self.path.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink) == true {
                let metadata = try self.path.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey])
                guard metadata.isRegularFile == true, metadata.isSymbolicLink != true,
                      let size = metadata.fileSize, size <= 2_000_000 else { throw CheckFailure(message: "invalid history file") }
                let bytes = try Data(contentsOf: self.path)
                guard bytes.count <= 2_000_000 else { throw CheckFailure(message: "history bound") }
                document = try JSONDecoder().decode(RecentHistoryDocument.self, from: bytes)
                guard document.schemaVersion == 2 else { throw CheckFailure(message: "history schema") }
            } else if let bytes = defaults.data(forKey: legacyKey) {
                guard bytes.count <= 2_000_000 else { throw CheckFailure(message: "legacy history bound") }
                document.videos = try JSONDecoder().decode([WorkspaceTask].self, from: bytes)
                legacyMigrationPending = true
            }
            document.videos = document.videos.map { value in
                var item = value.summary()
                if !item.terminal { item.state = "interrupted"; item.interrupted = true }
                return item
            }
            document.ai = document.ai.map { value in
                var item = value
                if !item.terminal { item.state = "interrupted"; item.interrupted = true }
                return item
            }
            document = try Self.bounded(document)
            videoTasks = document.videos; aiTasks = document.ai
        } catch {
            loadFailed = true; persistenceIssue = "最近任务摘要无法读取，请检查本机存储，或明确清除旧记录后重试。"
        }
    }

    private static func bounded(_ input: RecentHistoryDocument) throws -> RecentHistoryDocument {
        var document = input
        var videoIDs: Set<UUID> = [], aiIDs: Set<UUID> = []
        document.videos = document.videos.filter { videoIDs.insert($0.id).inserted }.map { $0.summary() }
        document.ai = document.ai.filter { aiIDs.insert($0.id).inserted }
        let activeVideos = document.videos.filter { !$0.terminal }
        let activeAI = document.ai.filter { !$0.terminal }
        let active = activeVideos.count + activeAI.count
        guard active <= 100 else { throw CheckFailure(message: "活动任务摘要已达100条，请等待处理结束后重试。") }
        let terminal = document.videos.filter(\.terminal).map { ("v:" + $0.id.uuidString, $0.createdAt) }
            + document.ai.filter(\.terminal).map { ("a:" + $0.id.uuidString, $0.createdAt) }
        let keep = Set(terminal.sorted { $0.1 == $1.1 ? $0.0 < $1.0 : $0.1 > $1.1 }.prefix(100 - active).map(\.0))
        document.videos.removeAll { $0.terminal && !keep.contains("v:" + $0.id.uuidString) }
        document.ai.removeAll { $0.terminal && !keep.contains("a:" + $0.id.uuidString) }
        document.videos.sort { $0.createdAt > $1.createdAt }
        document.ai.sort { $0.createdAt > $1.createdAt }
        return document
    }

    private func currentMetadata(_ url: URL) throws -> stat? {
        var info = stat()
        let result = url.withUnsafeFileSystemRepresentation { pointer -> Int32 in
            guard let pointer else { return -1 }
            return lstat(pointer, &info)
        }
        if result == 0 { return info }
        if errno == ENOENT { return nil }
        throw CheckFailure(message: "history file metadata unavailable")
    }
    private func currentFileIdentity() -> FileIdentity? {
        guard let info = try? currentMetadata(path), info.st_mode & mode_t(S_IFMT) == mode_t(S_IFREG) else { return nil }
        return FileIdentity(device: Int64(info.st_dev), inode: UInt64(info.st_ino), size: Int64(info.st_size),
                            modifiedSeconds: Int64(info.st_mtimespec.tv_sec), modifiedNanoseconds: Int64(info.st_mtimespec.tv_nsec))
    }

    private func commit(_ input: RecentHistoryDocument, allowReplacingInvalid: Bool = false) throws {
        guard !loadFailed || allowReplacingInvalid else { throw CheckFailure(message: persistenceIssue ?? "最近任务摘要暂不可用。") }
        let document = try Self.bounded(input)
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        let bytes = try NativeHeartbeat.shared.measure(.historyEncode) { try encoder.encode(document) }
        guard bytes.count <= 2_000_000 else { throw CheckFailure(message: "最近任务摘要超出保存上限。") }
        let parent = path.deletingLastPathComponent()
        // Queue polling often repeats an unchanged full summary. Only a write
        // confirmed by this instance may suppress IO; explicit clear must run.
        if !allowReplacingInvalid, !loadFailed, !legacyMigrationPending,
           persistenceIssue == nil, lastSuccessfulBytes == bytes,
           let directory = try? currentMetadata(parent), directory.st_mode & mode_t(S_IFMT) == mode_t(S_IFDIR),
           let identity = currentFileIdentity(), identity == lastSuccessfulFile { return }
        do {
            try FileManager.default.createDirectory(at: parent, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            guard let directory = try currentMetadata(parent), directory.st_mode & mode_t(S_IFMT) == mode_t(S_IFDIR) else { throw CheckFailure(message: "invalid history directory") }
            if let metadata = try currentMetadata(path) {
                guard metadata.st_mode & mode_t(S_IFMT) == mode_t(S_IFREG) else { throw CheckFailure(message: "invalid history target") }
            }
            try NativeHeartbeat.shared.measure(.historyAtomicWrite) { try bytes.write(to: path, options: .atomic) }
            guard let identity = currentFileIdentity(), identity.size == Int64(bytes.count) else { throw CheckFailure(message: "history file changed after write") }
            if legacyMigrationPending { defaults.removeObject(forKey: legacyKey); legacyMigrationPending = false }
            lastSuccessfulBytes = bytes; lastSuccessfulFile = identity
            loadFailed = false; persistenceIssue = nil
            videoTasks = document.videos; aiTasks = document.ai
            videoIDsDidChange.send(Set(videoTasks.map(\.id)))
        } catch {
            let message = "最近任务摘要未能保存，原记录仍保留；请检查本机存储后重试。"
            persistenceIssue = message
            throw CheckFailure(message: message)
        }
    }

    func saveVideos(_ values: [WorkspaceTask]) throws {
        try commit(RecentHistoryDocument(videos: values.map { $0.summary() }, ai: aiTasks))
    }
    func clearTerminated() throws {
        try commit(RecentHistoryDocument(videos: videoTasks.filter { !$0.terminal }, ai: aiTasks.filter { !$0.terminal }), allowReplacingInvalid: true)
    }
    func clearTerminated(keepingVideos values: [WorkspaceTask]) throws {
        try commit(RecentHistoryDocument(videos: values.map { $0.summary() }, ai: aiTasks.filter { !$0.terminal }), allowReplacingInvalid: true)
    }
    func beginAI(sourceName: String) throws -> UUID {
        guard !sourceName.isEmpty, sourceName.count <= 255, !sourceName.contains("/"), !sourceName.contains("\0") else { throw CheckFailure(message: "分离摘要文件名无效。") }
        let item = AIHistoryRecord(id: UUID(), sourceName: sourceName, createdAt: Date(), state: "queued")
        try commit(RecentHistoryDocument(videos: videoTasks, ai: [item] + aiTasks))
        return item.id
    }
    func updateAI(_ id: UUID, state: String, failure: JobFailure? = nil) throws {
        guard Self.states.contains(state), let index = aiTasks.firstIndex(where: { $0.id == id }) else { throw CheckFailure(message: "这条分离摘要已不可用。") }
        var values = aiTasks; values[index].state = state; values[index].failure = failure
        values[index].interrupted = state == "interrupted"
        try commit(RecentHistoryDocument(videos: videoTasks, ai: values))
    }

    private func validateAIResultDirectory(_ directory: URL) throws {
        let metadata = try directory.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
        guard metadata.isDirectory == true, metadata.isSymbolicLink != true,
              FileManager.default.isReadableFile(atPath: directory.path) else { throw CheckFailure(message: "请选择原分离结果文件夹。") }
        for name in ["vocals.wav", "accompaniment.wav"] {
            let file = directory.appendingPathComponent(name)
            let info = try file.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey])
            guard info.isRegularFile == true, info.isSymbolicLink != true, (info.fileSize ?? 0) > 0,
                  FileManager.default.isReadableFile(atPath: file.path) else { throw CheckFailure(message: "结果文件夹缺少可读取的人声或伴奏文件，请重新选择。") }
        }
    }
    func recordAIExport(_ id: UUID, directory: URL) throws {
        guard let index = aiTasks.firstIndex(where: { $0.id == id }), aiTasks[index].state == "succeeded" else { throw CheckFailure(message: "这条分离摘要已不可用。") }
        let access = SecurityAccess(directory); activeResultAccesses += 1
        defer { access.release(); activeResultAccesses -= 1 }
        do { try validateAIResultDirectory(directory) }
        catch { throw CheckFailure(message: "结果文件夹不可读取，请重新授权或重新选择文件夹。") }
        guard let bookmark = RecentTaskStore.bookmark(directory) else { throw CheckFailure(message: "结果文件夹授权未能保存，请重新选择。") }
        var values = aiTasks
        values[index].resultDirectoryName = directory.lastPathComponent; values[index].resultBookmark = bookmark
        try commit(RecentHistoryDocument(videos: videoTasks, ai: values))
    }
    func reauthorizeAIResult(_ id: UUID, directory: URL) throws {
        guard let item = aiTasks.first(where: { $0.id == id }), item.state == "succeeded",
              let expectedName = item.resultDirectoryName, expectedName == directory.lastPathComponent else { throw CheckFailure(message: "请选择这条分离记录原来的结果文件夹。") }
        try recordAIExport(id, directory: directory)
    }
    /// The callback runs synchronously while the same validated directory scope is held.
    func withAIResultDirectory<T>(_ id: UUID, _ action: (URL) -> T) -> T? {
        guard let item = aiTasks.first(where: { $0.id == id }), item.state == "succeeded",
              let directory = RecentTaskStore.resolve(item.resultBookmark), directory.lastPathComponent == item.resultDirectoryName else { return nil }
        let access = SecurityAccess(directory); activeResultAccesses += 1
        defer { access.release(); activeResultAccesses -= 1 }
        guard (try? validateAIResultDirectory(directory)) != nil else { return nil }
        return action(directory)
    }
}
