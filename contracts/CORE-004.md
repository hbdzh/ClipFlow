# CORE-004：显式转码、有界队列与音轨策略（V0兼容）

2026-11-10，Integration/Core联合审定。本契约是新能力的实现目标，实际状态见交付报告，不能把本文当作实现证据。基线MAC-002提交a2dd961，经85ee0ad合入main并推送。依据PROD-002与[ADR-0011](../docs/adr/0011-core-004-transcode-queue.md)。

## 兼容与调用边界

八个C ABI、原buffer/handle所有权、schema_version=0、固定数值错误、同engine调用者串行化规则保持。控制消息通过`ms_submit_job`的JSON operation分派，明确返回控制响应而非假作业；新控制响应最多1MiB、每份接收摘要最多4096字节（超限在建立jobs前拒绝），既有媒体成功result仍128KiB上限；只有媒体作业才有真实job_id。Rust内部BackendConfig增加字段/请求枚举增加variant，直接Rust源码调用需更新构造及exhaustive match；它不是新增稳定Rust SDK承诺。旧Core不理解新字段时应报扩展不支持，不由客户端删字段/换策略重试。新Core的旧配置/mock/trim/准确P01行为不变。

`media_backend`新增可选严格布尔`core004_enabled`（缺省false），true要求`core003_enabled:true`，否则配置同步INVALID_ARGUMENT。可选`transcode_timeout_ms`为1..7200000整数，缺省600000；仅新转码及其预检使用，不含排队，所有处理阶段共享操作期限。Prepared保存自身预检已耗的单调Duration，执行只获得总预算减已耗的剩余Duration，不按阶段重置，也不按毫秒截断丢失精度；等待队列/其它项预检不消耗该项处理预算。新模式probe/流复制预检及执行使用timeout_ms，准确trim使用accurate_reencode_timeout_ms，transcode使用transcode_timeout_ms，不能一律套转码期限。旧timeout_ms/accurate_reencode_timeout_ms含义不改。未启用CORE-004的请求不可触发新队列/预检或偷偷更换模式。

新模式每个队列engine固定最多32非终态及100终态记录；旧max_jobs的“所有保留作业计数”只用于旧模式，不将其当新活跃容量。新模式拒绝mock操作，probe/trim/transcode单提交也进入统一容量与预检路径；旧模式mock仍保持。32是单队列engine边界，不是多个engine相加的进程全局容量；PROD-002的宿主总量须由MAC-003共享一个队列engine或另行协调至32。跨engine测试可超过32用来验证单重permit，不能据此声明产品全局容量通过；原生宿主总量当前NOT_RUN。单提交不承诺去重，新客户端须使用batch_submit（含单项批）实现用户提交身份。

## 转码请求

```json
{"schema_version":0,"operation":"transcode",
 "source":{"uri":"file:///EXAMPLE/source.mp4"},
 "destination":{"uri":"file:///EXAMPLE/new-output.mp4"},
 "profile":"mp4_h264_720p_v1",
 "audio_selection":{"mode":"one","stream_index":2},
 "range":{"start_us":400000,"end_us":1700000}}
```

- source/destination为本地文件URI，沿用已有路径验证；profile必填，拒绝任意编码器/filter/命令参数。转码本身显式有损，不提供自动无损回退。
- range可省略，表示受支持整片；提供则start/end均为非负整数微秒且start<end，半开区间与源首视频内容PTS原点规则沿用CORE-003。运行前核实实际范围，不以UI浮点默认值替代。
- audio_selection可省略，默认`{"mode":"all_supported"}`。`one`必须有全局源stream_index（非音轨ordinal），且指向已验证AAC音轨；`none`不造静音轨；all_supported/none不得带stream_index，拒绝未知字段/负数/不存在索引。全源布局有效后，仅对实际保留音轨检查区间交集：未选轨无交集不能误拒none/one；实际保留轨无支持交集仍UNSUPPORTED_FORMAT，不静默删去。必须先验证整个源布局，不能借none/one绕过字幕、旋转、HDR、多音轨/多声道拒绝规则。旧trim的all_supported语义不变；新队列中的准确Trim仅调用准确选帧预检，不能先套流复制关键帧planner误拒合法短区间。
- profile=`mp4_h264_standard_v1`为P01：支持尺寸/有理CFR保持，libx264 CRF23/medium/yuv420p，AAC stereo192k/mono96k；profile=`mp4_h264_720p_v1`为P02：CRF25/medium，stereo128k/mono64k。采样率、所选轨语言/声道和共同源时间关系保留，不上混，不把流索引当语言猜测。
- 源支持范围沿用CORE-003有界MP4/MOV、单H.264/yuv420p/CFR、0..2 AAC-LC mono/stereo、BT.709有限或未标色等全部限制；HEVC/VFR/Alpha/HDR/字幕/非单位SAR/复杂edit等明确拒绝。不会把允许转码扩大成任意格式兼容。

