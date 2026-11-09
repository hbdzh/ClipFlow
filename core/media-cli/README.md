# Core 验证 CLI

在仓库根目录运行：

```sh
cargo run --manifest-path core/Cargo.toml --locked -p media-cli -- version
cargo run --manifest-path core/Cargo.toml --locked -p media-cli -- capabilities
cargo run --manifest-path core/Cargo.toml --locked -p media-cli -- mock succeed
cargo run --manifest-path core/Cargo.toml --locked -p media-cli -- capabilities --media
cargo run --manifest-path core/Cargo.toml --locked -p media-cli -- probe /absolute/source.mp4
cargo run --manifest-path core/Cargo.toml --locked -p media-cli -- trim /absolute/source.mp4 /absolute/output.mp4 1100000 2600000
```

`version`、默认 `capabilities` 和 `mock` 保持 CORE-001 的 mock 语义。`probe`、`trim` 和 `capabilities --media` 显式启用真实后端；它们从 `PATH` 查找 `ffmpeg` 和 `ffprobe`，也可设置以下环境变量。工具必须由调用方安装，本仓库不携带 FFmpeg 二进制。

| 环境变量 | 默认值或约束 |
| --- | --- |
| `MEDIASTUDIO_FFMPEG` | FFmpeg 可执行文件的绝对路径；未设置时查找 `PATH` |
| `MEDIASTUDIO_FFPROBE` | FFprobe 可执行文件的绝对路径；未设置时查找 `PATH` |
| `MEDIASTUDIO_TIMEOUT_MS` | `30000`；允许 `1..300000` |
| `MEDIASTUDIO_MAX_OUTPUT_BYTES` | `8388608`；允许 `1024..67108864` |

输入和输出路径可使用相对路径；CLI 依据当前目录转为绝对本地文件 URI，并正确编码 UTF-8、空格和特殊字符。参数通过参数数组交给后端，不经过 shell。路径必须为 UTF-8。目的文件不得已存在，后端不会覆盖源文件或已有目的文件。

`START_US`、`END_US` 是相对于规范化媒体时间线的整数微秒，须满足 `0 <= START_US < END_US`。当前只请求 `stream_copy`、`all_supported`，不允许回退重编码。实际保留范围受完整关键帧 GOP 限制，必须查看结果中的 `requested_range_us` 和 `actual_range_us`，不能把它当作任意帧精确剪辑。

`probe` 和 `trim` 可在末尾追加 `--cancel-after-ms N`，其中 `0 <= N <= 300000`。这是验证取消的客户端选项；若导出已提交，迟到的取消仍可能得到 `succeeded`。

CLI 向 stdout 输出一份 JSON，其中 `snapshots` 保存观察到的不同快照，`final` 是终态。真实任务只有 `succeeded` 返回退出码 0，`failed`、`cancelled` 和无效请求均返回非 0。失败详情位于 `final.error.code` 和 `final.error.message`；真实失败另带顶层数值 `error.code = -100`，作为旧 CLI 调用方兼容标记。默认 mock 的取消退出码仍为 0。

Core 中每个引擎最多运行一个真实媒体 worker；读取快照不会推动任务执行。后端超时从处理任务开始计算。CLI 退出时会请求取消并等待 worker 清理完成。此 CLI 用于开发验证，不代表 Windows、HarmonyOS 或完整播放观感已经验收。
