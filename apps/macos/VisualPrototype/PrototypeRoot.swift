import SwiftUI

struct PrototypeRoot: View {
    @State private var workspace = 0
    @State private var visibility: NavigationSplitViewVisibility = .all
    @State private var inspector = true
    @State private var details = false
    @State private var setting = 0
    var body: some View {
        StudioWorkspaceLayout(visibility: $visibility, inspectorPresented: $inspector) {
            PrototypeSidebar(audio: workspace == 1)
        } content: {
            if workspace == 0 { EditingSample() } else { AudioSample() }
        } inspector: {
            InspectorSample(audio: workspace == 1, tab: $setting)
        }
        .toolbar {
            ToolbarItem(placement: .principal) {
                StudioWorkspacePicker(selection: $workspace)
            }
            ToolbarItemGroup(placement: .primaryAction) {
                Button { details.toggle() } label: { Label("任务详情", systemImage: "list.bullet.rectangle") }
                Button { inspector.toggle() } label: { Label("检查器", systemImage: "sidebar.right") }
            }
        }
        .sheet(isPresented: $details) {
            VStack(alignment: .leading, spacing: 18) {
                HStack { Text("任务详情").font(.title2.bold()); Spacer(); Button("完成") { details = false } }
                Label("暂无进行中的任务", systemImage: "checkmark.circle").foregroundStyle(.secondary)
                Text("视觉样板展示任务面板的样式。 ").font(.callout).foregroundStyle(.secondary)
                Spacer()
            }.padding(24).frame(width: 480, height: 280)
        }
    }
}

struct PrototypeSidebar: View {
    let audio: Bool
    private let clips = [("海岸日记.mp4", "02:34", "mountain.2"), ("城市漫步.mov", "04:48", "building.2"), ("海岸航拍.mp4", "01:20", "water.waves")]
    var body: some View {
        List {
            Section { Label(audio ? "音频项目" : "我的项目", systemImage: audio ? "music.note.list" : "folder"); if !audio { Label("媒体库", systemImage: "square.stack.3d.up"); Label("最近使用", systemImage: "clock") } }
            Section(audio ? "当前音频" : "素材") {
                if audio { sidebarItem("Morning Light.wav", "04:26 · 已分离", "waveform", selected: true) }
                else { ForEach(Array(clips.enumerated()), id: \.offset) { i, c in sidebarItem(c.0, c.1, c.2, selected: i == 0) } }
            }
            Section("最近项目") {
                sidebarItem(audio ? "夏日记录.wav" : "旅行片段.mp4", audio ? "03:12 · 已分离" : "00:48", audio ? "waveform" : "film", selected: false)
            }
        }.listStyle(.sidebar).safeAreaInset(edge: .bottom) {
            HStack { Label("视觉样板", systemImage: "sparkles").font(.caption); Spacer(); Text("演示数据").font(.caption2) }.foregroundStyle(.secondary).padding(14)
        }
    }
    private func sidebarItem(_ name: String, _ detail: String, _ icon: String, selected: Bool) -> some View {
        StudioSidebarRow(name: name, detail: detail, icon: icon, selected: selected)
            .listRowBackground(selected ? Color.accentColor.opacity(0.15) : Color.clear)
    }
}

struct InspectorSample: View {
    let audio: Bool
    @Binding var tab: Int
    @State private var rotation = 0
    @State private var scale = 100
    @State private var offsetX = 0
    @State private var offsetY = 0
    @State private var start = "0.000000"
    @State private var end = "154.000000"
    @State private var exportMode = 0
    @State private var volume = 0.8
    var body: some View {
        StudioInspectorLayout(audio: audio, tab: $tab) {
                    if audio {
                        StudioSeparationSettings(modelName: "人声与伴奏 · UMX-HQ") {
                            Text("演示模型").font(.caption).foregroundStyle(.secondary)
                        } status: { EmptyView() }
                    } else {
                        if tab == 0 {
                            StudioTransformControls(rotation: $rotation, scale: $scale, offsetX: $offsetX, offsetY: $offsetY,
                                                    controlsEnabled: exportMode == 2,
                                                    resetEnabled: rotation != 0 || scale != 100 || offsetX != 0 || offsetY != 0,
                                                    showModeWarning: exportMode != 2 && (rotation != 0 || scale != 100 || offsetX != 0 || offsetY != 0)) {
                                rotation = 0; scale = 100; offsetX = 0; offsetY = 0
                            }
                            Divider()
                            StudioTrimControls(start: $start, end: $end, editingEnabled: true, pointsEnabled: true, issue: nil,
                                               setInPoint: { start = "42.318000" }, setOutPoint: { end = "42.318000" })
                        } else {
                            Text("音频设置").font(.headline); LabeledContent("音轨", value: "立体声 · AAC"); HStack { Text("音量"); Slider(value: $volume) }
                        }
                        Divider()
                        StudioExportControls(selection: $exportMode, fast: 0, accurate: 1, standard: 2, compact: 3,
                                             editingEnabled: true, isReencoded: exportMode != 0) {
                            Button("批量导出 1 份素材…") {}.disabled(true)
                        }
                    }
        } action: {
            Button {} label: { Label(audio ? "开始分离" : "选择位置并导出", systemImage: audio ? "waveform" : "square.and.arrow.up").frame(maxWidth: .infinity) }
                .buttonStyle(.borderedProminent).controlSize(.large).disabled(true)
        }
    }
}
