# ADR-0004：CORE-002 显式媒体后端、异步任务与安全流复制

- 日期：2026-11-09；状态：Accepted（Integration + Core 联合审定，独立 QA 43 项最终验收通过，限本机首版子集）。
- 任务：CORE-002；基线 `2410b59ef7b153c7274d4e2f677b648c037ca6d0`，包含已推送的 MAC-001 / QA-001。
- 相关来源：技术方案 §4/5/7、任务卡、QA-001 边界、[V0 契约](../../contracts/README.md)。

## 背景

CORE-001 只有 poll-driven mock。QA-001 证明固定 FFmpeg 参考可运行，但原简单 `-ss/-t` 会在 B 帧尾部跳过内容；其 1.45 秒参考方案只绑定一个输入/工具，不能当通用算法。MAC-001 的默认配置必须继续有相同 mock 语义。HOS-001 缺真实 PC/SDK/进程许可反馈，不能假设三端均允许运行 helper。

## 决定与责任边界

1. 保持八个 C ABI 函数、`ms_buffer` 布局和分配/释放方；默认 engine 继续 mock-only，保留原样例/错误/状态测试。可选 config `media_backend` 显式启用真实后端，添加兼容 JSON 操作而不静默改变旧客户端行为。
2. domain 验证版本、严格字段、file URI、整数微秒和显式策略；orchestrator 独占状态/队列/取消/关闭；media-ffmpeg 独占版本检查、命令数组、时间基/关键帧计划、文件安全与实际结果验证；CLI 调同一 Engine。UI 不参与 FFmpeg 决策。本轮不改原生端业务。
3. 每 engine 懒启动一个 media worker，有界队列 + try_send，FFI 提交不等待媒体执行。真实 snapshot 只观察，绝不靠轮询推进媒体；mock 保持原行为。queued 取消可直接 cancelled（未拥有媒体资源）；active 取消先请求，退出/清理确认后终态。已完成原子提交的取消竞态保留 succeeded，不能把已交付文件假报为 cancelled。
4. Drop 发取消/停止并 join worker，FFI destroy 仍在非终态时 BUSY 不销毁；quarantine 的销毁也走 cleanup/join。关闭可能等待进程退出，必须由后台桥接执行。进程用 Command 参数数组与独立 stdin/stdout/stderr；超时/取消/输出上限时 kill + wait，reader 有界保存并持续 drain，所有早退/unwind 用 RAII 回收。
5. 首版 trim 只接纳有界 MP4/MOV 中单 H.264 视频及最多限定 AAC 音轨（含无音轨）；probe 可报告其中其他单视频。trim 不支持的流/格式拒绝，不静默丢轨/重编码；旋转/Display Matrix/side data 与明确颜色标签待黄金保留验收，probe 给出限制、trim 明确拒绝。具体 limits 由能力报告公开。工具配置属于宿主信任配置，本轮只实测本机既有 FFmpeg/ffprobe 7.1.1；runtime 接纳主版本 7，不据此声称全部 7.x 通过。媒体路径只接受普通本地文件，固定 file+mov 白名单，并禁 MOV 外部数据引用/绝对外部路径与网络/playlist处理；不承诺对任意恶意 helper/fork 树提供沙盒。
6. 范围是相对**首视频 PTS**规范化后的整数微秒。选择包围区间的前/后关键帧（或 EOF），候选尾截止来自实际 packet DTS/time_base，不写死 QA-001 常数。对非零源 PTS 使用原始时间戳 seek，并验证实际内容；公开 requested/actual/raw-source 范围、time_base/PTS 与微秒舍入，不宣称任意帧精确无损。
7. 输出必须通过流布局/codec、解码、DTS/时序、逐帧内容连续与压缩 packet 来源等检查，不能只看文件/退出码。候选数量、子进程输出与时间均有限；无法证明完整计划时 SEEK_NOT_EXACT，不交付不完整产物。结果摘要最多 128 KiB，并在输出提交前验证，保留 256 任务仍有界，不在成功提交后因预算简单丢弃结果。
8. 源文件只读；检查身份/长度/mtime，拒绝现存目的文件及源别名。目标同目录私有 staging，通过验证才用 create-new hard_link 发布，不用可能覆盖的 rename；失败/取消/超时显式清理，只移除自己拥有的文件。硬链接/临时目录支持不足时拒绝，不回退覆盖。预先别名与提交碰撞有防护，不声称抵御同用户恶意进程所有 TOCTOU。
9. 唯一依赖图变化是 media-ffmpeg → 已有 path media-domain；registry 版本/checksum 和 Rust 1.99.0 不变，无新 crate、FFmpeg 二进制或模型入库。现有 GPL 工具只是外部开发运行时；正式打包/许可、SDK最低版本与平台支持没有因此通过。

## 契约与失败路径

具体 config/request/result/state 见 contracts/CORE-002.md。输入仍按 V0 Value 采用重复键后值覆盖；不伪称拒绝重复键。严格类型/未知字段与不接受额外 FFmpeg 参数阻断用户自定义命令；更高层协议冻结前仍需评估重复键策略。

先由 QA 写外部失败/契约用例、Core 写 domain/状态/后端用例，再实现。成功路径：显式后端 create → submit probe/trim → worker 验证 → terminal JSON → free/destroy。失败包括损坏/无依赖/范围/URI、现存/别名输出、取消/超时/管道超限、输出校验和清理失败。所有权、线程、命令生成和覆盖行为须独立复审。

## 实测前的范围限制与回滚

本机实现只给 macOS arm64 证据。Windows/鸿蒙（尤其 FFmpeg helper 与文件授权）仍未验证；不能宣布 G1/G2 完整通过。若真实管线失败，保留默认 mock 和已验收平台 Spike，不让原生客户端各自绕过 Core。生产级资源策略、格式拓展、持久恢复、真实设备/安装验收另做。

命令语义参考 [FFmpeg n7.1.1 官方文档源](https://raw.githubusercontent.com/FFmpeg/FFmpeg/n7.1.1/doc/ffmpeg.texi)、[ffprobe 官方文档源](https://raw.githubusercontent.com/FFmpeg/FFmpeg/n7.1.1/doc/ffprobe.texi)。边界和非零 PTS 策略依据本轮实际帧/包实验及独立输出校验，不能以文档替代实测。
