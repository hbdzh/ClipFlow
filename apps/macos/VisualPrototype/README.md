# ClipFlow 视觉样板

独立的 SwiftUI 视觉原型，不连接 Rust Core。文件名、时长、波形、状态为演示数据；分离和导出按钮禁用。播放按钮只切换图标，并不播放媒体。系统壁纸只用于本机预览，不打包进项目。

在装有 Xcode 命令行工具的 macOS 上，从仓库根目录运行统一入口：

```sh
./script/build_and_run.sh prototype --verify
```
