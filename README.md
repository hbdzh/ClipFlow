<p align="center">
  <img src="apps/macos/Resources/ClipFlow.png" alt="ClipFlow 图标" width="120">
</p>

<h1 align="center">ClipFlow</h1>

<p align="center">剪辑视频、转换格式、分离人声与伴奏。</p>

ClipFlow 是一款原生桌面音视频工具，将常用的媒体处理功能放在同一个工作区。

## 功能

- **视频剪辑**：预览视频、选取片段，快速导出或精确剪辑。
- **视频转码**：选择导出预设和音轨，转换视频。
- **批量处理**：管理多个素材和导出任务，支持取消与重试。
- **音源分离**：分离人声与伴奏，试听并导出音轨。

快速剪辑使用流复制，实际边界受关键帧影响；精确剪辑需要重新编码。

## 开发状态

ClipFlow 正在开发中，目前提供 macOS 本机开发版，尚无正式发行版。Windows 和鸿蒙 PC 版本计划后续开发。

## 从源码运行

macOS 开发版需要 Xcode、Python 3、Rust 工具链及预先准备好的媒体和 AI 组件。在仓库根目录执行：

```sh
./script/build_and_run.sh full --verify
```

组件准备、构建和使用方法请参阅 [macOS 开发说明](apps/macos/README.md)。
