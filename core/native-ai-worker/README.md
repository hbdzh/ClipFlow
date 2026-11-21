# 原生 AI worker（受控开发）

这是 MAC-004 的实验性 CPU worker，运行时不使用 Python。它使用固定模型及固定子图，分块执行频谱变换和有状态双向 LSTM，在 Core 私有 staging 内写入 `vocals.wav`、`accompaniment.wav`。参数是绝对路径：`--model`、`--graphs`、`--runtime`、`--input`、`--staging`；结果为 AI-001 的单行 JSON。Core 仍负责进程、资源和发布。

依赖由独立 `Cargo.lock` 固定，开发环境通过 `cargo build --release --offline --locked --manifest-path core/native-ai-worker/Cargo.toml` 构建。直接依赖声明：`ort` 2.0.0-rc.15、`rustfft` 6.4.1、`sha2` 0.10.9 为 MIT OR Apache-2.0，`hound` 3.5.1 为 Apache-2.0；来源见各包 Cargo 元数据及 lockfile。实际 ONNX Runtime 二进制、传递依赖及分发义务尚未完成发行审查。

开发时可用仓库的 `tools/prepare_graphs.py` 和 ONNX 1.18.0 从固定原始模型离线生成八个子图；脚本使用 Python 仅用于一次性开发准备，不由应用或 worker 调用。模型和子图不入库，程序使用固定 SHA-256 校验模型与各子图，并将校验后的子图字节直接交给 ONNX Runtime。需分发时应将它们预生成并审查来源与许可证。`ort` 必须使用 `api-21`（不启用会触发开发机 NPU 路径的 `api-22`），本机实验加载 ONNX Runtime 1.22.1；这不代表其他系统或 ORT 构建均已验证。

本地针对性对照：使用授权人声/伴奏混音 3 秒和 8 秒片段，其中 8 秒跨越 256 帧块边界。独立检查器确认两条普通 WAV 均为 44.1 kHz 双声道 float，完整 FFmpeg 解码通过，采样数与输入一致；8 秒人声/伴奏与原 worker 相对 RMS 差分别为 `2.99934e-7` / `4.74508e-8`。这不证明主观听感、所有输入格式、10 分钟场景、沙盒安装包、签名或发行已验证。
