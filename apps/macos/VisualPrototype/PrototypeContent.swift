import SwiftUI
import AppKit

private let lake = NSImage(contentsOfFile: "/System/Library/Wallpapers/.default/DefaultAerial.jpg")

struct EditingSample: View {
    @State private var playing = false
    @State private var progress = 0.33
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 4) { Text("海岸日记.mp4").font(.title2.bold()); Text("视频素材 · 02:34").font(.callout).foregroundStyle(.secondary) }
                Spacer(); Text("H.264     3840 × 2160     30 FPS").font(.caption).foregroundStyle(.secondary)
            }.padding(.bottom, 4)
            StudioVideoStage {
                ZStack {
                    Color.black
                    if let lake { Image(nsImage: lake).resizable().aspectRatio(contentMode: .fill).frame(maxWidth: .infinity).clipped() }
                    else { Image(systemName: "mountain.2.fill").font(.system(size: 64)).foregroundStyle(.white.opacity(0.7)) }
                }
            } controls: {
                HStack(spacing: 16) {
                    Button { playing.toggle() } label: { Image(systemName: playing ? "pause.fill" : "play.fill") }.buttonStyle(.plain)
                    Text("00:42 / 02:34").font(.caption.monospacedDigit())
                    Slider(value: $progress).tint(.white)
                    Image(systemName: "speaker.wave.2")
                    Image(systemName: "arrow.up.left.and.arrow.down.right")
                }
            }
            StudioContentCard {
                HStack { Label("时间线", systemImage: "film").font(.headline); Spacer(); Image(systemName: "scissors"); Image(systemName: "arrow.uturn.backward"); Text("00:42.318").font(.caption.monospacedDigit()).padding(.horizontal, 10).padding(.vertical, 5).background(.quaternary, in: Capsule()) }
                HStack { Text("0:00"); Spacer(); Text("0:30"); Spacer(); Text("1:00"); Spacer(); Text("1:30"); Spacer(); Text("2:00"); Spacer(); Text("2:34") }.font(.caption2.monospacedDigit()).foregroundStyle(.secondary)
                GeometryReader { g in
                    ZStack(alignment: .leading) {
                        HStack(spacing: 1) { ForEach(0..<8) { _ in if let lake { Image(nsImage: lake).resizable().aspectRatio(contentMode: .fill).frame(width: max(1, g.size.width/8-1), height: 58).clipped() } } }.clipShape(RoundedRectangle(cornerRadius: 6))
                        RoundedRectangle(cornerRadius: 6).stroke(Color.accentColor, lineWidth: 3)
                        Rectangle().fill(Color.accentColor).frame(width: 2).offset(x: g.size.width * 0.33)
                    }
                }.frame(height: 58)
                HStack { Text("入点  00:00.000"); Spacer(); Text("选中  02:34"); Spacer(); Text("出点  02:34.000") }.font(.caption.monospacedDigit()).foregroundStyle(.secondary)
            }
        }
    }
}

