import Foundation
struct JobFailure: Codable, Sendable { let code: String; let message: String }
struct MediaRange: Codable, Sendable { let startUs: Int64; let endUs: Int64 }
struct MediaStream: Sendable { let codecType: String; let width: Int?; let height: Int? }
struct MediaInfo: Sendable { let streams: [MediaStream] }
struct BridgeError: Error { let code: Int; let operation: String }
enum ExportStrategy: Sendable { case copy; var transcodePreset: TranscodePreset? { nil }; var wireStrategy: String { "copy" }; var profile: String? { nil } }
@main struct Run {
 static func main() throws {
  let output = MediaInfo(streams: [MediaStream(codecType: "video", width: 180, height: 320)])
  let t = VideoTransform(rotationDegrees: 90, scalePercent: 100, offsetX: 20, offsetY: -10)
  func plan(_ rect: VisibleContentRect?, _ right: Int = 0) -> TranscodePlan {
   TranscodePlan(profile: TranscodePreset.standard.rawValue, contentWidth: 160, contentHeight: 310, canvasWidth: 180, canvasHeight: 320, paddingRight: right, paddingBottom: 10, selectedSourceAudioStreamIndices: [], maximumOutputFileBytes: 500, videoTransform: t, visibleContentRect: rect)
  }
  precondition(plan(VisibleContentRect(x: 20,y: 0,width: 160,height: 310)).matches(profile: TranscodePreset.standard.rawValue, output: output))
  precondition(!plan(nil).matches(profile: TranscodePreset.standard.rawValue, output: output))
  precondition(!plan(VisibleContentRect(x: Int.max,y: 0,width: 160,height: 310)).matches(profile: TranscodePreset.standard.rawValue, output: output))
  precondition(!plan(VisibleContentRect(x: 20,y: 0,width: 160,height: 310), 1).matches(profile: TranscodePreset.standard.rawValue, output: output))
  let source = URL(fileURLWithPath: "/tmp/source.mp4"), destination = URL(fileURLWithPath: "/tmp/output.mp4")
  let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase
  let old = QueueMediaRequest.transcode(source: source, destination: destination, range: nil, preset: .standard, audio: .all, videoTransform: nil)
  let new = QueueMediaRequest.transcode(source: source, destination: destination, range: nil, preset: .standard, audio: .all, videoTransform: t)
  let oldJSON = try JSONSerialization.jsonObject(with: encoder.encode(old)) as! [String: Any]
  let newJSON = try JSONSerialization.jsonObject(with: encoder.encode(new)) as! [String: Any]
  precondition(oldJSON["video_transform"] == nil)
  let transform = newJSON["video_transform"] as? [String: Int]
  precondition(transform?["rotation_degrees"] == 90 && transform?["offset_x"] == 20)
  print("transform bridge checks passed")
 }
}
