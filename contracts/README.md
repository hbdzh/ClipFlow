# Rust Core 公共接口契约（V0 / CORE-001 mock 基线）

CORE-004的显式转码/原子批队列/音轨选择见[CORE-004.md](CORE-004.md)，需core004_enabled且core003_enabled；本轮实现/验证状态另见报告，八函数ABI不变。

CORE-003的显式精确/新copy profile与独立期限兼容扩展见[CORE-003.md](CORE-003.md)，需core003_enabled明确开启；实现/验证结果以对应交付证据为准。

CORE-002 的显式媒体后端兼容扩展另见 [CORE-002.md](CORE-002.md)；下文保留默认 mock 行为和共用所有权约束。未设置 backend 的原调用不被静默升级为媒体处理。

2026-11-09：Integration/Core 联合审定。现有八个函数签名和 `ms_buffer` 布局保持兼容。以下表格及 Mock 小节描述默认配置；真实媒体需显式后端，见顶部扩展和独立交付证据。基础决策见 [ADR-0002](../docs/adr/0002-core-001-abi-baseline.md)，媒体决策见 [ADR-0004](../docs/adr/0004-core-002-media-pipeline.md)。

## 所有权、指针与线程

- opaque `ms_engine*` 由 Rust 创建，调用方持有到一次成功 destroy；create 错误时 out_engine 为 NULL。null engine 返回 INVALID_HANDLE；非空无效指针不保证可检测，禁止用悬空指针或重复销毁做正常错误测试。
- 输入 JSON/job_id 只借用到函数返回，须非 NULL、长度为正且内存有效可读；NULL+0、非 NULL+0、NULL+正长度均 INVALID_ARGUMENT。不依赖 NUL 终止；内嵌 NUL 是数据，JSON 中非法控制字节须报错。JSON 最长 65536 字节，job_id 最长 128 字节；上限优先于 null/零长度判断检查，超限统一 LIMIT_EXCEEDED，且始终在构造 slice 前拒绝。
- 所有 out 指针须有效可写、正确对齐，且不与输入、engine 或活跃输出别名。调用前使用全新存储或空 buffer；不得把尚未释放的旧输出传回。函数在验证其他参数前将有效 out 置 NULL/0；这不负责回收旧输出。
- 成功输出由 Rust 分配，调用方只能用 `ms_buffer_free` 原样释放一次；ptr/len 不能修改，不能用 C free。buffer 不借用 engine，可在 engine 销毁后释放。`{NULL,0}` 可释放，其他伪造/重复释放是调用方违规。
- 同一 engine 的全部调用由调用方串行化，destroy 与所有借用调用互斥；包括 version/snapshot 的调用也不能并发。不同 engine 可在不同线程独立调用，buffer 的单独释放可跨线程但不得并发使用同一 buffer。以后若扩展同一 engine 并发，须补契约和竞态测试。
- 长任务、轮询与退出放后台；UI 主线程仅接收强类型桥接结果。默认 mock 不启工作线程、子进程或媒体 I/O，不能据此推断真实取消/退出性能。

## 输入与消息规则

JSON 必须为 UTF-8 对象，含可表示为有符号/无符号 64 位整数的 `schema_version: 0`。缺失/非整数版本为 INVALID_ARGUMENT；有效整数非 0 为 UNSUPPORTED_SCHEMA；语法错误或非对象 JSON 为 INVALID_JSON；非法 UTF-8 为 INVALID_UTF8。字段名严格区分大小写，拒绝未知字段。V0 mock 采用 serde_json Value 的重复键后值覆盖行为，不能声称会拒绝重复键；真实媒体协议冻结前需重新评估此策略。

| 调用 | 允许输入 / 响应 |
|---|---|
| engine_create | `{"schema_version":0}`；可选 `max_jobs` 整数 1..256，默认 64 |
| get_version | schema_version、core_version、abi_version、stage、mock_only；无 JSON 输入 |
| get_capabilities | schema_version、core_version、platform、features、media_backend；probe/trim_stream_copy/accurate_reencode/ai_separation 均 unavailable，mock_job available |
| submit_job | `{"schema_version":0,"operation":"mock","outcome":"succeed"}`；outcome 可省略，或为 fail；任何真实媒体/其他操作返回 UNSUPPORTED_OPERATION，不访问路径 |
| get_job_snapshot / cancel_job | 输入 plain UTF-8 job_id（不是 JSON）；未知 job 返回 NOT_FOUND。ID 只属于创建它的 engine，不是跨引擎全局句柄 |

