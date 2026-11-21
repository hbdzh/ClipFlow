# ADR-0001：初始架构、工具链基线与验证边界

- 状态：Accepted（Integration 对既有架构基线和本次 Rust 锁定的审阅）；V0 ABI 尚未冻结，平台能力未验证。
- 日期：2026-11-09（Asia/Shanghai）。
- 关联任务：INT-001；后续 PROD-001、CORE-001、QA-001、WIN/MAC/HOS-001。
- 决策来源：技术方案 §2–7、§8–12，根指令、首批任务卡、[契约说明](../../contracts/README.md)、本机证据。本 ADR 不引入新的 SDK 最低支持版本或已验证平台能力。

## 背景与已知事实

仓库仍是起步文档与声明草案；没有 Rust Core 二进制或原生 App。当前只有 macOS arm64 开发主机可直接使用，Windows VM 枚举失败，HDC 无连接目标。DevEco/SDK 安装、Rust OpenHarmony target、HarmonyOS PC 真机运行属于不同层级的证据。


## 候选方案

| 方案 | 收益 | 成本与风险 | 本次结论 |
|---|---|---|---|
| 三端分别处理媒体业务 | 起步时可少写共享层 | 模式、时间戳、取消、文件安全语义会分叉；违背既定边界 | 不采用 |
| 原生 UI + Rust Core + 初期外部 FFmpeg | 原生交互、统一任务语义、媒体进程故障可隔离；CLI 可复验 | 需要 FFI 所有权、线程、进程和打包验证 | 沿用现有基线 |
| 一开始三端统一静态链接 libav | 减少运行时外部进程依赖 | 初期 native ABI、构建配置和再分发审查范围扩大；尚无真机证据 | 作为平台适配后备，不立即引入 |
| 先做单平台 UI 样机 | 容易展示交互 | 无法消除鸿蒙 PC 技术风险 | PROD-001 可用 mock；实际三端完整 UI 等 G1 |

没有选择更少前置工作的三端独立媒体实现，是因为项目要求统一业务语义，且鸿蒙进程/文件能力和 FFI 生命周期必须先有证据。

## 决定与责任边界

| 行为 | 唯一业务所有者 | 平台配合与目录 |
|---|---|---|
| 窗口、菜单、快捷键、文件选择与授权、原生预览 | 原生客户端 | `apps/windows`、`apps/macos`、`apps/harmonyos`；向 Core 传递已授权资源 |
| 时间范围、模式、输入规则、标准媒体对象 | Rust domain | `core/media-domain`；UI 只呈现 Core 的决定 |
| 作业状态、排队、取消、退出、清理、进度 | Rust orchestrator | `core/media-orchestrator`；后台执行，UI 轮询 |
| ffprobe/FFmpeg 参数和媒体输出验证 | Core 媒体适配器 | `core/media-ffmpeg`；参数数组、受控临时文件；必要时替换为平台库适配 |
| C ABI 所有权、错误与 JSON 封送 | Rust FFI + 平台薄封装 | `core/media-ffi`；公共规范 `bridges/`；三端不各自定义媒体协议 |
| 无 UI 的同语义入口 | Rust CLI | `core/media-cli` |
| 公共契约与依赖入库审阅 | Integration/Core | `contracts/`；平台仅提出变更请求 |
| 独立媒体结果验收 | QA | `tests/`；不能改黄金预期来迎合实现 |

1. 保持 Windows C#/WinUI 3、macOS Swift/SwiftUI+AppKit、鸿蒙 PC ArkTS/ArkUI；媒体业务统一进 Rust。算法复用 FFmpeg，不重写编解码器。
2. 初期 Core 下游优先隔离的 ffprobe/FFmpeg 进程；鸿蒙 helper、文件权限或分发不通过时，只替换媒体适配层，评估 Native/libav，并另作许可/接口 ADR。客户端不得绕过 Core。
3. 快速模式是流复制，接受经说明的关键帧/封装边界限制；精确模式需要重编码。G2 不承诺 smart-cut，不静默切换策略。转码/AI 留待后续门禁。
4. 根 `rust-toolchain.toml` 固定已安装的 Rust 1.99.0、minimal、clippy/rustfmt；不使用 stable alias 作为锁。选它是因为本机已有精确版本且组件可调用，无须升级全局工具链。实际 fmt/clippy/test 待 CORE-001。其他工具是环境记录，平台最低系统与工程锁定尚待真实 Spike。
5. 源码许可沿用现有审查规则：本轮不新增开源授权或更改商业发行策略。当前无根 LICENSE，项目对外授权方案待所有者决定；不得默认套用 MIT、Apache 或 GPL。禁止复制未经审查的参考项目源码/素材/模型；第三方逐项审查来源、版本、SPDX/条款、链接形式、构建参数、分发权及义务。
6. 本机 FFmpeg 输出明确包含 GPL 构建开关，`-L` 声明 GPL v3-or-later。它只是开发机外部工具，未入库；随包分发、静态/动态链接和最终应用许可结论仍未审查。本轮发行结论为 NO_GO，不用 CLI 隔离来假定许可问题已经解决。

## V0 ABI 审阅结论：草案待补齐

以下是实现前提案，不是当前已实现行为，也不是对既有签名的单方面修改。

