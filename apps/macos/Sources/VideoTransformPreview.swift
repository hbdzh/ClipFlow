import AppKit
import AVFoundation
import SwiftUI

// Display geometry only; Core remains authoritative for whether export is allowed.
struct VideoPreviewGeometry {
    let width: Int
    let height: Int
    let scaledWidth: Int
    let scaledHeight: Int

    init?(sourceWidth: Int, sourceHeight: Int, transform: VideoTransform) {
        guard (2...1920).contains(sourceWidth), (2...1920).contains(sourceHeight),
              min(sourceWidth, sourceHeight) <= 1080,
              sourceWidth.isMultiple(of: 2), sourceHeight.isMultiple(of: 2),
              [0, 90, 180, 270].contains(transform.rotationDegrees),
              (100...200).contains(transform.scalePercent) else { return nil }
        let rotated = transform.rotationDegrees == 90 || transform.rotationDegrees == 270
        width = rotated ? sourceHeight : sourceWidth
        height = rotated ? sourceWidth : sourceHeight
        scaledWidth = ((width * transform.scalePercent + 99) / 100 + 1) & ~1
        scaledHeight = ((height * transform.scalePercent + 99) / 100 + 1) & ~1
    }

    func layerPosition(in size: CGSize, transform: VideoTransform) -> CGPoint {
        // AVPlayerLayer's composited coordinates move visually upward for +Y.
        // Core offsets use a top-left origin, so positive Y must be inverted.
        CGPoint(x: size.width / 2 + CGFloat(transform.offsetX) * size.width / CGFloat(width),
                y: size.height / 2 - CGFloat(transform.offsetY) * size.height / CGFloat(height))
    }
}

struct TransformedNativePreview: NSViewRepresentable {
    let player: AVPlayer
    let geometry: VideoPreviewGeometry
    let transform: VideoTransform

    func makeNSView(context: Context) -> VideoTransformPreviewView {
        VideoTransformPreviewView()
    }
    func updateNSView(_ view: VideoTransformPreviewView, context: Context) {
        view.update(player: player, geometry: geometry, transform: transform)
    }
}

final class VideoTransformPreviewView: NSView {
    private let scaledLayer = CALayer()
    private let playerLayer = AVPlayerLayer()
    private var geometry: VideoPreviewGeometry?
    private var videoTransform = VideoTransform.identity

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        wantsLayer = true
        layer?.backgroundColor = NSColor.black.cgColor
        layer?.masksToBounds = true
        layer?.isGeometryFlipped = true
        scaledLayer.isGeometryFlipped = true
        playerLayer.videoGravity = .resizeAspect
        layer?.addSublayer(scaledLayer)
        scaledLayer.addSublayer(playerLayer)
    }
    required init?(coder: NSCoder) { nil }

    func update(player: AVPlayer, geometry: VideoPreviewGeometry, transform: VideoTransform) {
        playerLayer.player = player
        self.geometry = geometry
        videoTransform = transform
        needsLayout = true
    }

    override func layout() {
        super.layout()
        guard let geometry, bounds.width > 0, bounds.height > 0 else { return }
        // The caller fits this view to the output canvas aspect ratio.
        let width = bounds.width
        let height = bounds.height
        let rotated = videoTransform.rotationDegrees == 90 || videoTransform.rotationDegrees == 270
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        scaledLayer.bounds = CGRect(x: 0, y: 0, width: width, height: height)
        scaledLayer.position = geometry.layerPosition(in: bounds.size, transform: videoTransform)
        scaledLayer.setAffineTransform(CGAffineTransform(scaleX: CGFloat(geometry.scaledWidth) / CGFloat(geometry.width),
                                                         y: CGFloat(geometry.scaledHeight) / CGFloat(geometry.height)))
        playerLayer.bounds = CGRect(x: 0, y: 0, width: rotated ? height : width, height: rotated ? width : height)
        playerLayer.position = CGPoint(x: width / 2, y: height / 2)
        playerLayer.setAffineTransform(CGAffineTransform(rotationAngle: CGFloat(videoTransform.rotationDegrees) * .pi / 180))
        CATransaction.commit()
    }
}
