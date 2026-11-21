# ClipFlow Rust Core

Rust Core 负责媒体任务、文件处理和跨平台接口。公开接口及使用约束见 [Core 接口契约](../contracts/README.md)。

在仓库根目录执行以下命令，构建或测试需要 Rust 工具链及锁定依赖的本地缓存：

```sh
cargo build --manifest-path core/Cargo.toml --offline --locked
cargo test --manifest-path core/Cargo.toml --offline --locked
```

真实媒体处理需要显式配置可用的 FFmpeg 和 ffprobe；默认配置保留模拟后端。实际格式和功能支持取决于配置和组件。
