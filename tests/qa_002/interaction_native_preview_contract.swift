import Foundation

@main struct AudioPreviewDecodeContract {
    static func main() throws {
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        let flags: [String: Any] = ["source_identity_unchanged": true, "full_decode": true, "finite_samples": true, "content_mapping_verified": true]
        let valid: [String: Any] = ["mock_only": false, "file_created": true, "preview_only": true, "profile": "wav_pcm_f32_preview_v1", "sample_rate": 44100, "channels": 1, "sample_count": 132300, "source_audio_origin_us": 25056, "source_content_duration_us": 3_000_000, "preview_validation": flags]
        var checks: [[String: Any]] = []
        func check(_ name: String, _ object: [String: Any], accepted: Bool) throws {
            var good = false
            do {
                let value = try decoder.decode(JobResult.self, from: JSONSerialization.data(withJSONObject: object))
                good = value.previewOnly == true && value.exportStrategy == nil
            } catch { good = false }
            checks.append(["name": name, "result": good == accepted ? "PASS" : "FAIL"])
        }
        try check("valid-private-preview-is-never-video-export", valid, accepted: true)
        for origin in [0, -5_000_000] { var value = valid; value["source_audio_origin_us"] = origin; try check("signed-source-origin-\(origin)", value, accepted: true) }
        let mutations: [(String, Any)] = [("preview_only", false), ("mock_only", true), ("file_created", false), ("profile", "anything"), ("sample_rate", 22050), ("channels", 6), ("sample_count", 0), ("sample_count", 26_460_001), ("source_content_duration_us", 3_001_000)]
        for (index, mutation) in mutations.enumerated() {
            var value = valid; value[mutation.0] = mutation.1
            try check("reject-field-\(mutation.0)-\(index)", value, accepted: false)
        }
        for key in ["source_audio_origin_us", "preview_validation", "sample_count"] { var value = valid; value.removeValue(forKey: key); try check("reject-missing-\(key)", value, accepted: false) }
        for key in flags.keys.sorted() { var invalid = flags; invalid[key] = false; var value = valid; value["preview_validation"] = invalid; try check("reject-unverified-\(key)", value, accepted: false) }
        let passed = checks.allSatisfy { $0["result"] as? String == "PASS" }
        let report: [String: Any] = ["passed": passed, "scope": "UNIT_TESTED production JobResult DTO decoding; crafted positive and negative messages, no FFI call or AVFoundation/GUI result", "checks": checks, "summaries": []]
        let data = try JSONSerialization.data(withJSONObject: report, options: [.sortedKeys])
        FileHandle.standardOutput.write(data); FileHandle.standardOutput.write(Data("\n".utf8)); exit(passed ? 0 : 1)
    }
}
