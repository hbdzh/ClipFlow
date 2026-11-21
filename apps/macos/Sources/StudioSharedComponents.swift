import SwiftUI
import AppKit

enum AppBrand {
    static let name = "ClipFlow"
}

struct StudioWorkspaceLayout<Sidebar: View, Content: View, Inspector: View>: View {
    @Binding var visibility: NavigationSplitViewVisibility
    @Binding var inspectorPresented: Bool
    @State private var inspectorWidth: CGFloat = 300
    @ViewBuilder let sidebar: () -> Sidebar
    @ViewBuilder let content: () -> Content
    @ViewBuilder let inspector: () -> Inspector

    var body: some View {
        NavigationSplitView(columnVisibility: $visibility) {
            sidebar().navigationSplitViewColumnWidth(min: 190, ideal: 220, max: 270)
        } detail: {
            HStack(spacing: 0) {
                mainContent.frame(minWidth: 300)
                if inspectorPresented {
                    StudioInspectorDivider(width: $inspectorWidth)
                    inspector().frame(width: inspectorWidth)
                        .background(.regularMaterial)
                }
            }
        }
    }

    private var mainContent: some View {
        ScrollView {
            content().frame(maxWidth: 1040).frame(maxWidth: .infinity).padding(20)
        }.background(Color(nsColor: .windowBackgroundColor))
    }
}

private struct StudioInspectorDivider: View {
    @Binding var width: CGFloat
    @State private var startWidth: CGFloat?
    @State private var hovering = false
    private let limits: ClosedRange<CGFloat> = 270...340

    var body: some View {
        Rectangle().fill(Color.clear)
            .frame(width: 12)
            .overlay { Rectangle().fill(Color(nsColor: .separatorColor)).frame(width: 1) }
            .contentShape(Rectangle())
            .gesture(DragGesture(minimumDistance: 0, coordinateSpace: .global)
                .onChanged { value in
                    if startWidth == nil { startWidth = width }
                    width = min(limits.upperBound, max(limits.lowerBound, (startWidth ?? width) - value.translation.width))
                }
                .onEnded { _ in startWidth = nil })
            .onHover { inside in
                guard inside != hovering else { return }
                hovering = inside
                if inside { NSCursor.resizeLeftRight.push() } else { NSCursor.pop() }
            }
            .onDisappear { if hovering { NSCursor.pop(); hovering = false } }
            .accessibilityElement()
            .accessibilityLabel("检查器宽度")
            .accessibilityValue("\(Int(width)) 点")
            .accessibilityAdjustableAction { direction in
                switch direction {
                case .increment: width = min(limits.upperBound, width + 10)
                case .decrement: width = max(limits.lowerBound, width - 10)
                @unknown default: break
                }
            }
    }
}

struct StudioInspectorLayout<Content: View, Action: View>: View {
    let audio: Bool
    @Binding var tab: Int
    @ViewBuilder let content: () -> Content
    @ViewBuilder let action: () -> Action

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if !audio {
                Picker("设置", selection: $tab) { Text("画面").tag(0); Text("音频").tag(1) }
                    .pickerStyle(.segmented).padding(16)
                Divider()
            }
            ScrollView {
                VStack(alignment: .leading, spacing: 20) { content() }
                    .padding(18).frame(maxWidth: .infinity, alignment: .leading)
            }
            Divider()
            action().padding(16)
        }.frame(maxHeight: .infinity, alignment: .top)
    }
}

struct StudioVideoStage<Picture: View, Controls: View>: View {
    @ViewBuilder let picture: () -> Picture
    @ViewBuilder let controls: () -> Controls

    var body: some View {
        VStack(spacing: 0) {
            picture().aspectRatio(16/8.4, contentMode: .fit).frame(maxHeight: 360)
                .frame(maxWidth: .infinity).background(Color.black).clipped()
            controls().foregroundStyle(.white).padding(.horizontal, 18).padding(.vertical, 13)
                .background(Color(red: 0.09, green: 0.10, blue: 0.13))
        }.clipShape(RoundedRectangle(cornerRadius: 12))
    }
}

struct StudioWaveformPanel<Content: View>: View {
    @ViewBuilder let content: () -> Content
    var body: some View {
        VStack(spacing: 0) { content() }
            .padding(.bottom, 14)
            .background(Color(red: 0.12, green: 0.14, blue: 0.18), in: RoundedRectangle(cornerRadius: 12))
    }
}

struct StudioContentCard<Content: View>: View {
    @ViewBuilder let content: () -> Content
    var body: some View {
        VStack(alignment: .leading, spacing: 12) { content() }
            .padding(16).frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 12))
    }
}

struct StudioAudioHeading: View {
    let title: String
    let subtitle: String
    var body: some View {
        HStack(spacing: 14) {
            Image(systemName: "waveform").font(.title2).foregroundStyle(Color.accentColor)
                .frame(width: 54, height: 54)
                .background(Color.accentColor.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.title2.bold()).lineLimit(1).truncationMode(.middle)
                Text(subtitle).font(.callout).foregroundStyle(.secondary)
            }
            Spacer()
        }.padding(.bottom, 4)
    }
}