成功 JSON 均含 schema_version=0。无错误 JSON buffer，也无 last_error API；函数错误通过固定错误码报告，不能从旧 buffer 读取诊断。mock 失败任务的 snapshot 可含固定错误对象，不能回显用户输入/路径。

`examples/mock-config.json`、`mock-request.json` 是可用请求；`mock-version.json`、`mock-snapshot.json` 是语义示例，实际平台/版本以实现为准。`trim-request.json` 是 CORE-002 流复制请求，默认 mock engine 必须拒绝；显式 backend engine 按扩展校验执行；旧产品原型的 UI envelope 不自动成为本协议。

## Mock 作业状态及关闭

- submit 返回 job_id、operation=mock、state=queued。首个 snapshot 推进为 running，再次推进到 succeeded 或 failed；这是可复现的模拟轮询行为，不是生产调度器。重复读取终态保持终态。
- cancel queued/running 后进入 cancelling；首个 snapshot 仍返回 cancelling，下一次为 cancelled。重复取消和终态取消幂等返回 OK；成功/失败与取消的含义不混淆。
- succeeded 的结果明确 `mock_only:true,file_created:false`；mock 取消也只代表模拟终态，没有执行文件清理或媒体处理。progress 可为 null，不伪造真实处理进度。
- 任一任务非终态时 destroy 返回 BUSY，句柄仍有效。调用方取消并轮询全部任务至终态，再重试 destroy。
- 终态保留到 engine 销毁，`max_jobs` 计所有保留任务；达到上限后 submit 返回 LIMIT_EXCEEDED。需要更多任务时关闭已完成的 engine 并新建，不能无限累积数据。没有后台进程、真实输出或作业跨重启恢复。

## 错误与 panic

| 数值 | 名称 | 含义 |
|---|---|---|
| 0 | MS_OK | 调用成功；cancel OK 不等于已经到 cancelled |
| -1 | MS_E_INVALID_ARGUMENT | 空输入/out、配置字段、范围/类型等不合法 |
| -2 | MS_E_INVALID_HANDLE | null handle；不承诺检测任意悬空/伪造指针 |
| -3 | MS_E_INVALID_UTF8 | 输入 UTF-8 不合法 |
| -4 | MS_E_UNSUPPORTED_SCHEMA | 有效整数版本不是 0 |
| -5 | MS_E_INVALID_JSON | JSON 语法或对象根类型错误 |
| -6 | MS_E_NOT_FOUND | 当前 engine 没有该任务 |
| -7 | MS_E_BUSY | 非终态任务阻止 destroy |
| -8 | MS_E_UNSUPPORTED_OPERATION | 未实现的操作 |
| -9 | MS_E_LIMIT_EXCEEDED | 输入长度/保留任务数超限 |
| -100 | MS_E_INTERNAL | 内部错误或捕获的 unwinding panic |

FFI 出口捕获 unwinding panic 并转换错误；panic payload 不在出口析构，防止二次 panic。发生内部 panic 的 engine 隔离，只允许 destroy（mock 无外部资源；显式媒体 engine 还需取消/join worker 后销毁）；其他调用返回 INTERNAL。不能保证捕获 abort、OOM、外来异常或调用方指针 UB。不得把 catch_unwind 当作无效内存访问的安全网。

## 兼容策略与平台桥接

V0 保留 CORE-001 mock 子集，并通过独立审定的 CORE-002 可选配置扩展真实媒体；ABI major 仍为 0。真实 probe/trim 使用扩展消息与测试，不能把 mock 输出当媒体实现。同一 major 不改函数签名、结构体布局、分配/释放方或已有字段语义；破坏变化必须升级 major 并记录迁移。生产前应升为 v1。

C# 用 SafeHandle/明确 UTF-8 长度与 IDisposable；Swift 用受控对象和 defer/free；ArkTS 经薄 Native 桥接统一生命周期和后台调用。三个平台都先依本契约做 mock 调用，平台真实加载、布局、权限与释放证据分别验收。
