# CORE-003：显式精确重编码与受限媒体扩展（V0兼容）

2026-11-10，Integration/Core联合审定；实现/独立验收状态另见交付报告。产品依据为PROD-002，决策见[ADR-0009](../docs/adr/0009-core-003-accurate-trim.md)。八个C ABI、buffer布局、释放方、同engine串行化、schema_version=0及同步错误数值保持[原契约](README.md)；新功能仅显式配置/请求启用，不改变默认mock。

新客户端用含core003_enabled的配置遇旧Core应报告该扩展不支持；不能静默删profile/改策略来执行。旧Core可合法拒绝新字段，本次兼容承诺是新Core继续接受旧调用，不要求旧二进制理解新消息。配置与请求例子见[配置](examples/core003-media-config.json)、[准确请求](examples/accurate-trim-request.json)、[扩展copy](examples/stream-copy-v1-request.json)，EXAMPLE路径不是可执行环境。

## 配置与处理期限

在已有`media_backend`内新增可选严格布尔`core003_enabled`（缺省false）。只有显式true才接纳新profile及报告准确能力，旧配置继续accurate_reencode.available=false且不额外检测编码器。新profile配旧flag的语法正确请求可以排队，但必须在文件/进程I/O前UNSUPPORTED_BACKEND，不得擅自启用。

另新增可选`accurate_reencode_timeout_ms`，整数1..7200000，缺省600000（10分钟）。旧`timeout_ms`仍缺省30000、范围1..300000，控制probe、legacy和扩展stream-copy及能力检测；新字段仅控制accurate_reencode，覆盖版本/编码器检查、源分析、重编码、输出验证与清理前检查，不含排队。所有阶段共享该次操作deadline，不在每个子进程重置。output预算等旧字段不变，未知字段/类型/零/负数/溢出同步INVALID_ARGUMENT，无ghost job。CLI defensive期限须使用当前操作期限加既有关闭余量。

## 显式请求

```json
{
  "schema_version":0,"operation":"trim",
  "source":{"uri":"file:///EXAMPLE/source.mp4"},
  "destination":{"uri":"file:///EXAMPLE/new-output.mp4"},
  "range":{"start_us":1100000,"end_us":2600000},
  "strategy":"accurate_reencode",
  "profile":"mp4_h264_standard_v1",
  "stream_selection":"all_supported",
  "allow_reencode_fallback":false
}
```

- accurate必须有上述固定profile；错误/缺省profile同步拒绝，不接收任意codec/CRF/filter/FFmpeg参数。`allow_reencode_fallback:false`禁止另行切换策略，不否定用户已选的重编码。
- legacy `strategy:stream_copy`且无profile按CORE-002原规则执行（含显式颜色拒绝）；旧请求/默认配置/旧错误用例继续有效。
- 新`strategy:stream_copy,profile:mp4_h264_copy_v1`显式启用下述CORE-003受限范围，包括709元数据；保留关键帧包围语义，不重编码。不允许把精确profile用于流复制或反之。
- 两种显式profile仍只支持`all_supported`及`allow_reencode_fallback:false`；音轨选择/转码队列属于CORE-004，不能静默删轨。未知/互斥字段同步拒绝；语法正确但媒体不适用可排队后结构化失败。
- 内部强类型为`TrimStrategy::{StreamCopy,StreamCopyV1,AccurateReencode}`；这是Rust实现类型，不跨C ABI暴露。

## 严格支持范围与能力

新profile：仅本机已接纳macOS文件后端；MP4/MOV单H.264、8-bit yuv420p、偶数宽高≥2、长边≤1920短边≤1080、时长≤600s；CFR仅24/25/30/60及24000/1001、30000/1001 fps（有理数，不靠浮点近似判同）。0..2条AAC-LC音轨，每轨mono/stereo、44.1/48kHz；所有支持音轨保留。源帧PTS/持续时间须能证明CFR、无洞且有界，不能只看r_frame_rate。