struct StudioSeparationSettings<Details: View, Status: View>: View {
    let modelName: String
    @ViewBuilder let details: () -> Details
    @ViewBuilder let status: () -> Status

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Label("分离设置", systemImage: "slider.horizontal.3").font(.headline)
            VStack(alignment: .leading, spacing: 9) {
                Text("分离模型").font(.callout.weight(.medium))
                Text(modelName).frame(maxWidth: .infinity, alignment: .leading).padding(9)
                    .background(.quaternary, in: RoundedRectangle(cornerRadius: 7))
                details()
            }
            Divider(); Text("分离内容").font(.headline)
            Label("人声", systemImage: "checkmark.square.fill").foregroundStyle(Color.accentColor)
            Label("伴奏", systemImage: "checkmark.square.fill").foregroundStyle(Color.accentColor)
            Divider(); Text("输出").font(.headline); LabeledContent("格式", value: "WAV · 44.1 kHz")
            status()
        }
    }
}

struct StudioTransformControls: View {
    @Binding var rotation: Int
    @Binding var scale: Int
    @Binding var offsetX: Int
    @Binding var offsetY: Int
    let controlsEnabled: Bool
    let resetEnabled: Bool
    let showModeWarning: Bool
    let reset: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text("画面变换").font(.headline)
                Spacer()
                Button("重置", action: reset).buttonStyle(.link).disabled(!resetEnabled)
            }
            Grid(alignment: .leading, horizontalSpacing: 18, verticalSpacing: 12) {
                GridRow { Text("旋转"); Picker("旋转", selection: $rotation) { Text("0°").tag(0); Text("90°").tag(90); Text("180°").tag(180); Text("270°").tag(270) }.labelsHidden() }
                GridRow { Text("缩放"); Stepper("\(scale)%", value: $scale, in: 100...200, step: 5) }
                GridRow { Text("水平"); Stepper("\(offsetX) 像素", value: $offsetX, in: -1920...1920, step: 2) }
                GridRow { Text("垂直"); Stepper("\(offsetY) 像素", value: $offsetY, in: -1920...1920, step: 2) }
            }.font(.callout).disabled(!controlsEnabled)
            Text("画面变换用于标准转码。正值向右或向下；超出画布的部分会裁切。")
                .font(.caption).foregroundStyle(.secondary)
            if showModeWarning {
                Text("请切换到标准转码或重置画面变换。 ").font(.caption).foregroundStyle(.orange)
            }
        }
    }
}

struct StudioTrimControls: View {
    @Binding var start: String
    @Binding var end: String
    let editingEnabled: Bool
    let pointsEnabled: Bool
    let issue: String?
    let setInPoint: () -> Void
    let setOutPoint: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("裁剪区间").font(.headline)
            HStack {
                VStack(alignment: .leading) { Text("开始（秒）").font(.caption); TextField("开始", text: $start).accessibilityIdentifier("range-start") }
                Text("→").foregroundStyle(.secondary)
                VStack(alignment: .leading) { Text("结束（秒）").font(.caption); TextField("结束", text: $end).accessibilityIdentifier("range-end") }
            }.textFieldStyle(.roundedBorder).monospacedDigit().disabled(!editingEnabled)
            HStack { Button("设为入点", action: setInPoint); Button("设为出点", action: setOutPoint) }.disabled(!pointsEnabled)
            if let issue { Text(issue).font(.caption).foregroundStyle(.orange) }
        }
    }
}

struct StudioExportControls<Selection: Hashable, Actions: View>: View {
    @Binding var selection: Selection
    let fast: Selection
    let accurate: Selection
    let standard: Selection
    let compact: Selection
    let editingEnabled: Bool
    let isReencoded: Bool
    @ViewBuilder let actions: () -> Actions

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("导出").font(.headline)
            Picker("导出模式", selection: $selection) {
                Text("快速 · 无损流复制").tag(fast)
                Text("精确 · 有损重编码").tag(accurate)
                Text("转码 · 标准画质").tag(standard)
                Text("转码 · 720p 较小体积").tag(compact)
            }.labelsHidden().disabled(!editingEnabled).accessibilityIdentifier("export-strategy")
            Text(isReencoded ? "所选模式会重新编码。" : "实际范围可能扩展到关键帧。")
                .font(.caption).foregroundStyle(.secondary)
            actions()
        }
    }
}

struct StudioWorkspacePicker: View {
    @Binding var selection: Int

    var body: some View {
        Picker("工作区", selection: $selection) {
            Text("剪辑与转码").tag(0)
            Text("音源分离").tag(1)
        }
        .pickerStyle(.segmented)
        .labelsHidden()
        .accessibilityIdentifier("workspace-picker")
    }
}

struct StudioSidebarRow: View {
    let name: String
    let detail: String
    let icon: String
    let selected: Bool

    var body: some View {
        HStack(spacing: 9) {
            Image(systemName: icon).foregroundStyle(selected ? Color.accentColor : .secondary)
                .frame(width: 34, height: 34)
                .background(Color.accentColor.opacity(selected ? 0.14 : 0.06), in: RoundedRectangle(cornerRadius: 7))
            VStack(alignment: .leading, spacing: 3) {
                Text(name).lineLimit(1).truncationMode(.middle)
                Text(detail).font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
        }
        .padding(.vertical, 3)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
