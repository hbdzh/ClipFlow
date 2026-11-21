# ADR-0021：macOS 沙盒固定 AI 代码与受管模型数据

- 日期：2026-11-14
- 状态：Accepted；Integration 已批准本机 macOS arm64 平台方向与冻结实现的静态边界。最终 GUI、布局及性能验收以父任务同包报告为准，不由此状态继承通过。
- 关联：QA-002 第五项闭环、ADR-0015、ADR-0016；基线失败及诊断界限见 平台诊断
- 范围：本机 macOS arm64 受控开发 GUI 的组件配置、迁移与缓存；不是正式分发或跨平台接收

## 背景与平台依据

旧 GUI 允许分别选择外部 worker、图目录、ORT 动态库和模型，并持有父进程 security-scoped URL。基线中四项均从 Native-v5 包逐字节复制，真实作业仍失败为 `DEPENDENCY_MISSING`。追加的 44.1 kHz 双声道 float32 静音输入排除了本例的格式规范化 FFmpeg 分支；错误仍丢失失败程序及原始 errno，不能据此确定首先拒绝执行的系统机制。

Apple 当前 [Accessing files from the macOS App Sandbox](https://developer.apple.com/documentation/security/accessing-files-from-the-macos-app-sandbox) 明确限定用户文件授权下的可执行位置。原文节录：

> Your app can’t run programs in locations outside its app bundle, sandbox container, or app group containers

因此，NSOpenPanel 对 `/private/tmp/.../media-ai-worker` 的读写授权不能构成执行授权；`com.apple.security.files.user-selected.executable` 的用途是写出可执行文件，不是给所选外部程序增加执行权限。

Apple [Enabling App Sandbox — Enabling App Sandbox Inheritance](https://developer.apple.com/library/archive/documentation/Miscellaneous/Reference/EntitlementKeyReference/Chapters/EnablingAppSandbox.html) 对子进程继承的原文节录为 “inherit only the static rights” 以及 “not any rights added to your sandbox after launch”。父进程在选择面板后持有访问 scope，不足以保证只收到路径字符串的 helper 可以读取外部组件；可使用的替代是传递数据或 bookmark。当前文件访问文档也给出跨进程 bookmark 解析方式。

[Embedding a command-line tool in a sandboxed app](https://developer.apple.com/documentation/xcode/embedding-a-helper-tool-in-a-sandboxed-app) 支持把 helper 放入应用包，并要求继承型 helper 使用恰当的 sandbox/inherit 签名。本决定采用已有包内 helper，不新增 helper entitlement。

## 候选方案

| 方案 | 取舍 |
| --- | --- |
| 继续让 GUI 选择任意外部 worker/ORT | 用户文件授权不保证外部执行；代码来源及动态库加载还需另审，不能修复当前闭环。 |
| 包内 helper，跨进程传递外部资源 bookmark | 属于可继续研究的平台方式，但需增加 helper 的 bookmark 解析和资源生命周期协议，超出本次最小修复。 |
| 固定包内 worker/ORT，显式导入固定模型数据到私有容器 | 采用。复用现有代码身份和原生调用参数，只增加平台文件授权、纯数据缓存及迁移。 |

不通过移除 quarantine、修改系统保护、增加执行 entitlement 或把外部代码复制到容器来维持旧流程。

## 决定与责任

| 行为 | 唯一责任方 |
| --- | --- |
| 选择面板、迁移确认、配置来源和大小展示、书签 scope 生命周期 | macOS 原生客户端 |
| 在后台核验并缓存八个固定 ONNX 图及原模型，管理缓存归属和配置记录 | macOS 纯数据 actor store |
| 来源/模型的媒体接收规则、输入规范化、作业/取消/超时、空间预算、双 stem 验证与提交 | 现有 Rust Core |
| 固定图字节及模型摘要再校验、CPU 推理、原生输出 | 现有包内 native worker 和 ORT |
| helper 签名、包内位置、容器路径和实际文件可访问性 | macOS 构建与平台适配；独立 QA 实测 |

1. GUI 的 worker 固定为当前应用包内 `Contents/MacOS/media-ai-worker`，ORT 固定为包内 `Contents/Frameworks/libonnxruntime.dylib`。不再把外部 worker/runtime 书签直接送入 Core，也不暗中执行用户此前选定的代码。缺少包内组件时明确失败，不用外部程序替补。
2. 用户通过原生面板明确选择外部图目录及模型，界面说明只导入本包支持的固定模型数据，显示包内程序/运行时与导入数据的不同来源。当前包内图及模型只作为固定文件集合、大小和 SHA-256 的参照，不在用户取消或外部数据不匹配时静默改用包内数据。
3. store 只接受八个固定图及原模型的普通文件，逐项以当前包内参照核对大小和完整 SHA-256；边复制边校验，并复核将发布的缓存实际字节。外部目录内的其他文件不是代码加载或任意插件入口。收据是本地缓存管理数据，不是用户提供的信任根。
4. 数据写入应用私有容器中的独立暂存目录，核验完整后再发布缓存和选择记录。写入失败、来源变动、哈希不符或归属/清理无法确认时不得把部分数据标记为可用；原配置保留，清理只作用于可确认由 store 创建并拥有的项目。
5. store 的读写、哈希和缓存维护在后台 actor 内完成；UI 保持响应，只接收明确的成功/失败结果。重启读取缓存时重新按当前包内参照校验，不因路径存在或旧收据自报摘要而直接接受。
6. Core 仍收到原有 `AIBackendConfiguration` 的绝对路径：包内 worker/ORT，加已核验的容器内 graphs/model。UI 不计算重采样参数、帧数、推理结果、输出原子性或 Core 作业状态。

## 老四组件配置的显式迁移

检测到旧 worker/graphs/runtime/model 四书签时，显示旧配置需要迁移及新方案的具体含义。没有用户确认，不自动建立受管配置、不把包内 worker/ORT 伪装成用户原先选定的组件，也不运行旧外部程序。

用户确认后，可在授权有效的前提下以旧 graphs/model 作为明确确认的数据来源；授权失效则重新选择数据。worker/runtime 不导入、不复制，旧外部文件不修改。迁移只有在数据完整校验和受管选择记录发布成功后才完成，随后按确认范围退役旧书签。取消面板、取消迁移或校验失败时保留原记录并保持待迁移状态，不自动下载或替换数据。

已迁移配置重启后的读取属于恢复已确认的选择，不是自动迁移旧配置。换包后参考版本不符时要求重新配置，不能静默换模型。

## 接口、依赖、平台与回滚

- 八函数 C ABI、`schema_version=0`、公共请求/响应、Core 错误语义及 `contracts/AI-001.md` 的原生开发配置不变；此决定收窄 macOS GUI 可配置的代码来源，不移除已有 Core/CLI 的受信开发接口。
- 不修改 Core 媒体决定、worker 成功算法、模型、图、ORT 版本、依赖锁文件、SDK 最低版本、许可证或 entitlement。不复制 worker/dylib 代码，不移除任何隔离属性。
- 保持 ADR-0016 的本机开发和发行 NO_GO 边界。包内签名验证、开发 GUI 成功与正式签名、公证、渠道审查分别判断。
- macOS arm64 平台设计为静态复核支持，修复包 GUI 运行仍待测；Intel、Windows 和 HarmonyOS 为 NOT_TESTED，不继承本决定的运行保证。
- 如回滚，停用受管配置入口并保留用户原数据及可确认归属的缓存；不得自动恢复旧外部执行流程或因未知目录内容而递归删除。需要恢复旧受信开发路径时，另行明确说明其限制。

## 验证要求与 Integration 意见

首个成功用例：用户从包外明确选择与当前包基准一致的数据，完成容器导入，退出并重启后恢复同一选择，用全零 float32 音频完成真实 Core 作业及双 stem 输出。证据要同时记录包内 worker/ORT、受管 graphs/model 的来源和摘要；不得只验证独立 helper，或把机器码驱动称为 GUI。

失败用例至少包括：模型或任一固定图大小/摘要不符时拒绝，原选择仍有效；老四书签迁移取消或拒绝授权时不提交任务、不自动替换为包内数据。缺少包内代码、缓存损坏、写入/清理失败沿现有平台失败规则报告，不通过篡改公开错误或放松校验使验收通过。

Integration 平台意见：**支持该方案作为当前最小修正**。它分别解决外部代码执行位置与子进程数据授权边界，保持 Core 单一语义与既有 ABI；无需修改系统保护或依赖。此意见只批准平台方向，不替代 store/迁移实现复审、最终包身份核对或实际静音 GUI 复验。本轮撰写者未运行应用、worker、媒体播放或 VoiceOver。

Integration 实现审定（2026-11-14）：**Accepted，限定静态实现批准**。`AIComponentStore`、异步配置/文件面板、退出所有权、包内代码定位及诊断增量已完成分工独立复审；源与候选包绑定见 Integration 复审及其机器记录。既有公共 ABI、Core 语义与固定第三方组件不变。所读 store/观察器控制是静音文件验证，不能替代最终沙盒 GUI、大模型链路、窗口缩放或 ≤500 ms 性能接收；本机开发及发行 NO_GO 边界保持。
