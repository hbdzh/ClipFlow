import AppKit
import AVKit
import Combine

/// Native presentation state; media policy and accepted jobs remain in Core.
@MainActor final class WorkspaceNavigation: ObservableObject {
    static let shared = WorkspaceNavigation()
    @Published var selection = 0
    static var acceptsCommand: Bool {
        guard !ApplicationShutdown.shared.started, !NativeFilePanels.hasPending, let window = NSApp.keyWindow,
              window.title == AppBrand.name, window.attachedSheet == nil,
              NSApp.modalWindow == nil else { return false }
        return true
    }
    /// Let native controls consume Space/arrows, including SwiftUI controls
    /// whose first responder is a hosting view rather than an NSControl.
    static func acceptsPlayback(in window: NSWindow) -> Bool {
        guard NSApp.keyWindow === window, window.attachedSheet == nil,
              NSApp.modalWindow == nil, !(window.firstResponder is NSTextView),
              !(window.firstResponder is NSControl) else { return false }
        // SwiftUI can report its hosting group while a native checkbox is
        // focused. Only the explicit player view owns global playback keys;
        // all other controls use their normal Space/arrow activation.
        var view = window.firstResponder as? NSView
        while let current = view {
            if current is AVPlayerView { return true }
            view = current.superview
        }
        return false
    }
    func importCurrent() {
        guard Self.acceptsCommand else { return }
        if selection == 0 { WorkspaceModel.shared.chooseInput() }
        else { Task { await AISeparationModel.shared.chooseSource() } }
    }
    func submitCurrent() {
        guard Self.acceptsCommand else { return }
        if selection == 0 { WorkspaceModel.shared.chooseOutput() }
        else { Task { await AISeparationModel.shared.start() } }
    }
}
