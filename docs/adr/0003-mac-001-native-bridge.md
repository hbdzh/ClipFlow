# ADR-0003：MAC-001 本机 SwiftUI → Rust 薄桥接

- 日期：2026-11-09；状态：Accepted（Integration 对本次 Spike 范围的设计审核；实测结果另记）。
- 任务：MAC-001；依赖已合入 main 的 CORE-001，基线 `6d193a02f743067b4cbc6e318469b5c5418571b8`。

## 背景与范围

已有可调用的 CORE-001 mock C ABI，但 C/Python 调用不证明 SwiftUI 原生链路。MAC-001 只需最简窗口、Core version/capabilities、公共头导入和缓冲释放；不提前实现 UI-001、播放器、FFmpeg 业务或真实媒体处理。

本轮可用本机为 macOS 27.0.1 / arm64，Xcode 27.2、macOS SDK 27.2、Swift 6.4.2、Rust 1.99.0。工具版本的实际输出见 environment.json。Windows、Intel macOS、鸿蒙 PC 不共享该验证结果。

## 决定

1. 使用系统 SwiftUI/AppKit 构建本机最简窗口，Swift 编译器直接导入 canonical `contracts/media_core_v0.h`，不复制或另写一套 ABI 声明。公共契约/版本/schema/状态不变。
2. 类型安全 CoreBridge 独占并串行访问 engine，裸 handle 不传给 View/跨 actor 调用方；输入采用显式长度 UTF-8。输出解码前后均用 defer 确保 Rust `ms_buffer_free` 一次释放，失败不读取旧 buffer；关闭按现有 BUSY/cancel/poll 规则处理。
3. 阻塞/FFI 调用在后台桥接执行，SwiftUI 状态只由 MainActor 更新。actor 内部封装若需要 unchecked Sendable，必须明确封装对象的唯一访问者和 deinit/关闭约束，并经独立审阅，不能用该声明绕过并发安全。
4. 采用本机 `arm64-apple-macosx27.0` Spike 编译目标，仅是本轮可复现测试基线，**不设定产品最低支持 macOS，不声称 Intel 或旧系统兼容**。不引入第三方 Swift 包、不更新 Rust 依赖或 SDK。
5. 本地 `.app` 包含自己构建的 dylib，以相对 executable 的 Frameworks rpath 加载，避免依赖开发目录。若修改复制库的 install name，记录源/包内二进制的不同哈希。仅使用本地 ad-hoc 临时签名，不使用真实证书、公证或商店提交。
6. 启动时的缺失 dylib 若由 dyld 在 main 前拒绝，按启动失败记录，不描述成已存在的 UI 恢复流程；不为这个 Spike 另建动态加载框架。真正的包升级/签名/发行另立任务。
7. 本轮的 mock job/cancel 只能作为资源生命周期演示，不能变成真实媒体或临时文件清理能力。真实 probe/trim/重编码/AI 必须继续显示不可用。

## 候选与理由

直接在 View 里调用裸指针虽然代码更少，但无法清晰界定线程/释放方。将全部 Core 复制成 Swift mock 无法验证真实库加载。因此使用薄桥接和共享 Rust 实现。完整 Xcode 工程/播放器暂非必要；本轮可用受控脚本与系统 swiftc 编译，不新增项目生成器依赖。

## 验证与失败用例

- 同一 CoreBridge 供 headless self-test 与 SwiftUI 使用；实际 create → version/capabilities → mock/取消 → close，记录响应、退出码与产物哈希。
- 对非法 schema/参数、重复生命周期、活动任务关闭、缺失 dylib 等保留明确错误证据，不故意用悬空指针触发 UB。
- 本机实际启动 `.app`，观察版本和真实能力响应、手动操作和关闭；编译成功不替代 UI 实测。二进制架构、rpath、ad-hoc 签名、本地 bundle 加载分别记录。
- 独立复审 Swift 线程/所有权/释放和源码检查；内存分析/旧系统/Intel/沙盒/权限/预览未执行时必须列出。

## 后果与回滚

有本机 Swift 原生接入证据后可继续 UI-001 的前置准备，但 G1 仍须 Windows/鸿蒙真机。接口不迁移；若桥接或 bundle 失败，隔离修复平台代码，不修改 Core 媒体语义。正式发行前需重定支持系统/架构、签名/沙盒/安装与所有第三方分发义务。

来源：技术方案 §2/6/7、[契约](../../contracts/README.md)、MAC-001 任务卡、平台指令。实际结果见 MAC-001 报告。