颜色允许未标色/unknown，或明确BT.709有限(tv)范围；混杂/不完整已知色彩、HDR、Alpha、旋转/变换DisplayMatrix、非单位SAR、字幕/附件/多视频/其他音频布局/VFR等明确UNSUPPORTED_FORMAT，不能自动色彩转换、丢轨或CFR化。未知/外部引用/网络协议仍按旧安全边界拒绝。

能力分开报告legacy stream-copy、新copy profile及accurate profile支持/限制。accurate可用必须先确认工具版本和固定libx264、AAC编码器存在，不得由操作系统名称或stream-copy可用推断；执行时仍核对真实编码/验证结果。只允许CPU软件P01：libx264 CRF23/medium、yuv420p，尺寸/有理CFR保持；AAC-LC按每轨stereo192kbps/mono96kbps，保留采样率/语言/声道。不静默换硬件编码器。现有GPL构建仅开发外部运行时，不复制/随包分发；本轮无新registry依赖或编码器入库批准。

## 精确时间与内容

### 显式 probe 补充（ADR-0019，Integration 静态批准）

仅 `core003_enabled:true` 的现有 probe 可返回独立音频结果：`media_kind:"audio"`、`audio_origin_us`、`duration_us`、真实 `container` / `streams`，省略视频 origin/frame_count。音频原点为首实际解码内容 PTS，时长至末内容结束，均整数微秒；只接受本地单音频流 WAV PCM16/24/float32、FLAC、MP3、M4A AAC-LC，44.1/48kHz mono/stereo、1–600秒，无视频/封面/附件。不支持布局或时间无法证明则拒绝。视频导出范围不变，不启用音频裁剪/转码。

音频结果另返回整数 `sample_count`（按精确有理内容跨度确定的音频样本帧数）、`decoded_duration_us`（实际解码含尾填充的时长）及 `codec_tail_padding_us`（已证明排除的末帧填充，微秒）。样本帧数不是交错 PCM 标量个数，不从已截断的 duration_us 二次推导。

视频既有字段语义不变，可增加 `video_cfr_rate` 有理数字符串；实际逐帧 PTS/duration 已证明固定网格且符合六个必选帧率才返回，VFR/未知时省略。新客户端接受旧结果缺字段并禁用逐帧，不以 avg_frame_rate 猜测。关闭 core003 的旧 probe、八函数 ABI、请求 JSON、错误码及所有权不变；旧客户端可能无法解码原本不支持的音频结果，不承诺新增输入域透明兼容。能力报告区分音频 probe 与视频导出范围。决策与验收见 [ADR-0019](../docs/adr/0019-probe-audio-and-cfr-evidence.md)。

AAC 仅在实际解码帧连续、stream start_pts 匹配首内容 PTS（≤一个有效 tick，tick≤一个样本）、声明末点晚于最后帧起点且不超过解码末点加一个有效 tick 时，取声明末点和解码末点中较早者排除最后一帧内填充（包含合法恰好末点及量化）。跨多帧缩短、起点矛盾或声明末点不合法均拒绝；其他格式不套此规则，不以 format.duration 替代实际内容证明。MP4/MOV edit list 仅无 edit 或一个 rate=1 内容段、可带前置 empty 段为视频导出支持范围；重复/变速 edit 保留视频探测并标不支持、导出拒绝，复杂 M4A 音频探测拒绝，不以 flattened PTS 冒充布局证明。详见 ADR-0019。

### 原音预览副本（同 ADR-0019，Integration 静态批准）

严格请求为 `{"schema_version":0,"operation":"prepare_audio_preview","source":{"uri":"file:///EXAMPLE/input.mp3"},"destination":{"uri":"file:///EXAMPLE/new-preview.wav"},"profile":"wav_pcm_f32_preview_v1"}`；无 range/额外参数，未知字段拒绝。仅 core003/core004 同时启用，使用 transcode_timeout_ms 和全局单重调度。Core 按已证内容起点/有效尾生成保原 rate/channels 的 float32 WAV，输出原点0；准确样本数不能从截断微秒再 floor 推测。结果标 preview_only 并给 sample_count、sample_rate / channels、source origin 映射，不启用音频裁剪/转码或 AI。

