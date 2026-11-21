import AppKit
import Foundation
import UniformTypeIdentifiers

// Substitute only the helper's shutdown flag. No panel is displayed; no
// MediaStudio Core, AVPlayer or other media code is loaded by this driver.
@MainActor final class ApplicationShutdown {
    static let shared = ApplicationShutdown()
    var started = false
}

@main struct NativePanelReuseChecks {
    @MainActor static func main() async throws {
        let app = NSApplication.shared
        app.setActivationPolicy(.prohibited)
        var checks: [[String: String]] = []
        func check(_ value: Bool, _ name: String) throws {
            checks.append(["name": name, "result": value ? "PASS" : "FAIL"])
            if !value { throw NSError(domain: name, code: 1) }
        }
        NativeFilePanels.prepare()
        try await Task.sleep(for: .milliseconds(20))
        guard let first = NativeFilePanels.open() else { throw NSError(domain: "open unavailable", code: 1) }
        first.allowedContentTypes = [.movie]
        first.allowsMultipleSelection = true
        first.title = "media"; first.message = "old message"
        first.canCreateDirectories = false
        first.treatsFilePackagesAsDirectories = true
        first.canChooseFiles = false; first.canChooseDirectories = true
        guard let second = NativeFilePanels.open() else { throw NSError(domain: "reuse unavailable", code: 1) }
        try check(first === second, "open-panel-object-is-reused")
        try check(second.allowedContentTypes.isEmpty, "media-filter-is-cleared")
        try check(second.canChooseFiles && !second.canChooseDirectories, "sdk27-file-directory-flags-reset-after-types")
        try check(!second.allowsMultipleSelection, "multiple-selection-does-not-leak")
        try check(!second.treatsFilePackagesAsDirectories, "package-directory-policy-reset")
        try check(second.message.isEmpty && second.title != "media", "role-text-does-not-leak")
        try check(second.canCreateDirectories, "create-directory-default-restored")
        second.canChooseFiles = false; second.canChooseDirectories = true
        try check(!second.canChooseFiles && second.canChooseDirectories, "directory-owner-can-apply-its-policy")
        let cancelled = await NativeFilePanels.choose(second)
        try check(cancelled.response == .cancel && cancelled.url == nil && cancelled.urls.isEmpty,
                  "no-main-window-returns-empty-cancel-snapshot")
        try check(!NativeFilePanels.hasPending, "refused-presentation-leaves-no-reservation")
        guard let save = NativeFilePanels.save() else { throw NSError(domain: "save unavailable", code: 1) }
        save.allowedContentTypes = [.mpeg4Movie]; save.nameFieldStringValue = "old.mp4"
        save.message = "old media destination"
        guard let nextSave = NativeFilePanels.save() else { throw NSError(domain: "save reuse unavailable", code: 1) }
        try check(save === nextSave, "save-panel-object-is-reused")
        try check(nextSave.allowedContentTypes.isEmpty && nextSave.nameFieldStringValue.isEmpty,
                  "video-extension-and-name-do-not-leak-into-stem-directory-save")
        try check(nextSave.message.isEmpty, "save-message-reset")
        ApplicationShutdown.shared.started = true
        try check(NativeFilePanels.open() == nil && NativeFilePanels.save() == nil,
                  "shutdown-refuses-reborrow")
        try check(app.windows.allSatisfy { !$0.isVisible }, "no-window-or-panel-was-displayed")
        let result: [String: Any] = ["result": "PASS", "checks": checks,
            "scope": "Native object state without showing UI; production helper/heartbeat, stub shutdown flag only",
            "no_playback_or_audio_output": true]
        FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys]))
        FileHandle.standardOutput.write(Data("\n".utf8))
    }
}
