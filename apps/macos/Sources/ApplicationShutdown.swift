import Foundation
import Combine

/// Retain cleanup and its owners after the application watchdog returns.
@MainActor final class ApplicationShutdown: ObservableObject {
    static let shared = ApplicationShutdown(
        freeze: {
            AISeparationModel.shared.beginShutdown()
            WorkspaceModel.shared.beginShutdown()
        },
        operations: [
            { try await AISeparationModel.shared.shutdown() },
            { try await WorkspaceModel.shared.shutdown() }
        ])
    private let freeze: @MainActor () -> Void
    private let operations: [@MainActor () async throws -> Void]
    private var workers: [Int: Task<Void, Never>] = [:]
    private var results: [Int: Result<Void, Error>] = [:]
    @Published private(set) var started = false
    @Published private(set) var completed = false
    private(set) var watchdogTimeouts = 0
    @Published private(set) var issue: String?
    var pendingCount: Int { workers.count }

    init(freeze: @escaping @MainActor () -> Void,
         operations: [@MainActor () async throws -> Void]) {
        self.freeze = freeze; self.operations = operations
    }
    func shutdown() async throws {
        guard !completed else { return }
        if !started { started = true; freeze() }
        for index in operations.indices where workers[index] == nil {
            if case .success? = results[index] { continue }
            results[index] = nil
            workers[index] = Task { @MainActor [self] in
                do { try await operations[index](); results[index] = .success(()) }
                catch { results[index] = .failure(error) }
                workers[index] = nil
            }
        }
        let deadline = ContinuousClock.now + .seconds(15)
        while !workers.isEmpty {
            guard ContinuousClock.now < deadline else {
                watchdogTimeouts += 1
                let message = "关闭超过 15 秒仍未确认；已保留会话与文件访问，后台继续清理。当前不能退出或启动新任务。"
                issue = message; throw CheckFailure(message: message)
            }
            try await Task.sleep(for: .milliseconds(20))
        }
        for index in operations.indices {
            if case .failure(let error)? = results[index] {
                let message = "关闭尚未确认：\(error.localizedDescription) 已保留相关会话与文件访问，不能退出或启动新任务。"
                issue = message; throw CheckFailure(message: message)
            }
        }
        completed = true; issue = nil
    }
}