P02尺寸按PROD-002：横框1280×720、竖框720×1280；s=min(1,Bw/W,Bh/H)，内容宽高max(2,floor(原边×s+0.5))，画布各向上取偶数，仅右/下0或1像素黑padding，SAR=1。使用确定的`bicubic+accurate_rnd`缩放与对应padding；保持原CFR与色域/transfer，不自动做颜色空间或HDR转换。独立质量参考对源使用相同几何变换后对齐比较，不能与未缩放源直接PSNR。

成功result显式`strategy:transcode`、profile、reencoded:true/lossless:false、请求/实际范围、output_media_info、transcode_plan和validation。计划报告source音轨全局indices、内容/画布尺寸、右/下padding与实际选择。沿用全源选帧、全部所选音轨解码/时轴/内容判据；Y/U/V平均PSNR各≥30dB、709固定色块U/V误差≤8，带限音频逐声道RMS/cosine及共同PTS检查不冒充完整听感。旧准确和流复制result字段不改，旧平台不会自行提交新请求。

## 原子批接收与身份

```json
{"schema_version":0,"operation":"batch_submit","submission_id":"example_001",
 "items":[{"schema_version":0,"operation":"transcode",
  "source":{"uri":"file:///EXAMPLE/a.mp4"},
  "destination":{"uri":"file:///EXAMPLE/new-a.mp4"},
  "profile":"mp4_h264_standard_v1","audio_selection":{"mode":"all_supported"}}]}
```

示例文件见[配置](examples/core004-media-config.json)、[转码](examples/transcode-request.json)、[批接收](examples/batch-submit-request.json)、[快照](examples/queue-snapshot-request.json)，EXAMPLE路径不代表可执行环境。

items为1..32个既有trim或新transcode请求；不含probe/mock/嵌套控制。整条消息仍受65536字节上限。items空数组同步INVALID_ARGUMENT，超过32同步LIMIT_EXCEEDED；合法长度但与现有活跃总数合计超32才是accepted:false/RESOURCE_LIMIT。submission_id为1..64 ASCII `[A-Za-z0-9_-]`，客户端生成唯一身份，不使用用户路径作为key。

先验证全部语法、容量、源身份/支持范围/区间/音轨/依赖、目标与父目录/权限、批内目标冲突及资源预算（目标判重按父目录dev/ino和本卷OS实际文件名等价规则，涵盖大小写/Unicode规范化别名，不只比较PathBuf字节）；任一已知非法项整批拒绝，无部分作业/最终目标。预检可以创建并清理受控私有写权限探针、执行真实工具探测，不是“完全无I/O”。预检不做输出编码；不承诺全部码流全解码，ffprobe布局/帧/范围中不可见的损坏仍须执行前全轨解码并按项失败，不能称输出验证已被预检替代。所有成功预检才原子建立全部jobs并唤醒worker。运行可能先于调用返回，但不得看到部分接收的批。

语法/结构错误仍同步固定ABI错误且无job。可解释的媒体/容量/预算业务预检失败返回MS_OK控制响应`accepted:false,errors:[{index,code,message}]`，不带新job_ids；错误不回显原路径/stderr。接收返回`schema_version,operation,accepted:true,replayed:false,submission_id,job_ids`。规范化指解析后强类型消息（字段顺序、URI百分号编码与省略audio默认All归一），不额外访问文件系统；dot/../symlink/case等路径别名不作为等价payload，可保守SUBMISSION_CONFLICT。同ID及规范化相同内容返回replayed:true与原IDs，不再次编码；不同内容为SUBMISSION_CONFLICT，不能覆盖旧提交。

