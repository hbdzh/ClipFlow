import Foundation

struct VideoTransform: Equatable {
    var rotationDegrees = 0
    var scalePercent = 100
    var offsetX = 0
    var offsetY = 0
    static let identity = VideoTransform()
}

@main struct Run {
    static func main() {
        for angle in [0, 90, 180, 270] {
            let value = VideoTransform(rotationDegrees: angle, scalePercent: 101)
            let geometry = VideoPreviewGeometry(sourceWidth: 320, sourceHeight: 180, transform: value)!
            let expected = angle == 90 || angle == 270 ? (180, 320, 182, 324) : (320, 180, 324, 182)
            precondition(geometry.width == expected.0 && geometry.height == expected.1)
            precondition(geometry.scaledWidth == expected.2 && geometry.scaledHeight == expected.3)
        }
        precondition(VideoPreviewGeometry(sourceWidth: 319, sourceHeight: 180, transform: .identity) == nil)
        precondition(VideoPreviewGeometry(sourceWidth: 320, sourceHeight: 180, transform: VideoTransform(rotationDegrees: 45)) == nil)
        let geometry = VideoPreviewGeometry(sourceWidth: 320, sourceHeight: 180, transform: VideoTransform(rotationDegrees: 90))!
        let positive = geometry.layerPosition(in: CGSize(width: 90, height: 160), transform: VideoTransform(offsetX: 40, offsetY: 40))
        let negative = geometry.layerPosition(in: CGSize(width: 90, height: 160), transform: VideoTransform(offsetX: -40, offsetY: -40))
        precondition(positive == CGPoint(x: 65, y: 60))
        precondition(negative == CGPoint(x: 25, y: 100))
        print("transform preview geometry checks passed")
    }
}
