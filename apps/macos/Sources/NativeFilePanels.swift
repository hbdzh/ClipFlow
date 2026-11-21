import AppKit

/// Platform file selection only. Models own scopes and recheck submission state.
@MainActor enum NativeFilePanels {
    struct Selection: Sendable {
        let response: NSApplication.ModalResponse
        let url: URL?
        let urls: [URL]
        static let cancelled = Selection(response: .cancel, url: nil, urls: [])
    }
    private final class Entry {
        let panel: NSSavePanel
        let completion: @MainActor @Sendable (Selection) -> Void
        var presented = false
        var finishing = false
        init(_ panel: NSSavePanel, completion: @escaping @MainActor @Sendable (Selection) -> Void) {
            self.panel = panel; self.completion = completion
        }
    }
    private static var pending: [ObjectIdentifier: Entry] = [:]
    private static var preparedOpen: NSOpenPanel?
    private static var preparedSave: NSSavePanel?
    private static var openTitle = ""
    private static var saveTitle = ""
    private static var preparation: Task<Void, Never>?
    static var hasPending: Bool { !pending.isEmpty }

    /// Product startup preparation, without displaying a hidden panel. The
    /// observer starts first; its original 2-second warmup stays unchanged.
    static func prepare() {
        guard preparation == nil, !ApplicationShutdown.shared.started else { return }
        preparation = Task { @MainActor in
            defer { preparation = nil }
            guard !ApplicationShutdown.shared.started else { return }
            _ = obtainOpen()
            try? await Task.sleep(for: .milliseconds(1))
            guard !ApplicationShutdown.shared.started else { return }
            _ = obtainSave()
        }
    }
    private static func obtainOpen() -> NSOpenPanel {
        if let preparedOpen { return preparedOpen }
        let panel = NativeHeartbeat.shared.measure(.filePanelPreparation) { NSOpenPanel() }
        panel.animationBehavior = .none
        openTitle = panel.title; preparedOpen = panel
        return panel
    }
    private static func obtainSave() -> NSSavePanel {
        if let preparedSave { return preparedSave }
        let panel = NativeHeartbeat.shared.measure(.filePanelPreparation) { NSSavePanel() }
        panel.animationBehavior = .none
        saveTitle = panel.title; preparedSave = panel
        return panel
    }
    private static func reset(_ panel: NSSavePanel, title: String) {
        // SDK 27 type changes can change file/dir flags: set those flags last.
        panel.allowedContentTypes = []
        panel.allowsOtherFileTypes = false
        panel.title = title; panel.message = ""; panel.prompt = ""
        panel.canCreateDirectories = true
        panel.treatsFilePackagesAsDirectories = false
        panel.directoryURL = nil
    }
    static func open() -> NSOpenPanel? {
        guard pending.isEmpty, !ApplicationShutdown.shared.started else { return nil }
        return NativeHeartbeat.shared.measure(.filePanelPreparation) {
            let panel = obtainOpen()
            guard !panel.isVisible, panel.sheetParent == nil else { return nil }
            reset(panel, title: openTitle)
            panel.canChooseFiles = true; panel.canChooseDirectories = false
            panel.allowsMultipleSelection = false
            return panel
        }
    }
    static func save() -> NSSavePanel? {
        guard pending.isEmpty, !ApplicationShutdown.shared.started else { return nil }
        return NativeHeartbeat.shared.measure(.filePanelPreparation) {
            let panel = obtainSave()
            guard !panel.isVisible, panel.sheetParent == nil else { return nil }
            reset(panel, title: saveTitle)
            panel.nameFieldStringValue = ""
            return panel
        }
    }
    private static func finish(_ id: ObjectIdentifier, entry: Entry, response: NSApplication.ModalResponse) {
        guard pending[id] === entry, !entry.finishing else { return }
        entry.finishing = true
        // Snapshot before orderOut or another owner can reconfigure the cache.
        let selected = response == .OK ? Selection(response: response, url: entry.panel.url,
            urls: (entry.panel as? NSOpenPanel)?.urls ?? entry.panel.url.map { [$0] } ?? []) : .cancelled
        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(1))
            guard pending[id] === entry else { return }
            entry.panel.orderOut(nil)
            pending.removeValue(forKey: id)
            entry.completion(ApplicationShutdown.shared.started ? .cancelled : selected)
        }
    }
    static func begin(_ panel: NSSavePanel, allowSheetHost: Bool = false,
                      completion: @escaping @MainActor @Sendable (Selection) -> Void) {
        guard !ApplicationShutdown.shared.started, pending.isEmpty,
              let main = NSApp?.windows.first(where: { $0.title == AppBrand.name && !$0.isSheet }) else {
            completion(.cancelled); return
        }
        let host: NSWindow
        if let sheet = main.attachedSheet {
            guard allowSheetHost, sheet.attachedSheet == nil, !(sheet is NSSavePanel) else {
                completion(.cancelled); return
            }
            host = sheet
        } else { host = main }
        let id = ObjectIdentifier(panel), entry = Entry(panel, completion: completion)
        pending[id] = entry
        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(1))
            guard pending[id] === entry, !entry.finishing else { return }
            guard !ApplicationShutdown.shared.started, main.isVisible, host.isVisible,
                  NSApp.windows.contains(where: { $0 === main }), host.attachedSheet == nil,
                  (host === main || main.attachedSheet === host) else {
                finish(id, entry: entry, response: .cancel); return
            }
            entry.presented = true
            NativeHeartbeat.shared.measure(.filePanelPresentation) {
                panel.beginSheetModal(for: host) { response in finish(id, entry: entry, response: response) }
            }
        }
    }
    static func choose(_ panel: NSSavePanel) async -> Selection {
        await withCheckedContinuation { continuation in
            begin(panel) { continuation.resume(returning: $0) }
        }
    }
    static func cancelAll() {
        for (id, entry) in Array(pending) {
            guard !entry.finishing else { continue }
            if entry.presented { entry.panel.cancel(nil) }
            else { finish(id, entry: entry, response: .cancel) }
        }
    }
}