去重记录有界保留最近100个accepted提交；关联非终态的记录不能被淘汰/清除。过期或显式清除后相同key可被视作新提交，客户端不得靠重复key实现永久恢复。job详情最多保留100终态；去重重放可返回已回收的原ID，snapshot对此返回NOT_FOUND，绝不创建替代作业。重启不保留engine/job_id或自动续跑。

同步预检可能耗时，调用者仍按既有FFI规则放后台并串行调用；本版本不承诺同engine预检期间并发cancel/destroy。能力检测/工具阶段受明确期限，不能用跨线程无效指针绕过调用规则。

## 排程、控制与历史

- FIFO使用当前待执行顺序。进程内所有真实trim/transcode（含旧engine）共享单重任务permit；probe是轻探测。跨engine不同线程仍遵守同engine串行规则。只有获得permit且原子取当前队首后才能标running，等待permit的queued仍能排序/取消。此限制不是跨进程/其他软件的全系统锁。
- `queue_snapshot`返回schema_version/operation、queued_job_ids、running_job_id、active_count、queue_capacity:32、history_limit:100、resources及history（仅终态）；摘要包含job_id/operation/state/source_name/destination_name/requested_range_us/profile/audio_selection/error/created_sequence，不包含原始路径。普通job snapshot保持旧topshape并可附request_summary/created_sequence；不把未接收请求造为queued，不造百分比，未知progress保留null。
- `queue_reorder`携带job_ids，必须是当前全部queued的精确排列，无重复/遗漏/运行项；否则拒绝不改变原顺序。成功返回accepted:true/queued_job_ids；业务失败accepted:false/errors，顺序不变。运行/验证项不可移动，不提供pause/resume假状态。
- `job_retry`携带旧job_id、new_destination和新submission_id，只允许failed/cancelled。保留原source/range/profile/audio选择，重新预检，新job_id、新且按文件系统规则不同于旧目标的位置（包括../、符号链接父目录、大小写/Unicode别名拒绝）；成功项不重试、不覆盖。共享提交去重/容量语义。
- `history_clear`仅清终态摘要/详情及对应无活跃关联的去重记录，不能清理活动作业/预留/输出文件。成功返回accepted:true/cleared_count；已清除/回收job查询NOT_FOUND。
- 最近100条仅是本机允许摘要，host可按MAC-002规则保存文件名/授权引用；Core不新增磁盘历史库、不保存凭据/handle或自动续跑。原始路径/完整请求只留在有界内存执行/重试记录，不写普通日志。平台持久化失效授权、异常中断与清除界面由MAC-003接入。

既有cancel接口用于queued/running。queued取消不启动处理/输出，running先cancelling、真实进程回收/暂存清理后才cancelled。一个接收后源变化/目标竞争/处理失败不抹除其他项，队列继续；清理不能确认时CLEANUP_FAILED阻止危险复用，不能用继续队列掩盖未收回资源。destroy在非终态BUSY保留句柄；调用者先取消并轮询，15秒未确认关闭仍保留owner并报错，不能丢弃句柄或假称已关。

## 文件、空间与资源

受控staging在目标父目录，源只读，最终目标no-clobber提交；执行前再次核实冻结源身份、规范目标、父目录dev/ino及空间；父目录被同卷替换为新inode也以SOURCE_CHANGED拒绝。接收后的竞争按单项结构化失败，不重新接受变化源。危险输入只通过参数数组传递，不拼shell。

预估并强制监控媒体文件budget=`ceil(duration_us*4100000/1000000)+16MiB`，单项≤3GiB；磁盘reservation=`2*budget+64MiB`，同卷保留free floor64MiB。进程全局同卷RAII预留累计所有新模式待执行/活动项；失败批drop全部预留，终态清理确认才释放。queue_snapshot的原reserved_storage_bytes/estimated_retained_plan_bytes仅描述active作业预留；另附global scalar资源快照报告整个进程（含quarantine终态仍持有）的预留/target数/计划估算，不能把active=0理解为已确认全局归还。max_output_bytes仍只约束工具捕获输出，不当媒体文件预算。外部进程/用户写盘可竞争，运行前和执行期间仍须处理OUT_OF_DISK/RESOURCE_LIMIT，文件size监控是有界采样/完成后检查而非逐字节内核磁盘配额，不承诺排他独占磁盘。

