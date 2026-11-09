# MAC-003 原生队列独立 QA

使用生产 `WorkspaceModel` / `CoreBridge` / Rust C ABI 的 `--queue-self-test` 场景入口。实际输出另经独立 ffprobe、全流解码、PTS/DTS、尺寸、语言/声道和生成音调比对；通过条件见 [ACCEPTANCE.md](ACCEPTANCE.md)。

```sh
python3 -B tests/mac_003/run_acceptance.py --binary /absolute/MediaStudioSpike.app/Contents/MacOS/MediaStudioSpike --label native-stable-v1
```

每次采用新 label，不覆盖失败证据。合成媒体与逐场景 stdout 存在忽略的 `tests/mac_003/build/<label>/`；可追溯摘要写入 `docs/mac-003/evidence/independent-<label>.json`。命令存在不代表已通过，结论以独立报告为准。
