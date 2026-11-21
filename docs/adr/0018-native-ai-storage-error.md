# ADR-0018：原生 AI worker 的真实空间不足分类

- 日期：2026-11-14
- 状态：2026-11-14 Integration 独立静态审核通过；真实 ENOSPC 与最终包动态证据另记
- 范围：原生 worker 内部失败协议；不变更公开 C ABI、schema_version=0 或成功结果

## 问题

AC17 独立复验在专用 256MiB HFS+ 卷于预检后真实写至 errno 28 / 可用 0。原生 worker 确实失败，Core 不发布半组、清理并允许同会话恢复，但公开分类只有 PROCESS_FAILED，不能准确提示磁盘不足。原始证据为 `docs/qa-002/evidence/ai-20261114/atomic-storage-red.json`。

## 决策

原生 worker 只在错误链包含 `std::io::Error::raw_os_error()==Some(28)` 时输出固定单行 `{"schema_version":1,"error":"OUT_OF_DISK"}` 并退出 **73**；其他失败仍输出固定 PROCESS_FAILED / 退出 1。不按错误字符串、任意工具 exit 28、文件存在或剩余空间估算猜测。

runner 增加显式的 `run_group_with_status`，在原有取消、期限、管道 EOF、child/进程组确认回收全部完成后，才交还 exit status 和有界诊断。原 `run_group` 保持非零即 ProcessFailed 的行为，FFmpeg、shasum、df 与 Python worker 的调用不变。

只有 native worker 调用点同时验证退出 73 与完整固定错误 JSON 才映射公开 OUT_OF_DISK。状态/JSON 之一不匹配、异常信号或其他失败仍 PROCESS_FAILED。成功响应、算法、模型、参数、资源预留和双 stem 提交不变；清理或回收失败仍优先 CLEANUP_FAILED，不能被磁盘分类掩盖。

## 兼容及回滚

旧 worker 配新 Core 的该错误仍是 PROCESS_FAILED；新 worker 配旧 Core 同样安全失败为 PROCESS_FAILED，不会假报成功。同步更新随包 worker 和 Core 后获得准确分类。内部 worker 协议不是自由外部工具 exit code 的公共解释器。若回滚，成对回滚此 native 分类，不回滚清理/原子提交保护。

Integration 静态复核确认：错误分类仅检查实际 IO error 链，不按诊断文本猜测；runner 只在进程组、管道及直接子进程回收确认后交还退出状态，旧接口仍拒绝所有非零退出；Core 仅在 native 调用点同时核对专用状态与固定 JSON。取消、超时、诊断上限和 CLEANUP_FAILED 原优先级保持，未修改成功数值算法、模型、双输出提交或公开 ABI。该批准只覆盖本机 macOS 受控开发实现，不推定其他平台 errno 映射或动态验收通过。

## 验证

单测要求：实际 errno28 可分类、EACCES 和文本“errno28”不可分类；runner 原接口继续拒绝普通非零退出；native 映射必须同时具备73和固定JSON。真实小卷复验必须得到 OUT_OF_DISK、无目标/半组、原始源不变、进程和 staging 清理并同 engine 恢复。其他7组原子性/依赖边界回归及CPU信号链证据另行记录，不能从静态协议推导动态通过。
