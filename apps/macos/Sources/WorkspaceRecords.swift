import Foundation

struct WorkspaceAsset: Identifiable, Sendable {
    let id: UUID
    let identity: String
    let url: URL
    var state = "waiting"
    var info: MediaInfo?
    var failure: String?
    var startText = "0.000000"
    var endText = "0.000000"
    var mode: ExportStrategy
    var audioSelection: AudioSelection = .all
    var includedInBatch = true
}
struct ExportDraft: Codable, Sendable {
    let assetId: UUID
    let sourceURL: URL
    let sourceName: String
    let range: MediaRange
    let mode: ExportStrategy
    let configuration: BackendConfiguration
    var audioSelection: AudioSelection = .all
    var expectedAudioIndices: [Int]?
}
struct ActiveExport: Codable, Sendable {
    let id: UUID
    let draft: ExportDraft
    let destinationURL: URL
    var coreJobId: String?
}
struct WorkspaceTask: Identifiable, Codable, Sendable {
    let id: UUID
    let sourceAssetId: UUID
    let sourceName: String
    let destinationName: String
    let mode: ExportStrategy
    let requestedRange: MediaRange
    let createdAt: Date
    var coreJobId: String?
    var state: String
    var actualRange: MediaRange?
    var failure: JobFailure?
    var result: JobResult?
    var destinationBookmark: Data?
    var interrupted = false
    var sessionId: UUID?
    var audioSelection: AudioSelection?

    // Persistence excludes the native handle, whole Core response and raw paths.
    func summary() -> WorkspaceTask {
        var value = self
        value.result = nil
        return value
    }
    var terminal: Bool { ["succeeded", "failed", "cancelled", "interrupted", "unconfirmed"].contains(state) }
}

@MainActor
final class RecentTaskStore {
    private let path: URL?
    private let defaults: UserDefaults
    private let key = "MediaStudio.mac002.recentTasks.v1"
    init(path: URL? = nil, defaults: UserDefaults = .standard) { self.path = path; self.defaults = defaults }
    func load() -> [WorkspaceTask] {
        let data: Data?
        if let path {
            let values = try? path.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey])
            guard values?.isRegularFile == true, values?.isSymbolicLink != true,
                  let size = values?.fileSize, size <= 2_000_000 else { return [] }
            data = try? Data(contentsOf: path)
        } else { data = defaults.data(forKey: key) }
        guard let data, data.count <= 2_000_000,
              let values = try? JSONDecoder().decode([WorkspaceTask].self, from: data) else { return [] }
        var ids: Set<UUID> = []
        return values.prefix(100).filter { ids.insert($0.id).inserted }.map { value in
            var item = value.summary()
            if !item.terminal { item.state = "interrupted"; item.interrupted = true }
            return item
        }
    }
    func save(_ tasks: [WorkspaceTask]) throws {
        let values = tasks.prefix(100).map { $0.summary() }
        let data = try JSONEncoder().encode(values)
        guard data.count <= 2_000_000 else { throw CheckFailure(message: "最近任务摘要超出保存上限。") }
        if let path {
            let parent = path.deletingLastPathComponent()
            let keys: Set<URLResourceKey> = [.isRegularFileKey, .isSymbolicLinkKey]
            if FileManager.default.fileExists(atPath: path.path) {
                let info = try path.resourceValues(forKeys: keys)
                guard info.isRegularFile == true, info.isSymbolicLink != true else { throw CheckFailure(message: "最近任务路径不是普通文件。") }
            }
            guard !parent.resolvingSymlinksInPath().path.isEmpty else { throw CheckFailure(message: "最近任务目录无效。") }
            try data.write(to: path, options: .atomic)
        } else { defaults.set(data, forKey: key) }
    }
    static func bookmark(_ url: URL) -> Data? {
        (try? url.bookmarkData(options: .withSecurityScope, includingResourceValuesForKeys: nil, relativeTo: nil))
            ?? (try? url.bookmarkData(options: .minimalBookmark, includingResourceValuesForKeys: nil, relativeTo: nil))
    }
    static func resolve(_ data: Data?) -> URL? {
        guard let data else { return nil }
        var stale = false
        guard let url = try? URL(resolvingBookmarkData: data, options: [.withoutUI, .withSecurityScope],
                                 relativeTo: nil, bookmarkDataIsStale: &stale), !stale else { return nil }
        return url
    }
}
