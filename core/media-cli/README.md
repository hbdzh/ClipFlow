# Core 验证 CLI

在仓库根目录运行：

```sh
cargo run --manifest-path core/Cargo.toml --locked -p media-cli -- version
cargo run --manifest-path core/Cargo.toml --locked -p media-cli -- capabilities
cargo run --manifest-path core/Cargo.toml --locked -p media-cli -- mock succeed
cargo run --manifest-path core/Cargo.toml --locked -p media-cli -- capabilities --media
cargo run --manifest-path core/Cargo.toml --locked -p media-cli -- probe /absolute/source.mp4
cargo run --manifest-path core/Cargo.toml --locked -p media-cli -- trim /absolute/source.mp4 /absolute/output.mp4 1100000 2600000
cargo run --manifest-path core/Cargo.toml --locked -p media-cli -- capabilities --core003
cargo run --manifest-path core/Cargo.toml --locked -p media-cli -- trim-copy-v1 /absolute/source.mp4 /absolute/copy.mp4 1100000 2600000
cargo run --manifest-path core/Cargo.toml --locked -p media-cli -- trim-accurate /absolute/source.mp4 /absolute/accurate.mp4 1100000 2600000
```

`version`、默认 `capabilities` 和 `mock` 保持 CORE-001 的 mock 语义。`probe`、`trim` 和 `capabilities --media` 显式启用CORE-002兼容真实后端；它们从 `PATH` 查找 `ffmpeg` 和 `ffprobe`，也可设置以下环境变量。工具必须由调用方安装，本仓库不携带 FFmpeg 二进制。

| 环境变量 | 默认值或约束 |
| --- | --- |
| `MEDIASTUDIO_FFMPEG` | FFmpeg 可执行文件的绝对路径；未设置时查找 `PATH` |
| `MEDIASTUDIO_FFPROBE` | FFprobe 可执行文件的绝对路径；未设置时查找 `PATH` |
| `MEDIASTUDIO_TIMEOUT_MS` | `30000`；允许 `1..300000`，用于probe、流复制及能力检测 |
| `MEDIASTUDIO_ACCURATE_TIMEOUT_MS` | `600000`；允许 `1..7200000`；`trim-accurate`、`capabilities --core003`及`transcode` / `capabilities --core004`构建完整配置时读取；转码执行使用其独立的`MEDIASTUDIO_TRANSCODE_TIMEOUT_MS` |
| `MEDIASTUDIO_MAX_OUTPUT_BYTES` | `8388608`；允许 `1024..67108864` |

输入和输出路径可使用相对路径；CLI 依据当前目录转为绝对本地文件 URI，并正确编码 UTF-8、空格和特殊字符。参数通过参数数组交给后端，不经过 shell。路径必须为 UTF-8。目的文件不得已存在，后端不会覆盖源文件或已有目的文件。

`START_US`、`END_US` 是相对于规范化媒体时间线的整数微秒，须满足 `0 <= START_US < END_US`。旧`trim`只请求不带profile的`stream_copy`、`all_supported`，不允许回退重编码；其旧颜色限制不变。实际保留范围受完整关键帧 GOP 限制，必须查看结果中的 `requested_range_us` 和 `actual_range_us`，不能把它当作任意帧精确剪辑。

`probe`、`trim`、`trim-copy-v1` 和 `trim-accurate` 可在末尾追加 `--cancel-after-ms N`，其中 `0 <= N <= 300000`。这是验证取消的客户端选项；若导出已提交，迟到的取消仍可能得到 `succeeded`。

## CORE-003显式命令

`trim-copy-v1`固定请求`stream_copy` + `mp4_h264_copy_v1`；`trim-accurate`固定请求`accurate_reencode` + `mp4_h264_standard_v1`。两者自动在宿主配置启用`core003_enabled:true`，不改变公共操作名`trim`，仍只保留全部支持音轨，不接受codec/CRF/filter或任意FFmpeg参数。新profile范围与输出验证见[CORE-003契约](../../contracts/CORE-003.md)；能力不足或输入不支持仍明确失败，不静默换模式。

`capabilities --core003`显式检查新profile与软件编码能力；默认capabilities为mock，旧`capabilities --media`不启用新能力，不会因为FFmpeg存在就宣称准确可用。旧命令与`trim-copy-v1`不读取新准确期限环境变量；无效的`MEDIASTUDIO_ACCURATE_TIMEOUT_MS`不会改变它们的行为。

