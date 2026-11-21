import AppKit
import SwiftUI

struct AIAuditionKeyboard: NSViewRepresentable {
    let model: AISeparationModel
    func makeCoordinator() -> Coordinator { Coordinator(model) }
    func makeNSView(context: Context) -> NSView {
        let view = NSView(); context.coordinator.install(view); return view
    }
    func updateNSView(_ view: NSView, context: Context) {}
    static func dismantleNSView(_ view: NSView, coordinator: Coordinator) { coordinator.remove() }
    @MainActor final class Coordinator {
        let model: AISeparationModel
        private var monitor: Any?
        private weak var view: NSView?
        init(_ model: AISeparationModel) { self.model = model }
        func install(_ view: NSView) {
            self.view = view
            monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
                let handled = MainActor.assumeIsolated {
                    guard let self, let window = self.view?.window,
                          WorkspaceNavigation.acceptsPlayback(in: window), self.model.canExport,
                          event.keyCode == 49,
                          event.modifierFlags.intersection([.command, .control, .option, .shift]).isEmpty,
                          let directory = self.model.resultDirectory else { return false }
                    Task { await self.model.audition.toggle(self.model.audition.selected, directory: directory) }
                    return true
                }
                return handled ? nil : event
            }
        }
        func remove() { if let monitor { NSEvent.removeMonitor(monitor) }; monitor = nil }
    }
}
