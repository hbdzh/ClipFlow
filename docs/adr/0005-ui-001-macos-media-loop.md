# ADR-0005：macOS 原生真实媒体闭环

- 日期：2026-11-09；状态：Accepted（Integration 审定；本机独立原生11项、2项partial观察及CUA走查通过，限当前受限子集）。
- UI-001 基线：`04f6647389bcb198f64b4db3e20fd9337cb3bdbc`；CORE-002 `8f26b39` 已合入并推送。

## 范围与唯一责任

| 层 | 本次责任 |
|---|---|
| SwiftUI / AppKit | 原生文件面板、单媒体工作区、入出点与请求范围、任务和结果反馈、打开/显示结果、错误恢复 |
| AVFoundation / AVKit | 原生播放/暂停/seek、预览就绪与失败、平台媒体时间与规范化选择位置的映射 |
| CoreBridge actor | 唯一 engine 所有者、强类型消息、后台串行 FFI、Rust buffer 释放、取消/轮询/关闭 |
| Rust Core（不改） | 探测、关键帧与 packet 规划、实际范围、任务状态、媒体进程与文件清理、完整验证和新文件提交 |

继续使用 CORE-002 可选配置及八个 C ABI，不改 schema、签名、错误码、所有权或 JSON 字段。新 Swift 类型只消费既有消息，不冻结另一套平台业务协议。预览 UI 位置不决定流复制落点；不在 Swift 选择关键帧或构造 FFmpeg 命令。

## 决定

1. 单文件、单区间、快速流复制首版；精确模式、转码、AI 显示未开放理由，不静默回退。未知进度采用不定进度；Core running 包括验证，不捏造独立 validating 或百分比。
2. 默认 mock 自测继续保留。GUI 显式连接真实 backend，可信工具来自宿主配置，交给 Core 检查；缺失时显示恢复说明，不下载工具。不能仅由后缀或 OS 判定可剪辑。
3. 使用系统 AVPlayer / AVPlayerView，不增加第三方播放器/Swift 包。UI 时间以 Core 首视频 PTS 规范化为零；实际 AVAsset 时间线须独立读取及实测映射，不假设原始容器 PTS 与播放器 timeline 完全一致。本机 empty-edit 实验中 offset 源 track.start=0、首 reader sample=0，均不代表实际视频起点；非空 segment target.start=5s 可定位真实视频区间，须配合目标帧内容复验，见 调查。预览失败与处理能力分别表达。
4. 输入通过系统选择；授权租期覆盖探测、预览与仍在运行的任务，替换/关闭先停止相关使用再释放。security-scoped start 返回 true 的每次调用对应一次 stop；非沙盒开发下 false 不能一概视为无权限，实际读写由媒体链路确认。
5. 目标必须新 MP4。系统保存面板不改变 Core 拒绝现存目标的语义，不能把面板“替换”当作覆盖授权。Core 使用同父目录 staging；本轮为非沙盒本地开发 App，保存 URL 授权不等于沙盒下父目录 staging 已获许可。沙盒/直接分发与父目录授权另做专项验收。
6. Core FFI 不在 MainActor；输出均复制后由 Rust free，不跨 actor 共享 handle。真实关闭先取消、异步轮询到终态再 destroy；BUSY 保留所有权可重试，不用 MAC-001 的四次 mock poll 假设。切换、取消和关闭须防迟到结果更新错误媒体，清理未确认不得显示已取消。
7. 仅 Core succeeded 且真实结果确认提交后开放结果动作；展示用户请求范围、实际范围与偏差。失败的错误码放详情，主界面给可恢复说明。临时文件/源码保护仍由 Core 负责，UI 不擅自删除或覆盖路径。
8. 沿用现有 arm64/macOS 27.0 开发目标、Swift 6 严格并发、本地 ad-hoc 签名；不修改产品最低系统、不引入注册依赖或 FFmpeg 二进制、不使用真实证书/公证/商店发布。

## 先行验收与失败案例

成功：同一生产 ViewModel/bridge → 探测 → AVPlayer 就绪/seek → 选区 → 导出 → Core 终态 → 独立 ffprobe/解码/帧包时间校验。另用 CUA 实际原生面板与鼠标/键盘走查，二者证据分开，不能用 CLI 代替 UI。

失败至少包含：坏输入后重选、无效范围阻止提交、已有目标/源别名不覆盖、依赖缺失、取消先请求后清理、活动退出等待回收。使用受控挂起工具只作失败注入；成功媒体必须来自真实 FFmpeg。独立 QA 复审线程/FFI/URL 租期/关闭竞态，保留失败与修正证据。


## 限制与来源

本轮仅本机 arm64，不等于 Windows/Intel macOS/HarmonyOS 或完整 G1/G2。未验收的媒体格式/字幕/Alpha/色彩保真与发行仍保持未验证。AVPlayer 能播放不证明导出保真。

来源：任务卡、技术方案 §2/6/7、[既有媒体契约](../../contracts/CORE-002.md)、产品流程、[Apple security-scoped URL](https://developer.apple.com/documentation/foundation/nsurl/startaccessingsecurityscopedresource())、[AVAssetTrack 时间范围](https://developer.apple.com/documentation/avfoundation/avassettrack/timerange)。具体设备、构建、操作与输出以 UI-001 实测报告为准。

独立预览定位检查首轮全RGB阈值失败后，经 调查 审定保留RGB最近帧约束与全部指标，使用全分辨率BT.709权重亮度MSE≤原144定位门槛及0/32/34错帧负例；色彩保真仍未验证。执行证据与风险见 最终交付。
