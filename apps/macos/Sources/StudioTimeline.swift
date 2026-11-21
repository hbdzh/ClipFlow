import AppKit
import SwiftUI

/// Direct range handles complement the accessible numeric fields and sliders.
struct TimelineRangeControl: View {
    let duration: Int64
    @Binding var start: Int64
    @Binding var end: Int64
    let position: Int64
    let enabled: Bool
    let thumbnails: [CGImage]

    private func fraction(_ value: Int64) -> CGFloat {
        guard duration > 0 else { return 0 }
        return CGFloat(min(max(Double(value) / Double(duration), 0), 1))
    }
    private func tickLabel(_ value: Int64) -> String {
        if duration < 4_000_000 { return String(format: "%.1f", Double(value) / 1_000_000) }
        let seconds = max(0, value / 1_000_000)
        return String(format: "%02lld:%02lld", seconds / 60, seconds % 60)
    }
    var body: some View {
        VStack(spacing: 2) {
            HStack {
                ForEach(0..<5, id: \.self) { index in
                    if index > 0 { Spacer(minLength: 0) }
                    Text(tickLabel(Int64(Double(duration) * Double(index) / 4)))
                        .font(.caption2.monospacedDigit()).foregroundStyle(.secondary)
                }
            }
            GeometryReader { geometry in
                let width = max(geometry.size.width - 16, 1)
                let left = fraction(start) * width + 8
                let right = fraction(end) * width + 8
                let playhead = fraction(position) * width + 8
                ZStack(alignment: .leading) {
                    RoundedRectangle(cornerRadius: 6).fill(.primary.opacity(0.08))
                    HStack(spacing: 0) {
                        ForEach(Array(thumbnails.enumerated()), id: \.offset) { _, image in
                            Image(decorative: image, scale: 1).resizable().scaledToFill()
                                .frame(width: max(0, geometry.size.width / CGFloat(max(thumbnails.count, 1))), height: 58).clipped()
                        }
                    }.clipShape(RoundedRectangle(cornerRadius: 6)).allowsHitTesting(false)
                    RoundedRectangle(cornerRadius: 6).strokeBorder(Color.accentColor, lineWidth: 2)
                        .background(Color.accentColor.opacity(0.13), in: RoundedRectangle(cornerRadius: 6))
                        .frame(width: max(0, right - left), height: 58).offset(x: left)
                        .allowsHitTesting(false)
                    Rectangle().fill(Color.accentColor).frame(width: 2, height: 66).offset(x: playhead - 1)
                        .allowsHitTesting(false)
                    handle(x: left, width: width, isStart: true)
                    handle(x: right, width: width, isStart: false)
                }.frame(maxHeight: .infinity)
            }.frame(height: 66).coordinateSpace(name: "timelineRange")
        }.accessibilityElement(children: .contain)
    }
    private func handle(x: CGFloat, width: CGFloat, isStart: Bool) -> some View {
        RoundedRectangle(cornerRadius: 3).fill(Color.accentColor)
            .frame(width: 12, height: 58).contentShape(Rectangle())
            .offset(x: x - 6)
            .gesture(DragGesture(minimumDistance: 0, coordinateSpace: .named("timelineRange"))
                .onChanged { value in
                    guard enabled, duration > 0 else { return }
                    let proportion = min(max((value.location.x - 8) / width, 0), 1)
                    let time = Int64((Double(duration) * Double(proportion)).rounded())
                    if isStart { start = min(time, max(0, end - 1)) }
                    else { end = max(time, min(duration, start + 1)) }
                })
            .accessibilityLabel(isStart ? "拖动设置入点" : "拖动设置出点")
    }
}