精确命令明确有损重编码，不能称“精确无损”。它保留请求范围与实际选帧范围，源帧起点按有理时间基量化，音频/视频共同归零；准确deadline从处理开始覆盖分析、编码和验证，不含排队，CLI的防御等待使用该准确值加既有清理余量。现有每engine一个worker和关闭/取消所有权不变。

CLI 向 stdout 输出一份 JSON，其中 `snapshots` 保存观察到的不同快照，`final` 是终态。真实任务只有 `succeeded` 返回退出码 0，`failed`、`cancelled` 和无效请求均返回非 0。失败详情位于 `final.error.code` 和 `final.error.message`；真实失败另带顶层数值 `error.code = -100`，作为旧 CLI 调用方兼容标记。默认 mock 的取消退出码仍为 0。

Core 中每个引擎最多运行一个真实媒体 worker；读取快照不会推动任务执行。后端超时从处理任务开始计算。CLI 退出时会请求取消并等待 worker 清理完成。此 CLI 用于开发验证，不代表 Windows、HarmonyOS 或完整播放观感已经验收。

## CORE-004转码与JSONL会话

显式新命令（仅已接纳的本机软件路径）：

```sh
core/target/debug/media-cli capabilities --core004
core/target/debug/media-cli transcode /absolute/source.mp4 /absolute/new.mp4 mp4_h264_standard_v1 all
core/target/debug/media-cli transcode /absolute/source.mp4 /absolute/small.mp4 mp4_h264_720p_v1 none
core/target/debug/media-cli transcode /absolute/source.mp4 /absolute/one-track.mp4 mp4_h264_standard_v1 2
core/target/debug/media-cli core004-session /absolute/core004-config.json
```

`transcode`参数顺序为SOURCE DEST PROFILE AUDIO，AUDIO仅`all`、`none`或全局源stream_index非负整数；不接受任意codec/质量/filter参数。P01/P02、尺寸/音轨/预算与输出判据见[CORE-004契约](../../contracts/CORE-004.md)。它自动显式开启core003/core004，新增`MEDIASTUDIO_TRANSCODE_TIMEOUT_MS`默认600000、范围1..7200000；其他环境配置沿用上表。能力与执行来自同Core，不在CLI编排编码。预检失败输出`preflight.accepted:false`且非零退出，没有伪job_id；真实终态仍仅succeeded退出0。

会话CONFIG_FILE为最多65536字节的真实Engine配置，必须显式core004_enabled/core003_enabled；不从命令环境偷偷改写该文件的配置。标准输入为JSONL，每行最多65536字节，回复一行并立即flush：

```json
{"command":"submit","request":{"schema_version":0,"operation":"queue_snapshot"}}
{"command":"snapshot","job_id":"media-1"}
{"command":"cancel","job_id":"media-1"}
{"command":"wait","job_id":"media-1","timeout_ms":600000}
{"command":"close"}
```

submit直接转发媒体或控制JSON，batch/reorder/retry/history均由Engine负责；其回复为`{schema_version:0,command,ok:true,response:Core原响应}`。批内原子接收与submission_id幂等须用batch_submit，普通single不承诺幂等。wait仅轮询已有job至真实终态，timeout_ms范围1..7200000、缺省7200000；到期返回BUSY不抛弃engine。错误回复`{schema_version:0,ok:false,error:{code,message}}`，保持engine，后续可以修正/取消/轮询。未知字段严格拒绝；UTF8错误为-3，语法JSON错误-5。超长行报LIMIT_EXCEEDED后终止会话，绝不将该行尾部重解释为新命令。

close只在没有非终态且没有未确认清理时消费Engine；BUSY(-7)保留同engine，调用者须先cancel各项并wait，再重试close。成功close回复closed:true并退出0。EOF不是成功显式close：退出1且Rust Drop仍真实取消并join其worker，不造“已关闭”结果；应在发送完命令后显式close。会话只是一条后台开发通道，不扩张同engine C ABI串行规则，预检中不可并发cancel/destroy。

新模式32非终态/100终态详情与摘要独立于旧max_jobs。进程内所有真实trim/transcode（含旧engine）共一个重任务permit；等待者保持queued可排序/取消。未知progress为null。清理不确认时进程级隔离，不能通过新Engine、清历史或释放失败job来绕过，需保留现场并重启进程；这不是跨进程全系统锁。Core只提供有界内存摘要，本地历史文件与授权引用仍由原生宿主按MAC-002/MAC-003管理。
