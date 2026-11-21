# MAC-005 独立定向验收

日期：2026-11-13。macOS 27.0.1（26A434），arm64。本轮仅验证该开发包的本机安装副本、真实 GUI 音频及一条 Core 精确重编码路径，没有运行全量测试。

## 安装副本和签名

从 `apps/macos/build/MAC-005/MediaStudio-MAC005.app` 使用 `ditto` 复制至 `/private/tmp/mediastudio-mac005-qa/Installed/MediaStudio-MAC005.app`，未覆盖已有应用。安装副本主程序与工作区构建相同，SHA-256 `ff378ac3eb11e5224d880d86fdd845266621a1a836dd1371acf68ffd0317e157`。`codesign --verify --deep --strict --verbose=2` 对该副本及嵌入 helper、动态库通过；签名为 ad-hoc、TeamIdentifier 不存在，未使用真实证书。App Sandbox、user-selected read-write 和 app-scoped bookmark entitlement 存在。这里的安装是将 `.app` 复制到独立本地目录；不是 `.pkg` 安装、App Store、干净系统或公证验证。

启动安装副本后，GUI “媒体组件”显示默认 FFmpeg 和 ffprobe 均位于该安装副本的 `Contents/MacOS`，未设置 `MEDIASTUDIO_AI_*` 或媒体工具覆盖。包内签名 FFmpeg SHA-256 `f4d051535c9db13a3f6c66a5934d6a265f4bec27c603d74d9844d8106d8c57f3`、ffprobe `4d64f123c2eab385dd86aca93cdf88942c8351db4dd26270a026646e0dcbd32e`，worker `afe29d355861a5a3c0d6f1feae2585df3de56ce9a10bd0d3d9b9ca64073f7b26`。

## 真实音频 GUI

通过系统文件面板导入授权的人声和钢琴混音 3 秒样本，输入 SHA-256 `91362d158b201c41eff2fd2c5fc84d6284ae3557e0bb120683083737946259ca`，来源及许可边界见 AI-001 音频记录。界面显示组件已配置，点击开始后显示分离完成；人声音轨播放状态推进到约 0.2 秒。经文件夹授权和 Save Panel 将两轨导出至测试目录，界面显示“两条音轨已导出”。伴奏播放状态、本机扬声器出声及主观听感本轮未测。

独立 `tests/ai_001/verify_audio.py` 使用本机独立 FFmpeg/ffprobe 核对导出：两条均是 44.1 kHz 双声道 float PCM WAV、132000 帧、采样值有限、全段解码退出 0；源哈希不变。人声 SHA-256 `55f9e0a187ddbe88e4d0c0498cbfa51f36dac841654d29e77535c7c569c8e854`，伴奏 `63bc9112a5e432941f8adc9a245fbdcf57fa7b3424f3bba4ee4df13f839becdd`。与此前固定参考相比，相对 RMS 误差分别 `1.12644e-7` / `1.16011e-7`，最大绝对差均 `7.45058e-8`。检查脚本使用 Python 仅用于独立离线验证，不是安装包的运行依赖。

## 视频导入与精确重编码

使用本地 lavfi 生成的 3 秒 H.264/AAC 色图和正弦音（SHA-256 `97cff2dc9fab300703ebf0fd2b8a62f9be4555b647773ae671ff815f0ac67d56`）。通过安装副本 GUI 文件面板导入后，界面显示 3 秒、H.264 320×180 和 AAC，说明实际探测完成。自动化未能可靠选择短暂的模式菜单，故没有声称 GUI 精确导出通过。

随后将该合成素材的副本放进此测试应用的沙盒容器，以安装副本可执行程序运行一次 `--queue-self-test` 生产 `WorkspaceModel` / Core / FFmpeg 真实任务；通过 `env -i PATH=/usr/bin:/bin` 排除开发机 `MEDIASTUDIO_*` 配置。场景请求 `0.4–2.4` 秒、`accurateReencode`，返回 `passed=true`、真实作业 `succeeded`，Core 报告 libx264、实际区间 `0.4–2.4` 秒和 AAC；输入哈希不变。

独立主机 ffprobe 检查输出：H.264 320×180、50 帧、AAC 48 kHz 单声道，容器 2.000 秒；独立 FFmpeg 同时全段解码音视频退出 0。输出 SHA-256 `8e4499e7506856875709e658a14cdb9da8279ac2fcbf4ff5216cef1741159ba2`。该条结果是生产 model 的单场景自检和独立媒体检查，不是 GUI 选模式和导出验证。

## 仍未验证

干净机器、Finder 拖放到 `/Applications`、安装器/卸载工具、重签升级、正式签名及公证、Gatekeeper 分发、其他 macOS 架构与操作系统版本、GUI 精确导出、沙盒取消/崩溃清理、主观听感和长期性能均未验证。许可证审查见 [LICENSE-REVIEW](LICENSE-REVIEW.md)；本结果不等于发行许可完成。
