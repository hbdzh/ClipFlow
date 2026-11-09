# V0 扩展：CORE-002 真实媒体子集

2026-11-09，Integration + Core 联合审定；配套 [ADR-0004](../docs/adr/0004-core-002-media-pipeline.md)。八个 ABI 函数/缓冲布局/线程和释放义务保持 [原契约](README.md)。不启用 backend 时，CORE-001 默认 mock 行为不变。

## 显式宿主配置

```json
{
  "schema_version": 0,
  "max_jobs": 64,
  "media_backend": {
    "ffmpeg": "/opt/homebrew/bin/ffmpeg",
    "ffprobe": "/opt/homebrew/bin/ffprobe",
    "timeout_ms": 30000,
    "max_output_bytes": 8388608
  }
}
```

工具必须是非空、无 NUL 的绝对路径；由宿主选择可信安装，不接受媒体请求替换工具或附加命令参数。`timeout_ms` 默认 30000、范围 1..300000；从 worker 开始处理计算，覆盖版本/探测/规划/验证等阶段，不包括排队等待。stdout/stderr 每个 reader 分别最多保留 `max_output_bytes`，默认 8 MiB、范围 1024..64 MiB，超限终止处理而不是截断 JSON 后装作成功。

config 嵌套对象和请求字段严格，未知字段/类型错误为同步 -1。仍使用 V0 重复键后值覆盖规则。JSON 64 KiB 输入上限、UTF-8、schema 处理及输出归零规则不变。现有同步错误码不扩展。

宿主配置样例另存 [media-config.json](examples/media-config.json)，路径仅为本机示例，不能当作其他工作站默认值。

## 请求

probe（[probe-request.json](examples/probe-request.json)）：

```json
{"schema_version":0,"operation":"probe","source":{"uri":"file:///EXAMPLE/input.mp4"}}
```

trim：沿用 [trim-request.json](examples/trim-request.json)。`range.start_us/end_us` 为 i64 整数微秒，start>=0、end>start；相对首视频原始 PTS，而不是绝对 PTS。越实际媒体时长由 worker 拒绝。只接受 `strategy: stream_copy`、`stream_selection: all_supported`、`allow_reencode_fallback: false`，不静默换重编码或删轨。

source/destination 只含 uri，接受 `file:///` 本地绝对 URI，percent 解码必须仍为合法 UTF-8，拒绝 host、其他协议、未编码 query/fragment、控制字符和 NUL。FFmpeg/ffprobe 固定 file 协议及 mov demuxer 白名单，禁用 MOV 外部数据引用和绝对外部路径。真实文件还须后端检查；URI 通过并不等于文件可读/格式受支持。输出仅新 `.mp4` 文件，已有文件/目录/链接均拒绝，不能把源文件当输出或临时文件。

默认 mock engine 对媒体请求仍返回 -8；显式 backend engine 才按上述严格字段验证并排队。输入校验失败不得创建 ghost job。

## 异步与取消

提交成功返回 schema 0/job_id/operation/state=queued。真实任务由 engine 内单个有界 worker 异步处理，轮询只观察，progress 可为 null。任务终态和结果保留直到 destroy，仍受 max_jobs（1..256）限制。

- queued → running → succeeded/failed；处理与验证都属于 running，不用假百分比表示实际进度。
- queued 取消若 worker 未领取，可直接 cancelled，没有已启动资源。
- active 取消是请求，通常 running → cancelling → cancelled；只有进程退出和临时输出清理完成才可 cancelled。重复取消幂等。
- 提交完整产物成功之后到达的取消可以保持 succeeded。不得虚报 cancelled 同时暗留已成功提交文件。
- 清理不能确认时 failed/CLEANUP_FAILED，不能假装取消成功。任何非终态阻止普通 destroy，BUSY 不消费 handle。
- Engine Drop 也要取消/停止/join，包括 panic quarantine 销毁，必须后台调用；本轮不承诺任意外部工具 fork 树的终止或 UI 线程安全等待。

## 成功结果

