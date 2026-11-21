import CryptoKit
import Darwin
import Foundation

enum AIComponentStoreError: Error, LocalizedError, Sendable {
    case invalidSelection(String)
    case integrityMismatch(String)
    case cacheUnavailable(String)
    case cleanupFailed(stage: URL, message: String)
    var errorDescription: String? {
        switch self {
        case .invalidSelection(let text), .integrityMismatch(let text), .cacheUnavailable(let text): return text
        case .cleanupFailed(_, let text): return text
        }
    }
}

/// A data-only, serial background actor. No worker/runtime copying or execution.
actor AIComponentStore {
    struct Selection: Sendable { let graphs: URL; let model: URL; let modelBytes: Int64 }
    static let graphNames = ["pre.onnx", "post.onnx", "lstm-0-forward.onnx", "lstm-0-reverse.onnx", "lstm-1-forward.onnx", "lstm-1-reverse.onnx", "lstm-2-forward.onnx", "lstm-2-reverse.onnx"]
    private struct Digest: Codable, Equatable { let name: String; let bytes: Int64; let sha256: String }
    private struct Record: Codable { let version: Int; let id: String }
    private struct Receipt: Codable { let owner: String; let id: String; let files: [Digest] }
    private struct Identity: Equatable { let device: Int64; let inode: UInt64 }
    private struct InvalidReceipt: Codable { let owner: String; let id: String; let device: Int64; let inode: UInt64 }
    private let root: URL
    private let referenceGraphs: URL
    private let referenceModel: URL
    private let marker = "MediaStudio.AIComponentStore.v1"
    private let maximumBytes: Int64 = 1_000_000_000

    init(root: URL, referenceGraphs: URL, referenceModel: URL) {
        self.root = root; self.referenceGraphs = referenceGraphs; self.referenceModel = referenceModel
    }
    private func metadata(_ url: URL) throws -> stat? {
        guard url.isFileURL, url.path.hasPrefix("/"), !url.path.contains("\0") else { throw AIComponentStoreError.invalidSelection("请选择本机文件或目录。") }
        var value = stat()
        let code = url.withUnsafeFileSystemRepresentation { path -> Int32 in
            guard let path else { return -1 }; return lstat(path, &value)
        }
        if code == 0 { return value }; if errno == ENOENT { return nil }
        throw AIComponentStoreError.cacheUnavailable("无法读取组件数据，请检查权限。")
    }
    private func identity(_ value: stat) -> Identity { Identity(device: Int64(value.st_dev), inode: UInt64(value.st_ino)) }
    private func directory(_ url: URL, owned: Bool = false) throws {
        guard let value = try metadata(url), value.st_mode & mode_t(S_IFMT) == mode_t(S_IFDIR),
              !owned || (value.st_uid == getuid() && value.st_mode & 0o777 == 0o700) else {
            throw AIComponentStoreError.invalidSelection("数据目录不能是链接；缓存目录必须为本用户私有。")
        }
    }
    private func ordinary(_ url: URL, owned: Bool = false) throws -> stat {
        try directory(url.deletingLastPathComponent())
        guard let value = try metadata(url), value.st_mode & mode_t(S_IFMT) == mode_t(S_IFREG),
              !owned || (value.st_uid == getuid() && value.st_mode & 0o777 == 0o600 && value.st_nlink == 1) else {
            throw AIComponentStoreError.invalidSelection("组件数据必须是非链接普通文件；缓存文件必须为本用户私有。")
        }
        return value
    }
    private func ensureRoot() throws {
        let parent = root.deletingLastPathComponent()
        if try metadata(parent) == nil { try FileManager.default.createDirectory(at: parent, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700]) }
        try directory(parent)
        if try metadata(root) == nil { try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700]) }
        try directory(root, owned: true)
    }
    private func fsPath<T>(_ url: URL, _ body: (UnsafePointer<CChar>) throws -> T) throws -> T {
        try url.withUnsafeFileSystemRepresentation { path in
            guard let path else { throw AIComponentStoreError.invalidSelection("组件路径无效。") }; return try body(path)
        }
    }
    private func openRead(_ url: URL) throws -> Int32 {
        _ = try ordinary(url)
        let fd = try fsPath(url) { Darwin.open($0, O_RDONLY | O_NOFOLLOW | O_CLOEXEC) }
        guard fd >= 0 else { throw AIComponentStoreError.invalidSelection("组件数据无法读取，请重新选择或授权。") }; return fd
    }
    private func openNew(_ url: URL) throws -> Int32 {
        try directory(url.deletingLastPathComponent(), owned: true)
        let fd = try fsPath(url) { Darwin.open($0, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, mode_t(0o600)) }
        guard fd >= 0 else { throw AIComponentStoreError.cacheUnavailable("组件缓存无法写入，请检查空间和权限。") }
        guard fchmod(fd, mode_t(0o600)) == 0 else { close(fd); throw AIComponentStoreError.cacheUnavailable("无法建立私有缓存文件。") }; return fd
    }
    private func writeAll(_ data: UnsafeRawBufferPointer, fd: Int32) throws {
        guard let start = data.baseAddress else { return }; var offset = 0
        while offset < data.count {
            let count = Darwin.write(fd, start.advanced(by: offset), data.count - offset)
            if count < 0 && errno == EINTR { continue }
            guard count > 0 else { throw AIComponentStoreError.cacheUnavailable("缓存写入失败，请检查空间和权限。") }; offset += count
        }
    }
    private func digest(_ url: URL, name: String, expected: Digest? = nil, to target: URL? = nil) throws -> Digest {
        let fd = try openRead(url); defer { close(fd) }
        var before = stat()
        guard fstat(fd, &before) == 0, before.st_mode & mode_t(S_IFMT) == mode_t(S_IFREG), before.st_size > 0,
              before.st_size <= maximumBytes, expected == nil || Int64(before.st_size) == expected?.bytes else {
            throw AIComponentStoreError.integrityMismatch("模型或固定图的大小与本包不一致。")
        }
        let output = try target.map(openNew); defer { if let output { close(output) } }
        var hash = SHA256(), buffer = [UInt8](repeating: 0, count: 1_048_576), total: Int64 = 0
        while true {
            let count = buffer.withUnsafeMutableBytes { Darwin.read(fd, $0.baseAddress, $0.count) }
            if count < 0 && errno == EINTR { continue }
            guard count >= 0 else { throw AIComponentStoreError.invalidSelection("组件数据读取失败。") }; if count == 0 { break }
            total += Int64(count)
            guard total <= Int64(before.st_size) else { throw AIComponentStoreError.integrityMismatch("组件在读取期间发生变化。") }
            hash.update(data: Data(buffer[0..<count]))
            if let output { try buffer.withUnsafeBytes { try writeAll(UnsafeRawBufferPointer(rebasing: $0[0..<count]), fd: output) } }
        }
        var after = stat()
        guard fstat(fd, &after) == 0, identity(before) == identity(after), before.st_size == after.st_size,
              before.st_mtimespec.tv_sec == after.st_mtimespec.tv_sec, before.st_mtimespec.tv_nsec == after.st_mtimespec.tv_nsec,
              total == Int64(before.st_size) else { throw AIComponentStoreError.integrityMismatch("组件在读取期间发生变化。") }
        if let output, fsync(output) != 0 { throw AIComponentStoreError.cacheUnavailable("组件缓存未确认写入。") }
        let value = Digest(name: name, bytes: total, sha256: hash.finalize().map { String(format: "%02x", $0) }.joined())
        guard expected == nil || value == expected else { throw AIComponentStoreError.integrityMismatch("模型或固定图的 SHA-256 与本包不一致。") }; return value
    }
    private func fingerprints(graphs: URL, model: URL) throws -> [Digest] {
        try directory(graphs.deletingLastPathComponent()); try directory(graphs)
        var values = try Self.graphNames.map { try digest(graphs.appendingPathComponent($0), name: "graphs/" + $0) }
        values.append(try digest(model, name: "model.onnx")); return values
    }
    private func readRecord<T: Decodable>(_ url: URL, as type: T.Type) throws -> T {
        let info = try ordinary(url, owned: true)
        guard info.st_size > 0, info.st_size <= 4096 else { throw AIComponentStoreError.cacheUnavailable("组件缓存记录大小无效。") }
        let fd = try openRead(url); defer { close(fd) }; var bytes = [UInt8](repeating: 0, count: 4097), used = 0
        while used < bytes.count {
            let count = bytes.withUnsafeMutableBytes { Darwin.read(fd, $0.baseAddress?.advanced(by: used), $0.count - used) }
            if count < 0 && errno == EINTR { continue }; guard count >= 0 else { throw AIComponentStoreError.cacheUnavailable("组件缓存记录无法读取。") }
            if count == 0 { break }; used += count
        }
        guard used <= 4096, let value = try? JSONDecoder().decode(type, from: Data(bytes[0..<used])) else {
            throw AIComponentStoreError.cacheUnavailable("组件缓存记录损坏，原记录已保留；请检查后重新配置。")
        }; return value
    }
    private func writeRecord<T: Encodable>(_ value: T, to url: URL) throws {
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]; let bytes = try encoder.encode(value)
        guard bytes.count <= 4096 else { throw AIComponentStoreError.cacheUnavailable("组件缓存记录超出上限。") }
        let fd = try openNew(url); defer { close(fd) }; try bytes.withUnsafeBytes { try writeAll($0, fd: fd) }
        guard fsync(fd) == 0 else { throw AIComponentStoreError.cacheUnavailable("组件缓存记录未确认写入。") }
    }
    private func currentID() throws -> String? {
        let path = root.appendingPathComponent("selection.json"); guard try metadata(path) != nil else { return nil }
        let record = try readRecord(path, as: Record.self)
        guard record.version == 1, UUID(uuidString: record.id)?.uuidString == record.id else { throw AIComponentStoreError.cacheUnavailable("组件缓存配置无效，原记录已保留。") }; return record.id
    }
    private func selection(_ id: String, expected: [Digest]? = nil, at override: URL? = nil) throws -> (Selection, [Digest]) {
        let folder = override ?? root.appendingPathComponent(id, isDirectory: true), graphs = folder.appendingPathComponent("graphs", isDirectory: true)
        try directory(folder, owned: true); try directory(graphs, owned: true)
        guard Set(try FileManager.default.contentsOfDirectory(atPath: folder.path)) == Set(["graphs", "model.onnx", "owner.json"]), Set(try FileManager.default.contentsOfDirectory(atPath: graphs.path)) == Set(Self.graphNames) else {
            throw AIComponentStoreError.cacheUnavailable("缓存有未知或缺失数据，请保留现场。")
        }
        let receipt = try readRecord(folder.appendingPathComponent("owner.json"), as: Receipt.self)
        guard receipt.owner == marker, receipt.id == id, receipt.files.map(\.name) == Self.graphNames.map({ "graphs/" + $0 }) + ["model.onnx"], expected == nil || receipt.files == expected else {
            throw AIComponentStoreError.integrityMismatch("组件缓存不属于本包认可的数据。")
        }
        for file in receipt.files { let url = folder.appendingPathComponent(file.name); _ = try ordinary(url, owned: true); _ = try digest(url, name: file.name, expected: file) }
        guard let bytes = receipt.files.last?.bytes else { throw AIComponentStoreError.cacheUnavailable("模型缓存记录缺失。") }
        return (Selection(graphs: graphs, model: folder.appendingPathComponent("model.onnx"), modelBytes: bytes), receipt.files)
    }
    private func rename(_ source: URL, _ target: URL, exclusive: Bool) throws {
        let code = try fsPath(source) { from in try fsPath(target) { to in renamex_np(from, to, exclusive ? UInt32(RENAME_EXCL) : 0) } }
        guard code == 0 else { throw AIComponentStoreError.cacheUnavailable("组件缓存发布失败，请检查空间和权限。") }
    }
    private func removeOwned(_ url: URL, expected: Identity) throws {
        guard let value = try metadata(url), identity(value) == expected, value.st_uid == getuid(), value.st_mode & mode_t(S_IFMT) == mode_t(S_IFDIR) else {
            throw AIComponentStoreError.cleanupFailed(stage: url, message: "缓存清理归属无法确认，请保留现场。")
        }
        do { try FileManager.default.removeItem(at: url) } catch { throw AIComponentStoreError.cleanupFailed(stage: url, message: "组件缓存清理未确认，请保留现场。") }
    }
    // Retirement proves ownership, not usability. Missing/corrupt owned data
    // must remain repairable; unknown entries, links or receipts are retained.
    private func ownedCache(_ id: String, at folder: URL) throws -> Identity {
        try directory(folder, owned: true)
        guard Set(try FileManager.default.contentsOfDirectory(atPath: folder.path)).isSubset(of: ["graphs", "model.onnx", "owner.json"]) else { throw AIComponentStoreError.cacheUnavailable("缓存含未知数据。") }
        let receipt = try readRecord(folder.appendingPathComponent("owner.json"), as: Receipt.self)
        guard receipt.owner == marker, receipt.id == id, receipt.files.map(\.name) == Self.graphNames.map({ "graphs/" + $0 }) + ["model.onnx"] else { throw AIComponentStoreError.cacheUnavailable("缓存归属不明。") }
        let graphs = folder.appendingPathComponent("graphs", isDirectory: true)
        if try metadata(graphs) != nil {
            try directory(graphs, owned: true)
            guard Set(try FileManager.default.contentsOfDirectory(atPath: graphs.path)).isSubset(of: Set(Self.graphNames)) else { throw AIComponentStoreError.cacheUnavailable("缓存含未知数据。") }
            for name in Self.graphNames where try metadata(graphs.appendingPathComponent(name)) != nil { _ = try ordinary(graphs.appendingPathComponent(name), owned: true) }
        }
        let model = folder.appendingPathComponent("model.onnx")
        if try metadata(model) != nil { _ = try ordinary(model, owned: true) }
        guard let value = try metadata(folder) else { throw AIComponentStoreError.cacheUnavailable("缓存目录缺失。") }; return identity(value)
    }
    private func ownedInvalid(_ id: String, at folder: URL) throws -> Identity {
        try directory(folder, owned: true)
        guard Set(try FileManager.default.contentsOfDirectory(atPath: folder.path)) == Set(["owner.json", "record.json"]) else { throw AIComponentStoreError.cacheUnavailable("隔离记录归属不明。") }
        let receipt = try readRecord(folder.appendingPathComponent("owner.json"), as: InvalidReceipt.self)
        let raw = try ordinary(folder.appendingPathComponent("record.json"))
        guard raw.st_uid == getuid(), raw.st_mode & 0o777 == 0o600, raw.st_nlink == 1 || raw.st_nlink == 2 else { throw AIComponentStoreError.cacheUnavailable("隔离记录归属不明。") }
        if raw.st_nlink == 2 {
            guard let original = try metadata(root.appendingPathComponent("selection.json")), identity(original) == identity(raw) else { throw AIComponentStoreError.cacheUnavailable("隔离记录的链接归属不明。") }
        }
        guard receipt.owner == marker + ".invalid", receipt.id == id, receipt.device == Int64(raw.st_dev), receipt.inode == UInt64(raw.st_ino), let info = try metadata(folder) else { throw AIComponentStoreError.cacheUnavailable("隔离记录归属不明。") }
        return identity(info)
    }
    private func cleanRetired(currentID: String?, preserveUUIDCaches: Bool = false, keepingInvalid: String? = nil) throws {
        for name in try FileManager.default.contentsOfDirectory(atPath: root.path) where name != currentID && name != keepingInvalid {
            if name.hasPrefix(".selection-") {
                let file = root.appendingPathComponent(name)
                do {
                    let suffix = String(name.dropFirst(11)); guard UUID(uuidString: suffix)?.uuidString == suffix else { throw AIComponentStoreError.cacheUnavailable("暂存记录归属不明。") }
                    let record = try readRecord(file, as: Record.self)
                    guard record.version == 1, UUID(uuidString: record.id)?.uuidString == record.id else { throw AIComponentStoreError.cacheUnavailable("暂存记录归属不明。") }
                    try FileManager.default.removeItem(at: file)
                } catch { throw AIComponentStoreError.cleanupFailed(stage: file, message: "旧暂存配置清理未确认，未创建新缓存。") }
                continue
            }
            if name.hasPrefix(".invalid-") {
                let id = String(name.dropFirst(9)), folder = root.appendingPathComponent(name, isDirectory: true)
                do {
                    guard UUID(uuidString: id)?.uuidString == id else { throw AIComponentStoreError.cacheUnavailable("隔离记录归属不明。") }
                    try removeOwned(folder, expected: ownedInvalid(id, at: folder))
                } catch { throw AIComponentStoreError.cleanupFailed(stage: folder, message: "旧隔离记录清理未确认，未创建新缓存。") }
                continue
            }
            let stageID = name.hasPrefix(".stage-") ? String(name.dropFirst(7)) : name
            guard UUID(uuidString: stageID)?.uuidString == stageID else { continue }
            if preserveUUIDCaches && !name.hasPrefix(".stage-") { continue }
            let folder = root.appendingPathComponent(name, isDirectory: true)
            do {
                try removeOwned(folder, expected: ownedCache(stageID, at: folder))
            } catch { throw AIComponentStoreError.cleanupFailed(stage: folder, message: "旧缓存清理未确认，未创建新缓存；请保留现场。") }
        }
    }

    func load() async throws -> Selection? {
        do { return try loadData() }
        catch let error as AIComponentStoreError { throw error }
        catch { throw AIComponentStoreError.cacheUnavailable("组件缓存无法读取，原记录已保留；请检查权限。") }
    }
    private func loadData() throws -> Selection? {
        guard try metadata(root) != nil else { return nil }
        try directory(root.deletingLastPathComponent()); try directory(root, owned: true)
        guard let id = try currentID() else { return nil }
        return try selection(id, expected: fingerprints(graphs: referenceGraphs, model: referenceModel)).0
    }
    func install(graphs: URL, model: URL) async throws -> Selection {
        do { return try installData(graphs: graphs, model: model) }
        catch let error as AIComponentStoreError { throw error }
        catch { throw AIComponentStoreError.cacheUnavailable("组件导入未完成，原选择仍保留；请检查空间和权限。") }
    }
    private func installData(graphs: URL, model: URL) throws -> Selection {
        // Managed data cannot also be an external input to retirement cleanup.
        let prefix = root.standardizedFileURL.path + "/"
        guard !graphs.standardizedFileURL.path.hasPrefix(prefix), !model.standardizedFileURL.path.hasPrefix(prefix) else { throw AIComponentStoreError.invalidSelection("请从缓存之外选择原始模型和图数据。") }
        try ensureRoot()
        let oldID: String?
        var badRecord: Identity?
        do { oldID = try currentID() }
        catch {
            let raw = try ordinary(root.appendingPathComponent("selection.json"))
            guard raw.st_uid == getuid(), raw.st_mode & 0o777 == 0o600, raw.st_nlink <= 2 else { throw AIComponentStoreError.cacheUnavailable("原配置归属无法确认，请保留现场。") }
            oldID = nil; badRecord = identity(raw)
        }
        let expected = try fingerprints(graphs: referenceGraphs, model: referenceModel)
        guard try fingerprints(graphs: graphs, model: model) == expected else { throw AIComponentStoreError.integrityMismatch("所选模型和固定图必须与本包版本完全一致。") }
        try cleanRetired(currentID: oldID, preserveUUIDCaches: badRecord != nil)
        if let oldID, let existing = try? selection(oldID), existing.1 == expected { return existing.0 }
        let id = UUID().uuidString; var owned = root.appendingPathComponent(".stage-" + id, isDirectory: true)
        try FileManager.default.createDirectory(at: owned, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        let ownedID: Identity
        do { guard let created = try metadata(owned) else { throw AIComponentStoreError.cacheUnavailable("暂存目录不可用。") }; ownedID = identity(created) }
        catch { throw AIComponentStoreError.cleanupFailed(stage: owned, message: "暂存目录归属未确认，请保留现场。") }
        var committed = false; var tempRecord: URL?; var invalidBackup: (URL, Identity)?
        do {
            try FileManager.default.createDirectory(at: owned.appendingPathComponent("graphs", isDirectory: true), withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
            for file in expected {
                let input = file.name == "model.onnx" ? model : graphs.appendingPathComponent(String(file.name.dropFirst(7)))
                _ = try digest(input, name: file.name, expected: file, to: owned.appendingPathComponent(file.name))
            }
            try writeRecord(Receipt(owner: marker, id: id, files: expected), to: owned.appendingPathComponent("owner.json"))
            _ = try selection(id, expected: expected, at: owned)
            let published = root.appendingPathComponent(id, isDirectory: true)
            try directory(root, owned: true); try rename(owned, published, exclusive: true); owned = published
            let record = root.appendingPathComponent("selection.json")
            if let badRecord {
                let original = try ordinary(record)
                guard identity(original) == badRecord, original.st_nlink == 1 else { throw AIComponentStoreError.cacheUnavailable("原配置在导入期间发生变化。") }
                let backupID = UUID().uuidString, backup = root.appendingPathComponent(".invalid-" + backupID, isDirectory: true)
                try FileManager.default.createDirectory(at: backup, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
                guard let info = try metadata(backup) else { throw AIComponentStoreError.cleanupFailed(stage: backup, message: "隔离记录归属未确认，请保留现场。") }
                invalidBackup = (backup, identity(info))
                let code = try fsPath(record) { from in try fsPath(backup.appendingPathComponent("record.json")) { to in Darwin.link(from, to) } }
                guard code == 0 else { throw AIComponentStoreError.cacheUnavailable("原坏配置未能安全隔离，原记录仍保留。") }
                try writeRecord(InvalidReceipt(owner: marker + ".invalid", id: backupID, device: badRecord.device, inode: badRecord.inode), to: backup.appendingPathComponent("owner.json"))
            } else if try metadata(record) != nil { _ = try ordinary(record, owned: true) }
            let temp = root.appendingPathComponent(".selection-" + UUID().uuidString); tempRecord = temp
            try writeRecord(Record(version: 1, id: id), to: temp)
            try directory(root, owned: true); try rename(temp, record, exclusive: false); tempRecord = nil; committed = true
            if let oldID, oldID != id {
                do { let old = root.appendingPathComponent(oldID, isDirectory: true); try removeOwned(old, expected: ownedCache(oldID, at: old)) }
                catch { throw AIComponentStoreError.cleanupFailed(stage: root.appendingPathComponent(oldID), message: "新组件配置已保存，但旧缓存清理未确认；请保留现场。") }
            }
            if badRecord != nil {
                do { try cleanRetired(currentID: id, keepingInvalid: invalidBackup?.0.lastPathComponent) }
                catch { throw AIComponentStoreError.cleanupFailed(stage: root, message: "新组件配置已保存，但旧缓存清理未确认；请保留现场。") }
            }
            return Selection(graphs: published.appendingPathComponent("graphs", isDirectory: true), model: published.appendingPathComponent("model.onnx"), modelBytes: expected.last?.bytes ?? 0)
        } catch {
            if committed { throw error }
            if let (backup, backupID) = invalidBackup { try removeOwned(backup, expected: backupID) }
            if let tempRecord { do { try FileManager.default.removeItem(at: tempRecord) } catch { throw AIComponentStoreError.cleanupFailed(stage: owned, message: "配置暂存记录清理未确认；原选择仍保留。") } }
            try removeOwned(owned, expected: ownedID); throw error
        }
    }
}
