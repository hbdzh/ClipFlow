import SwiftUI

@main struct PrototypeApp: App {
    var body: some Scene {
        Window("\(AppBrand.name) · 视觉样板", id: "visual-prototype") {
            PrototypeRoot().frame(minWidth: 1050, minHeight: 680)
        }.defaultSize(width: 1440, height: 900)
            .windowToolbarStyle(.unified)
    }
}