| 函数 | 已声明的所有权 | 必须补齐的线程/错误约定 |
|---|---|---|
| `ms_engine_create` | 借用 config 字节；输出 opaque engine；由 destroy 释放 | 明确配置 JSON、长度上限、空输入语义、失败时 out_engine 置空；拷贝输入后才能返回 |
| `ms_engine_destroy` | 消耗有效 engine 句柄 | 调用方先停止并等待任务；后台调用；明确等待/超时、清理完成条件及与其他调用的排他规则 |
| `ms_get_version` | 借用 engine；输出 Rust buffer | 定义响应字段；失败时输出归零；明确是否可并发 |
| `ms_get_capabilities` | 同上 | mock 阶段不得把真实 probe/trim 标成 available；需要实际能力探测 |
| `ms_submit_job` | 借用请求字节；输出 Rust buffer | 明确返回前拷贝、响应 job_id、schema/未知字段、输入限制、队列错误 |
| `ms_get_job_snapshot` | 借用 job_id 字节；输出 Rust buffer | 定义 job_id 编码、未知 ID、终态保存期限、轮询和关闭竞态 |
| `ms_cancel_job` | 借用 job_id；无输出 buffer | 明确重复取消/终态取消语义；返回成功不能被误解为文件清理已完成；终态由 snapshot 确认 |
| `ms_buffer_free` | 释放由 Rust 分配的原始 buffer，一次且仅一次 | 明确空 buffer 是否可释放；禁止修改 ptr/len、重复释放或释放期间被其他线程访问 |

统一约束提案：所有输入只借用至函数返回；输出由 Rust 分配并只经 `ms_buffer_free` 释放；平台封装在调用后保证释放。engine 调用先由平台封装串行化，UI 主线程不执行可能等待的 FFI；destroy 与所有借用调用互斥。若将来支持同一 handle 并发，需要明确 Core 的同步与取消竞态契约并测试。

空输出指针、非法 UTF-8、schema 不支持和超大 JSON 的失败行为，错误详情取得方式、空 buffer、结构体布局、整数时间范围、URI 授权与路径转换、作业状态图均须补充。非空悬空指针/重复销毁属于调用方违规，不能承诺仅靠裸指针就安全返回 INVALID_HANDLE。panic 边界需转换为受控错误；abort/进程崩溃不能假装可由 catch_unwind 捕获。

建议状态图由 Core/Integration 联合审定：queued → running → succeeded/failed；取消请求进入 cancelling → cancelled，cancelled 只能在子进程退出和临时文件清理后出现。边界竞态及终态重复取消必须有测试；目前样例只给出 running，不能视为完整协议。

JSON 候选语义沿用现有 trim 样例的 `schema_version: 0`、`start_us/end_us` 整数微秒、`strategy: stream_copy`、禁止重编码回退；开始/结束范围及未知字段策略待冻结。禁止把浮点秒作为唯一时间真值。任何后续破坏变更需在 V0 冻结前审定并更新配套样例/契约测试；冻结后按 major 兼容策略迁移，不悄悄改布局。

## 首个验证用例与失败用例

以下是待 CORE-001/QA-001 实现的验收，不是本次测试通过项。

| 路径 | 输入/触发 | 期望语义与证据 |
|---|---|---|
| 成功 | schema 0 配置创建 engine → version/capabilities → 提交 mock job → snapshot 到终态 → 释放 buffers → destroy | 所有响应有 schema_version；mock 不宣称有真实媒体能力；记录原始响应、状态、ABI 布局和重复生命周期运行结果 |
| 失败 1 | `ms_submit_job` 收到非法 UTF-8，例如单字节 FF | 返回 `MS_E_INVALID_UTF8`；不启动任务，输出安全可判断；由契约失败测试验证，无 panic 越界 |
| 失败 2 | 合法 JSON 的 `schema_version` 为 999 | 返回 `MS_E_UNSUPPORTED_SCHEMA`；不启动任务，无输出泄漏；保存错误与检查命令 |
| 失败 3 | 长任务取消并与轮询/关闭相遇 | 状态可追踪，源文件不变、临时文件安全清理，关闭等待不阻塞 UI；由后续媒体集和 FFI 测试验证 |

## 后果、迁移与回滚

本次没有协议迁移。若选定 Rust 版本导致可重复失败，Integration 记录原因后修改精确版本并重跑检查，不改为漂移 alias。若鸿蒙适配不可行，先记录失败层级与替代 Adapter，保留共享 domain/orchestrator。任何新依赖、SDK 最低支持或分发策略变更需 ADR 与 Integration 审阅。

并行条件：INT-001 文档可供审阅后，PROD-001 和 QA-001 的准备可独立进行；CORE-001 的 ABI 实现须先 Core/Integration 联合审定公共契约与失败用例。平台实际调用依赖 CORE-001 构建物；HOS-001 可先做设备/SDK 准备，Rust 调用不得跳过 Stage/Native 基础验证。目录归属与依赖见 Worktree 计划。

## 验证证据与审核边界

环境清单 与其原始证据记录实测命令。起步包结构/JSON/C 头语法通过；Rust 精确工具链选择生效。三端应用、媒体处理、FFI 运行、取消/内存安全、安装与 SDK 真机匹配均未验证。Integration 已审阅本轮架构与锁定范围；Core 联合 ABI 审阅、独立 QA/CI 尚未执行，不标记 G0/G1 通过或任务独立验收 Done。
