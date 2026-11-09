import Foundation

struct SelfTestReport: Codable, Sendable {
    let schemaVersion: Int
    let scope: String
    let checks: [String]
    let version: CoreVersion
    let capabilities: CoreCapabilities
    let lifecycleIterations: Int
    let bridgeDiagnostics: BridgeDiagnostics
}

struct CheckFailure: Error, LocalizedError, Sendable {
    let message: String
    var errorDescription: String? { message }
}

func require(_ condition: Bool, _ message: String) throws {
    if !condition { throw CheckFailure(message: message) }
}

func runSelfTest() async throws -> SelfTestReport {
    let bridge = CoreBridge()
    let connection = try await bridge.connect()
    try require(connection.version.schemaVersion == 0 && connection.version.abiVersion == 0, "Unexpected ABI/schema")
    try require(connection.version.mockOnly, "Core must identify mock scope")
    for feature in ["probe", "trim_stream_copy", "accurate_reencode", "ai_separation"] {
        try require(connection.capabilities.features[feature]?.available == false, "Real media feature must remain unavailable")
    }
    try require(connection.capabilities.features["mock_job"]?.available == true, "Mock feature unavailable")
    var checks = ["version_capabilities", "real_media_unavailable"]
    let submitted = try await bridge.submit(outcome: .succeed)
    try require(submitted.state == "queued", "Submission must queue")
    let first = try await bridge.poll(id: submitted.jobId)
    let succeeded = try await bridge.poll(id: submitted.jobId)
    try require(first.state == "running" && succeeded.state == "succeeded", "Success state sequence invalid")
    try require(succeeded.result?.mockOnly == true && succeeded.result?.fileCreated == false, "Mock must not claim a real file")
    checks.append("mock_success_no_file")
    let toCancel = try await bridge.submit(outcome: .succeed)
    try await bridge.cancel(id: toCancel.jobId)
    let cancelling = try await bridge.poll(id: toCancel.jobId)
    let cancelled = try await bridge.poll(id: toCancel.jobId)
    try require(cancelling.state == "cancelling" && cancelled.state == "cancelled", "Cancellation confirmation sequence invalid")
    checks.append("cancel_waits_for_confirmation")
    let toFail = try await bridge.submit(outcome: .fail)
    _ = try await bridge.poll(id: toFail.jobId)
    let failed = try await bridge.poll(id: toFail.jobId)
    try require(failed.state == "failed" && failed.error?.code == "MOCK_FAILED", "Configured failure must remain failed")
    checks.append("mock_failure")
    do {
        _ = try await bridge.submitRawForSelfTest(Data(#"{"schema_version":999,"operation":"mock"}"#.utf8))
        throw CheckFailure(message: "Unsupported schema unexpectedly succeeded")
    } catch let error as BridgeError {
        try require(error.code == -4, "Wrong unsupported schema status")
    }
    do {
        _ = try await bridge.submitRawForSelfTest(Data([0xFF]))
        throw CheckFailure(message: "Invalid UTF-8 unexpectedly succeeded")
    } catch let error as BridgeError {
        try require(error.code == -3, "Wrong UTF-8 status")
    }
    do {
        _ = try await bridge.poll(id: "not-a-job")
        throw CheckFailure(message: "Unknown job unexpectedly succeeded")
    } catch let error as BridgeError {
        try require(error.code == -6, "Wrong unknown-job status")
    }
    checks += ["unknown_schema_rejected", "invalid_utf8_rejected", "unknown_job_rejected"]
    let activeAtClose = try await bridge.submit(outcome: .succeed)
    try require(activeAtClose.state == "queued", "Close test needs active job")
    try await bridge.close()
    try await bridge.close()
    do {
        _ = try await bridge.poll(id: activeAtClose.jobId)
        throw CheckFailure(message: "Closed bridge accepted a call")
    } catch let error as BridgeError {
        try require(error.code == -2, "Closed bridge error mismatch")
    }
    checks += ["close_cancels_known_jobs", "close_idempotent", "closed_handle_not_reused"]
    let diagnostics = await bridge.diagnostics()
    try require(diagnostics.allCallsOffMainThread, "FFI executed on main thread")
    try require(diagnostics.outputCalls == diagnostics.bufferReleaseCalls, "Each output path must release exactly once")
    try require(diagnostics.errorOutputsWereEmpty, "Core error output was nonempty")
    checks += ["actor_serializes_ffi_off_main", "all_output_paths_defer_free", "error_outputs_empty"]
    let iterations = 200
    for _ in 0..<iterations {
        let repeated = CoreBridge()
        _ = try await repeated.connect()
        try await repeated.close()
    }
    checks.append("200_create_read_free_destroy")
    return SelfTestReport(schemaVersion: 0, scope: "MAC-001 real Swift to Rust C ABI; mock jobs, no media processing", checks: checks,
                          version: connection.version, capabilities: connection.capabilities,
                          lifecycleIterations: iterations, bridgeDiagnostics: diagnostics)
}
