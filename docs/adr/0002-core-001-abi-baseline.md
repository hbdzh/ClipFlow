# ADR-0002：CORE-001 mock ABI、错误与依赖基线

- 日期：2026-11-09；状态：Accepted（Integration + Core 联合静态审定；实现须经独立 QA）。
- 任务：CORE-001；前置 INT-001 / PROD-001。

## 背景与范围

PROD-001 已通过 mock 回归并按用户指令合入 main、推送。其 UI 状态图不是 Rust 作业协议实现。CORE-001 只建立五 crate workspace、CLI、C ABI 与模拟状态，不提前实现 CORE-002 媒体处理或三端 GUI。

`ms_engine`/`ms_buffer` 和八个现有函数签名不变；新增兼容错误值 -5..-9，明确原草案未决语义。无 SDK 最低支持或平台能力承诺变化。core 代码由 Core 维护；contracts/docs/根清单由 Integration 整合；QA 只在 tests/core_001 新增独立测试，不改实现或黄金预期。

## 决定

1. UI → 统一 C ABI → Rust orchestrator/domain；CLI 调同一 Engine。domain 持有强类型配置/请求/状态与输入校验；orchestrator 持有单一 mock 作业状态；media-ffmpeg 本轮仅返回未实现能力，不执行命令。unsafe 仅放 media-ffi，显式 SAFETY 说明；非 FFI crate forbid unsafe。
2. 同一 engine 由调用方串行调用，destroy 排他；不同 engine 独立。暂不增加 async runtime/线程/回调依赖。模拟作业靠 snapshot 推进；不得将这种行为套到后续真实作业调度，真实执行器需要单独设计和测试。
3. 仅 operation=mock 可提交，succeed/fail 结果不含媒体产物；真实 probe/trim/重编码/AI 的 available=false。任务容量和输入大小受限；终态保留有上限。cancel 确认与终态分离，destroy BUSY 不释放 handle。
4. 严格字段和 UTF-8/JSON/version 规则见契约；JSON buffer 失败时归零且不分配错误详情。不增加未审阅的 last_error 或 callback ABI。V0 mock 的重复键后值覆盖行为明确记录，真实路径/媒体协议冻结前再评估，不承诺拒绝重复键。
5. Rust 精确基线仍为 1.99.0；FFI profile 使用 unwind 并 catch_unwind，只转换可捕获的 panic。payload 避免在出口析构；受影响 handle 隔离，后续仅允许销毁；abort/OOM/无效指针不在该保证内。
6. 唯一 registry 直接依赖为精确 `serde_json = "=1.0.149"`，标准 std 特性，不用 derive、外部 async 或 FFmpeg crate。使用已有 crates.io 缓存离线解析并提交 Cargo.lock；完整传递依赖、checksums、来源/许可审核另记 依赖记录，Integration 审核后才接纳。没有将本机 GPL FFmpeg/模型加入产物。

## 选择理由与替代

手写 JSON 解析会增加不可信输入处理风险，采用既有解析库并锁定来源/许可；直接合并真实媒体路径会超出 CORE-001，也会绕过 QA-001 与 HOS-001 前置。先实现同步可控 mock 可为三端库加载 Spike 提供真实二进制接口；不代表媒体算法已经可用。

## 验证计划与失败案例（实现前定义）

先由 QA 在 tests/core_001 写独立 ctypes/C 链接契约测试，Core 在各 crate 写失败/状态测试，再实现接口。成功路径是 create → version/capabilities → submit mock → poll terminal → free → destroy。失败路径至少覆盖空指针/out、零长度、UTF-8/JSON/schema/未知字段、超限、unsupported operation、未知 ID、活动任务阻止销毁、重复取消与任务隔离。避免故意解引用悬空句柄或 double free 的 UB 测试。

实际执行 fmt、clippy、test、build、CLI、C/C++ 头/布局、真实 dylib 链接/ctypes 和重复创建释放；复审每处 unsafe/panic/所有权。阶段结果与缺口记录到 报告。平台 UI、Windows/鸿蒙、Intel Mac、真实 FFmpeg、签名/发布仍单独验收。

## 后果、迁移与回滚

V0 mock 子集有可审定的运行语义，原 trim 样例保留为后续草案且当前拒绝。任何签名/布局或所有权破坏变更必须换 major 与迁移文档。若 mock 无法通过本机 ABI 测试，不扩大平台接入；保留隔离分支，修复或回退 CORE-001，不回退已验收 PROD 文档。不因本机 dylib 成功宣称三端 G1 通过。

## 实现资料

panic 捕获范围与 payload 析构风险依据 [Rust catch_unwind 文档](https://doc.rust-lang.org/std/panic/fn.catch_unwind.html)；allocator/Box 所有权恢复依据 [Rust Box 文档](https://doc.rust-lang.org/std/boxed/struct.Box.html)。这些参考不替代锁定工具链上的实际 ABI/单元测试；执行证据以本轮 Rust 1.99.0 和本机产物为准。