复用同父 staging 无覆盖发布、源身份和 target/storage leases；完整 probe/解码/有限值/采样数、源 unchanged 及序列化成功后才发布。取消/超时/坏输出/空间/清理失败沿用原错误和 owner 语义。Native 当前副本和源/目录 scope 持有到播放器停止及 Core/副本清理确认；失败隔离不丢 owner。八函数 ABI、旧请求不变，未启扩展拒绝新操作。真实 mono MP3 的原生 priming 缺失不能靠放宽预览容差绕过，详细边界与验证见 ADR-0019。

终态 succeeded 的 result 如下（示例数值非测试成绩），字段类型为整数/布尔/固定字符串；不得把它解释为视频 ExportStrategy。原子发布使用既有同父 Temporary + hard_link 新名，无覆盖；失败按身份回滚。capabilities 能力键为 `prepare_audio_preview`，依赖 core004 的资源与队列语义。

```json
{"schema_version":0,"mock_only":false,"file_created":true,"preview_only":true,"profile":"wav_pcm_f32_preview_v1","sample_rate":44100,"channels":1,"sample_count":132300,"source_audio_origin_us":25056,"source_content_duration_us":3000000,"preview_validation":{"source_identity_unchanged":true,"full_decode":true,"finite_samples":true,"content_mapping_verified":true}}
```

sample_rate 必须为44100/48000，channels 为1/2，sample_count 为正且不超过 sample_rate×600；source_audio_origin_us / source_content_duration_us 必须匹配源 probe 的整数微秒，duration 为1–600秒。四项 preview_validation 均来自实际检查且必须为 true；输出归零与源内容的映射来自这些字段，不能以文件存在、命令退出0代替验证。

### 视频裁剪时间规则

请求区间为相对源首视频内容PTS的整数微秒`[start,end)`。选择满足`源PTS>=origin+start`的第一源帧和`源PTS<origin+end`的最后源帧；比较须用checked整数/有理数避免浮点边界/溢出，无帧区间拒绝。首尾量化误差各不超过一源帧，返回实际源帧PTS、time_base与实际范围，不能把微秒显示精度说成任意采样精确。

视频按已选源帧索引完整裁剪；共同实际归零原点取第一选中源视频帧PTS（距请求start≤一帧，actual_range如实报告）；音频按该实际绝对起点至请求end裁剪，各轨统一减该时刻，不能各轨减各自STARTPTS而隐藏原有相对偏移。CFR源输出保持同一有理帧率，可用显式CFR编码保证MP4帧duration；必须验证帧数/内容对应无重复丢帧，不能对VFR自动转换。处理AAC编码延迟/edit list，独立验证解码后的实际对齐。packet按decode-order DTS检查单调，decoded frame按presentation-order PTS检查；正常B帧packet序PTS可非单调。

## 提交前强制验证

- 源身份在分析/执行/验证后未变；输出仍在同父受控staging，无已存在目标覆盖，所有可失败序列化/预算/取消检查在发布前。
- 输出probe核对MP4/H.264/AAC、流数量/顺序/语言/采样率/声道、尺寸/SAR、CFR/时间基/709或未标色；全部轨道真实解码，首尾/帧数及完整源选择序列对应。
- lossy不能用压缩或原像素hash相等来验收。用相同源选帧/色彩空间做全帧Y/U/V参考对齐质量验证：均值PSNR各≥30dB；709固定色块fixture的U/V均值误差各≤8个8-bit码值，checker能拒绝Y不变而色度错换/红蓝错置的负例；记录判据与实际值。不把tag保持当颜色正确，不把PSNR当全色彩/HDR/听感证明。
- 音视频相对共同源时间映射误差≤max(40ms,一视频帧+一输出AAC帧)，无累计漂移；视频边界如上。全轨音频布局和实际时轴均有证据，不以时长大致相同或decoder退出0代替。
- 每音轨逐声道、覆盖全区间的≤60秒窗口，将共同PTS对齐的源/输出解码为8kHz带限float32 PCM，受单进程输出预算约束；相对RMS误差≤0.35且cosine≥0.90。参考RMS<1e-6时要求输出RMS≤1e-5，不除零、不平移相关峰。此新增防损坏/轨映射门槛据本机AAC噪声/tone/pulse/silence实验审定，不代替独立音频脉冲时延或全频/主观听感；结果必须明确带限统计与覆盖。
- extended stream-copy仍验证完整选中源帧hash与压缩packet、统一时间平移/连续性；709元数据不得损失。legacy算法/判断保持，旧测试不能被删除或放宽。

