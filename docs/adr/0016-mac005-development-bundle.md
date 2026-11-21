# ADR-0016：macOS 受控开发包与内置组件

日期：2026-11-13。状态：**本机开发包，发行未接收**。本决定覆盖 MAC-005 的 macOS arm64 受控开发构建；安装和使用证据见 [MAC-005 报告](../mac-005/REPORT.md)，依赖审查见 [许可复核](../mac-005/LICENSE-REVIEW.md)。

## 布局与职责

| 部分 | 位置及职责 |
| --- | --- |
| macOS UI 与 Rust Core | App 主程序及 `Contents/Frameworks/libmedia_ffi.dylib`；所有媒体决策、队列和取消语义继续由 Core 管理。 |
| FFmpeg / ffprobe | `Contents/MacOS/` 的签名 helper；默认从包内定位，开发环境显式覆盖仍可使用。现有精确编码仍使用 Core 固定的 libx264/AAC 方案。 |
| 原生分离 | `Contents/MacOS/media-ai-worker`、`Contents/Frameworks/libonnxruntime.dylib` 和 `Contents/Resources/ai/` 中固定模型及图；运行时不调用 Python。 |
| 许可材料 | `Contents/Resources/licenses/` 收集 ORT、模型、FFmpeg、x264 与 Cargo 锁文件对应许可证和声明；模型、动态库和外部二进制均不入 Git。 |

开发包使用 ad-hoc 签名和 App Sandbox，文件经系统面板授权；不是正式签名、公证或商店发行方案。源代码、模型及依赖获取、版本、哈希和开发构建步骤见 [构建说明](../mac-005/BUILD.md)。构建步骤可再次执行不等于二进制逐字节可复现；两次 FFmpeg 构建因构建路径等信息导致散列不同。

## 许可决定

当前 FFmpeg 7.1.1 和静态链接 x264 的构建启用 GPL，实际工具自报 GPL v2 or later。其适用义务、模型权利链、ORT 静态内容及通告、各 Rust crate 和最终分发渠道分别审查；不能根据子进程隔离或某个顶层 MIT 声明推导整个产品可按特定许可发行。当前决策仅允许有条件的本机受控开发，对外提供安装包及正式发行均为 **NO_GO**，待实际工件的来源、完整源码、许可履行和渠道条件复核。

## 未覆盖

本机验证只对已执行的 macOS arm64 场景有效。Intel、其他系统版本、正式证书、公证、升级、Mac App Store、完整格式、音画主观质量和生产许可均不得由开发包成功推断；遗留项目按任务报告保留。FFmpeg 网络协议在此构建中禁用，不代表已验证全部本地格式。
