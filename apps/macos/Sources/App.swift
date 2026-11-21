import AppKit
import Foundation
import SwiftUI

// A local monitor leaves text editing and accessibility keyboard navigation with AppKit.
struct PlaybackKeyboard: NSViewRepresentable {
    let model: WorkspaceModel
    let preview: PreviewController
    func makeCoordinator() -> Coordinator { Coordinator(model, preview) }
    func makeNSView(context: Context) -> NSView {
        let view = NSView(); context.coordinator.install(view: view); return view
    }
    func updateNSView(_ view: NSView, context: Context) {}
    static func dismantleNSView(_ view: NSView, coordinator: Coordinator) { coordinator.remove() }
    @MainActor final class Coordinator {
        private let model: WorkspaceModel
        private let preview: PreviewController
        private var monitor: Any?
        private weak var view: NSView?
        init(_ model: WorkspaceModel, _ preview: PreviewController) { self.model = model; self.preview = preview }
        func install(view: NSView) {
            self.view = view
            monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
                let handled = MainActor.assumeIsolated {
                    guard let self, let window = self.view?.window, NSApp.keyWindow === window,
                          WorkspaceNavigation.acceptsPlayback(in: window), self.preview.ready,
                          !self.model.stopping, !self.model.unconfirmedCleanup else { return false }
                    let modifiers = event.modifierFlags.intersection([.command, .control, .option, .shift])
                    if event.keyCode == 49, modifiers.isEmpty {
                        self.preview.togglePlayback(); return true
                    }
                    if modifiers == .option, [123, 124].contains(event.keyCode), self.model.canStepPreviewFrame {
                        let direction = event.keyCode == 123 ? -1 : 1
                        Task { await self.model.stepPreviewFrame(direction: direction) }
                        return true
                    }
                    return false
                }
                return handled ? nil : event
            }
        }
        func remove() { if let monitor { NSEvent.removeMonitor(monitor) }; monitor = nil }
    }
}
@MainActor
final class WorkspaceDelegate: NSObject, NSApplicationDelegate {
    private var closingWindow: NSWindow?
    func applicationDidFinishLaunching(_ notification: Notification) {
        NotificationCenter.default.addObserver(self, selector: #selector(windowWillClose(_:)), name: NSWindow.willCloseNotification, object: nil)
        NativeHeartbeat.shared.startIfEnabled {
            let media = WorkspaceModel.shared.active
            let aiModel = AISeparationModel.shared
            let ai = aiModel.isRunning || aiModel.isConfiguring || aiModel.isRestoringComponents
            return media && ai ? .mixed : media ? .media : ai ? .ai : .idle
        }
        NativeFilePanels.prepare()
    }
    @objc private func windowWillClose(_ notification: Notification) {
        if let window = notification.object as? NSWindow, window.title == AppBrand.name { closingWindow = window }
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        Task {
            do {
                NativeFilePanels.cancelAll()
                try await ApplicationShutdown.shared.shutdown()
                await NativeHeartbeat.shared.stop()
                sender.reply(toApplicationShouldTerminate: true)
            }
            catch {
                sender.reply(toApplicationShouldTerminate: false)
                let window = closingWindow ?? sender.windows.first { $0.canBecomeMain }
                window?.makeKeyAndOrderFront(nil); sender.activate()
                let alert = NSAlert(); alert.messageText = "关闭尚未确认"; alert.informativeText = ApplicationShutdown.shared.issue ?? error.localizedDescription
                alert.addButton(withTitle: "返回工作区")
                if let window { await alert.beginSheetModal(for: window) } else { alert.runModal() }
            }
        }
        return .terminateLater
    }
}
struct MediaStudioSpikeApp: App {
    @ObservedObject private var model = WorkspaceModel.shared
    @ObservedObject private var ai = AISeparationModel.shared
    @ObservedObject private var navigation = WorkspaceNavigation.shared
    @NSApplicationDelegateAdaptor(WorkspaceDelegate.self) private var delegate
    var body: some Scene {
        Window(AppBrand.name, id: "workspace") { WorkspaceView(model: .shared, preview: WorkspaceModel.shared.preview) }
            .windowToolbarStyle(.unified)
            .windowResizability(.contentMinSize).defaultSize(width: 1280, height: 800)
            .commands {
                CommandGroup(after: .newItem) {
                    Button(navigation.selection == 0 ? "导入媒体…" : "选择音频…") { navigation.importCurrent() }
                        .keyboardShortcut("o", modifiers: .command).disabled(navigation.selection == 0 ? !model.canImport : !ai.canChooseSource)
                    Button(navigation.selection == 0 ? "导出所选区间…" : "开始分离") { navigation.submitCurrent() }
                        .keyboardShortcut(.return, modifiers: .command).disabled(navigation.selection == 0 ? !model.canExport : !ai.canStart)
                    if navigation.selection == 1 {
                        Button("导出两条音轨…") {
                            guard WorkspaceNavigation.acceptsCommand else { return }
                            Task { await ai.exportResult() }
                        }.keyboardShortcut("e", modifiers: [.command, .shift]).disabled(!ai.canExport)
                    }
                }
            }
    }
}
@main struct EntryPoint {
    @MainActor static func main() async {
        #if !MEDIASTUDIO_PRODUCTION
        do {
            let encoder = JSONEncoder(); encoder.outputFormatting = [.prettyPrinted, .sortedKeys]; encoder.keyEncodingStrategy = .convertToSnakeCase
            if CommandLine.arguments.contains("--self-test") {
                let report = try await runSelfTest(); FileHandle.standardOutput.write(try encoder.encode(report)); FileHandle.standardOutput.write(Data("\n".utf8)); exit(0)
            }
            if CommandLine.arguments.contains("--shutdown-self-test") {
                let report = try await runApplicationShutdownSelfTest(); FileHandle.standardOutput.write(try encoder.encode(report)); FileHandle.standardOutput.write(Data("\n".utf8)); exit(report.passed ? 0 : 1)
            }
            if CommandLine.arguments.contains("--media-self-test") {
                let report = try await runMediaSelfTest(); FileHandle.standardOutput.write(try encoder.encode(report)); FileHandle.standardOutput.write(Data("\n".utf8)); exit(report.passed ? 0 : 1)
            }
            if CommandLine.arguments.contains("--ai-self-test") {
                let report = try await runAISeparationSelfTest(); FileHandle.standardOutput.write(try encoder.encode(report)); FileHandle.standardOutput.write(Data("\n".utf8)); exit(report.passed ? 0 : 1)
            }
            if CommandLine.arguments.contains("--workspace-self-test") || CommandLine.arguments.contains("--queue-self-test") {
                let workspaceEncoder = JSONEncoder(); workspaceEncoder.outputFormatting = [.prettyPrinted, .sortedKeys]
                let report = try await runWorkspaceSelfTest(); FileHandle.standardOutput.write(try workspaceEncoder.encode(report)); FileHandle.standardOutput.write(Data("\n".utf8)); exit(report.passed ? 0 : 1)
            }
        } catch { FileHandle.standardError.write(Data("MediaStudio self-test failed: \(error.localizedDescription)\n".utf8)); exit(1) }
        #else
        if CommandLine.arguments.contains(where: { $0.hasSuffix("-self-test") }) {
            FileHandle.standardError.write(Data("Self-tests are not included; rebuild with --self-tests.\n".utf8))
            exit(2)
        }
        #endif
        MediaStudioSpikeApp.main()
    }
}