新模式累计保留计划计量≤128MiB，具体估算计量与上限在结果/能力中可追踪；不是声称实际RSS硬上限。计划过大/预算不足明确拒绝。macOS空间检测使用绝对`/bin/df`与参数数组、C locale、严格有界输出解析，失败不假设无限可用；它是宿主系统工具，不下载/打包新第三方。资源预留不等于已实测全部32×10分钟/最大尺寸长压。

## CLI与验收

### 2026-11-14 扩展：只读空间预览（ADR-0017，Integration 只读审核通过；动态验收另记）

`storage_preview` 仅在 CORE-004 显式模式可用；沿用 `ms_submit_job`，不新增 C 函数、schema 版本或依赖。旧引擎返回 `UNSUPPORTED_OPERATION`。请求只允许 schema_version、operation、items，items 同 `batch_submit` 的 1..32 个 trim/transcode 子请求；不接受 submission_id、probe、嵌套控制消息或任意编码参数。

```json
{"schema_version":0,"operation":"storage_preview","items":[{"schema_version":0,"operation":"trim","source":{"uri":"file:///EXAMPLE/source.mp4"},"destination":{"uri":"file:///EXAMPLE/new.mp4"},"range":{"start_us":1100000,"end_us":2600000},"strategy":"stream_copy","profile":"mp4_h264_copy_v1","stream_selection":"all_supported","allow_reencode_fallback":false}]}
```

成功示例（数值为说明，不是固定输出）：

```json
{"schema_version":0,"operation":"storage_preview","ready":true,"volumes":[{"volume_index":0,"item_indices":[0],"estimated_output_bytes":22927216,"available_storage_bytes":1000000000,"required_storage_bytes":180072160,"sufficient":true}],"errors":[]}
```

同一例若 available_storage_bytes=180072159，其余 required/budget 保持，返回 ready=false、该卷 sufficient=false、errors=[{"index":0,"code":"OUT_OF_DISK","message":"Insufficient unreserved output storage."}]。缺源、目标存在、不可读取父目录、无法可靠读取空间等返回 ready=false 和 indexed errors；无数字可得的项不伪造 volume。语法/长度/字段错误仍走固定 ABI 错误，无 JSON/作业。

每卷 `volume_index` 按首次出现顺序编号，`item_indices` 是原请求下标；不暴露卷路径/设备。`estimated_output_bytes` 是本卷保守媒体 budget 总和；`required_storage_bytes`=已有本卷全局预留+本批每项(2*budget+64MiB)之和+64MiB floor。预算函数与接收预检共用。`available_storage_bytes` 是本次有界 df 观察；同卷多次读取时保守取最小值。已有 AI 预留活动时 RESOURCE_LIMIT；清理未确认时 CLEANUP_FAILED。

`ready` 仅表示当时空间数字可得且足够，不是完整媒体/编码器/权限预检或提交成功；不带 accepted/job_ids/submission_id，不改变队列/历史/去重、不建立持久预留/计划、不创建 staging/目标、不唤醒媒体 worker。整个预览共享 timeout_ms。它检查可读源与目标父目录，但不试写，所以只读卷仍可能在空间方面 ready；提交时可写性仍会被原预检拒绝。客户端必须说明 budget 不是成品大小/成功保证，并在确认后走原 batch_submit/job_retry 重新验证。预览到确认间的外部写盘、源/目标变化和实际运行 ENOSPC 均不能被预览结果豁免。

新增transcode入口和`core004-session CONFIG_FILE` JSONL会话，使用同一Engine；会话的submit(request)/snapshot(job_id)/cancel(job_id)/wait(job_id)/close只是薄调用，不能客户端自造排程器。close在仍有非终态/清理未确认时BUSY且保留engine，调用者cancel/poll后重试；EOF/超长行不是成功close，RAII仍实际回收。wrapper JSONL行同样最多65536字节，超长不得重解释尾部为新命令。CLI具体参数/响应见其README与真实命令证据。

先新增契约成功/失败与旧请求兼容测试，再实现。独立QA验收P01/P02横竖/小尺寸/取整、0/1/2音轨及源索引、32/33/坏项原子性、幂等/重排/重试/回收、接收后竞争混合结果、跨engine单重执行、真实PID/staging取消/超时/关闭与空间不足、旧准确/流复制/ABI回归。仅运行过的本机Core/CLI/C ABI可报通过；MAC-003界面、Intel/Windows/HarmonyOS/硬件编码/主观/最大负载未测项保留。