成功结果保留`requested_range_us`、`actual_range_us`、`actual_source_range_us`、`output_media_info`、`validation`等共用字段，并显式标`strategy:accurate_reencode`/固定profile、`reencoded:true,lossless:false`及精确计划/源time_base/选帧证据；不用`stream_copy_plan`冒充重编码。没有解码/内容/时序验证不能将相关validation字段置true。主观听感/完整观看若未执行必须false或明确未验证；真实CLI/C ABI证据不等同原生UI支持。扩展copy结果显式profile、reencoded:false，旧result语义继续兼容。

AC05 的具体偏差由原生端在核对同任务冻结请求后，对规范微秒 `requested_range_us` / `actual_range_us` 分别做 checked `actual-requested` 并显示带符号数值；不要求额外独立偏差 JSON key。Core 仍唯一决定实际范围，单纯并排显示两个区间不能替代偏差数值。此为既有表示责任的明确裁定，不改 AC05 或响应 schema，见 [ADR-0020](../docs/adr/0020-ac05-derived-range-deviation.md)。

## 失败、取消与所有权

沿用queued/running/cancelling/cancelled/succeeded/failed；不新造validating状态或假百分比。缺编码器DEPENDENCY_MISSING，不支持媒体UNSUPPORTED_FORMAT，质量/轨道/时序失败OUTPUT_VALIDATION_FAILED，空区间INVALID_INPUT；保留现有PERMISSION_DENIED、TIMEOUT、SOURCE_CHANGED、OUTPUT_EXISTS、RESOURCE_LIMIT、PROCESS_FAILED等。可明确识别实际磁盘耗尽时用OUT_OF_DISK且无原始路径/stderr外泄。

取消/超时/执行/验证失败均回收自己拥有的进程并清理staging；只有确认后才能cancelled。清理失败CLEANUP_FAILED，不能假成功/丢弃owner。成功发布后的晚取消仍succeeded；Rust缓冲仍由ms_buffer_free释放，同engine调用由caller串行化，所有长期阻塞/销毁走后台。当前仍不承诺任意外部fork树/敌意TOCTOU或跨重启自动恢复。

## 实现前契约与验收

新增严格JSON成功与缺profile/错误组合/不支持schema/零负超限deadline失败测试先行；当前Core拒绝无profile的accurate负例继续拒绝。CLI保留`trim`，新增`trim-accurate`和`trim-copy-v1`（构造core003_enabled=true），能力入口新增`capabilities --core003`；旧`capabilities --media`仍默认false，使用同一Engine/URI/状态/取消，不写第二套媒体参数；返回真实终态，未支持能力非零退出。

真实验证覆盖CFR/B帧、分数帧率、+5s起点、头/中/尾/空区间、0/1/2音轨、709/未标色、Unicode/注入字符，及旋转/HEVC/VFR/字幕等拒绝、缺编码器/已有目标/源变化/写入失败/超时/取消/关闭/验证坏输出。QA独立保留失败与旧黄金预期；本轮只完成Core/CLI/C ABI，原生UI由MAC-002接入，未验证平台照实记录。