struct AudioSample: View {
    @State private var playing = false
    @State private var progress = 0.35
    @State private var vocalsMuted = false
    @State private var vocalsSolo = false
    @State private var backingMuted = false
    @State private var backingSolo = false
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            StudioAudioHeading(title: "Morning Light.wav", subtitle: "WAV · 44.1 kHz · 立体声 · 04:26")
            StudioWaveformPanel {
                HStack { Text("0:00"); Spacer(); Text("1:00"); Spacer(); Text("2:00"); Spacer(); Text("3:00"); Spacer(); Text("4:26") }.font(.caption2.monospacedDigit()).foregroundStyle(.white.opacity(0.65)).padding(.leading, 135).padding(.horizontal, 18).padding(.top, 18).padding(.bottom, 10)
                track("原始音频", icon: "waveform", color: .white.opacity(0.7), seed: 1, controls: false, muted: $vocalsMuted, solo: $vocalsSolo)
                track("人声", icon: "mic", color: .blue, seed: 2, controls: true, muted: $vocalsMuted, solo: $vocalsSolo)
                track("伴奏", icon: "music.note", color: .mint, seed: 3, controls: true, muted: $backingMuted, solo: $backingSolo)
            }
            HStack(spacing: 12) {
                Image(systemName: "repeat").foregroundStyle(.secondary)
                Spacer(); Image(systemName: "backward.end.fill")
                Button { playing.toggle() } label: { Image(systemName: playing ? "pause.fill" : "play.fill").font(.headline).foregroundStyle(.white).frame(width: 42, height: 42).background(Color.accentColor, in: Circle()) }.buttonStyle(.plain)
                Image(systemName: "forward.end.fill"); Spacer()
                Text("01:28.560 / 04:26.000").font(.callout.monospacedDigit()).foregroundStyle(.secondary).fixedSize()
                Image(systemName: "speaker.wave.2"); Slider(value: $progress).frame(width: 80)
            }.padding(.horizontal, 10)
            StudioContentCard {
                HStack { Text("分离结果").font(.headline); Label("分离完成", systemImage: "checkmark.circle.fill").font(.caption).foregroundStyle(.green); Spacer(); Button("导出两条音轨…") {}.disabled(true) }
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 12) { result("人声", "mic", .accentColor); result("伴奏", "music.note", .mint) }
                    VStack(spacing: 12) { result("人声", "mic", .accentColor); result("伴奏", "music.note", .mint) }
                }
                Text("演示数据 · 控件用于展示视觉样式").font(.caption2).foregroundStyle(.secondary)
            }
        }
    }
    private func track(_ title: String, icon: String, color: Color, seed: Int, controls: Bool, muted: Binding<Bool>, solo: Binding<Bool>) -> some View {
        HStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 7) {
                Label(title, systemImage: icon).font(.callout.weight(.medium)).foregroundStyle(.white)
                if controls { HStack { Toggle("M", isOn: muted); Toggle("S", isOn: solo) }.toggleStyle(.button).controlSize(.mini) }
            }.frame(width: 110, alignment: .leading).padding(.leading, 18)
            WaveformSample(color: color, seed: seed).frame(height: 66).padding(.trailing, 18)
        }.frame(height: 79).background(.white.opacity(seed == 1 ? 0.025 : 0.045)).overlay(alignment: .bottom) { Rectangle().fill(.white.opacity(0.08)).frame(height: 1) }
    }
    private func result(_ title: String, _ icon: String, _ color: Color) -> some View {
        HStack(spacing: 12) { Image(systemName: icon).font(.title3).foregroundStyle(color).frame(width: 42, height: 42).background(color.opacity(0.12), in: RoundedRectangle(cornerRadius: 9)); VStack(alignment: .leading, spacing: 4) { Text(title).font(.callout.weight(.semibold)); Text("WAV · 44.1 kHz").font(.caption).foregroundStyle(.secondary) }; Spacer(); Image(systemName: "play.circle.fill").font(.title2).foregroundStyle(.secondary) }.padding(12).frame(maxWidth: .infinity).background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 9))
    }
}
struct WaveformSample: View {
    let color: Color; let seed: Int
    var body: some View {
        Canvas { ctx, size in
            var path = Path(); let count = 220
            for i in 0..<count {
                let x = CGFloat(i) * size.width / CGFloat(count-1)
                let envelope = 0.20 + 0.5 * abs(sin(Double(i) * 0.035 + Double(seed)))
                let modulation = 0.18 + 0.82 * abs(sin(Double(i * (seed + 3)) * 0.79))
                let h = CGFloat(envelope * modulation) * size.height * 0.47
                path.move(to: CGPoint(x: x, y: size.height/2-h)); path.addLine(to: CGPoint(x: x, y: size.height/2+h))
            }
            ctx.stroke(path, with: .color(color), lineWidth: 1.5)
            let x = size.width * 0.35; var line = Path(); line.move(to: CGPoint(x:x,y:0)); line.addLine(to: CGPoint(x:x,y:size.height)); ctx.stroke(line, with: .color(.white.opacity(0.8)), lineWidth: 1)
        }
    }
}