probe result 至少有 `schema_version:0, mock_only:false, file_created:false, media_info`。media_info 包含 container、video_origin_us、规范化 duration_us、白名单 streams 元信息和 raw_format_start_time；不回显原始完整用户路径或任意 tags。probe 可以报告非 H.264 单视频，不能据此认为可剪辑。trim 只接纳单 H.264 + AAC/无音轨；旋转/Display Matrix、视频 side data 或明确颜色标签尚无完整保留验收，返回 UNSUPPORTED_FORMAT。probe 的 trim_display_metadata_supported 公开此限制。capabilities 分开列出 probe/trim 范围，最多 16 流、100000 视频帧；本轮 runtime 接纳主版本 7，实际只实测 7.1.1，不代表其余 7.x 已逐个通过。

trim result 至少有 `schema_version:0, mock_only:false, file_created:true`、`requested_range_us`、`actual_range_us`、`actual_source_range_us`、`output_media_info`、`validation`、`stream_copy_plan`。整数微秒值与原始 time_base/PTS 证据分开，舍入策略明确。

`alignment: enclose_keyframes` 将请求包围在前/后关键帧（或 EOF）内，实际范围可以扩展；必须返回实际内容范围/偏差。以原始关键帧 PTS seek、实际 packet DTS 截止候选及解码内容验证，禁止硬编码 QA-001 的 1.45 秒。无法证明连续、完整且可解码时失败，不输出“精确无损”承诺。

成功前须验证流/codec 和全部轨道映射、实际解码、DTS、源/输出压缩 packet 与 decoded frame 内容关系、实际首尾。有限音视频终点差只是时序检查，不是听感/口型同步证明。大文件/异常时基超出有界分析能力可拒绝，不能把抽样称全量验证。每个 result 最多 128 KiB，提交目标之前保证预算，不在已发布文件后丢弃成功结果。

## Worker 错误

语法正确的请求可能同步排队成功后异步失败，snapshot.error 使用固定 code/message；不回显完整用户路径、命令或原始 stderr。未启动资源的 queued cancellation 可 error=null。

| code | 明确语义 |
|---|---|
| INVALID_INPUT | 源不存在/空文件、越实际时长等不合法媒体输入 |
| INVALID_MEDIA | 损坏、非媒体或无法解析的探测数据 |
| DEPENDENCY_MISSING | 配置的工具不可用 |
| PERMISSION_DENIED | 工具不可执行、目录/目标写入权限等 |
| PROCESS_FAILED | 子进程非零、管道/状态读取失败（不覆盖更具体取消/超时） |
| RESOURCE_LIMIT | 进程输出、分析或摘要超过公开上限 |
| TIMEOUT | worker 总处理期限到达，子进程已终止/回收 |
| CANCELLED | 活动工作取消且清理已确认 |
| OUTPUT_EXISTS | 目标预先存在或提交期间出现，不覆盖 |
| UNSUPPORTED_FORMAT | 首版未支持的 codec/布局/封装 |
| UNSUPPORTED_BACKEND | 工具主版本非 7 或非已接纳的 macOS 文件后端 |
| SEEK_NOT_EXACT | 无法证明受限流复制计划的完整连续边界 |
| OUTPUT_VALIDATION_FAILED | 产物结构/解码/内容未通过验证 |
| SOURCE_CHANGED | 处理期间源身份/长度/mtime 改变 |
| CLEANUP_FAILED | 无法确认进程/临时文件清理完成 |
| INTERNAL | 内部失败或捕获的 worker panic |

## CLI 与兼容性

`media-cli probe PATH`、`media-cli trim SOURCE DEST START_US END_US [--cancel-after-ms N]` 使用同一真实 Engine；可用 `capabilities --media` 显式检查工具。普通 version/capabilities/mock 保持默认 mock 兼容。CLI 用 PATH 或明确的 MEDIASTUDIO_FFMPEG/FFPROBE 绝对路径构造 host config；环境 timeout/output 上限也经过同一 domain 校验。真实命令错误/失败/取消非零退出，具体 worker 诊断在 final.error；顶层数值 error.code=-100 只是 CLI 兼容失败标记，不是新增 ABI 码。默认 mock cancel 仍退出 0。不能只读“提交成功”。

本机通过不代表 Windows/鸿蒙 helper 或权限模型通过；完整平台与媒体验收见交付记录。默认 MAC-001 mock 仍可用，真实 UI 接入属于 UI-001。
