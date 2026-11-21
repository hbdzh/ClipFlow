# ClipFlow macOS 开发版

## 构建和运行

在仓库根目录执行：

```sh
./script/build_and_run.sh full --verify
```

需要 Xcode/macOS SDK、Python 3、项目锁定的 Rust 工具链，以及预先准备好的 FFmpeg、ffprobe、ONNX Runtime、模型和子图。默认从本机 `apps/macos/build/MAC-005/MediaStudio-QA002-Native-Closure-v6.app` 复用组件；可用 `--components-app /绝对路径/组件包.app` 指定其他组件包。构建脚本不会自动下载这些组件。准备方式见 [组件构建说明](../../docs/mac-005/BUILD.md)。

构建产物位于 `apps/macos/build/unified/` 的独立目录。`--build-only` 只构建；`--verify` 仅检查启动后的进程。开发包采用 ad-hoc 签名，未公证，不是正式发行包。

## 使用

在视频工作区导入本地素材，设置剪辑范围或选择转码预设和音轨，然后选择导出位置。快速剪辑使用流复制，实际边界受关键帧影响；精确剪辑需要重新编码。音源分离工作区可导入音频、分离人声和伴奏、试听并导出音轨。具体可用能力取决于素材和已准备的组件。
