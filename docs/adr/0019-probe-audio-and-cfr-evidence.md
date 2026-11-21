# ADR-0019：显式音频探测与 CFR 证据

日期：2026-11-14。状态：ACCEPTED_STATIC_REVIEW。Integration 独立静态复审及最终签名 Core 控制通过，见批准与源码身份及最终绑定索引。此状态不替代最终 GUI、完整 QA 或发行接收。

## 问题与决策

PROD-002 要求音频原音探测/试听；现有 probe 强制 MP4 视频，AI 分离后试听无法替代。另 avg_frame_rate 不能证明逐帧 CFR，UI 不应据此猜帧位置。

| 边界 | 唯一责任 |
|---|---|
| Rust Core | 显式 core003 probe 新增独立音频分支；核对字节、流、实际解码帧时间及身份。视频逐帧证明 CFR 后才给可选有理帧率证据 |
| Rust 视频导出 | 保留原支持范围，不消费音频探测以启用音频裁剪/转码 |
| Swift bridge/model | 解码可选音频/帧率证据；音频禁止视频导出、批导出、视频选区和逐帧 |
| AVFoundation / UI | 原音播放/暂停/定位；视频使用 Core 已证明的有理网格定位，文本和面板保留键盘操作 |

这样保持一个媒体探测来源，不在 UI 调 ffprobe 或复制支持判断。无依赖、许可、SDK、模型变化。

## 协议与兼容

八函数 ABI、buffer 所有权、同 engine 串行调用、schema_version=0、既有请求字段及错误数值不变。仅 core003_enabled:true 扩展 probe 输入域；关闭时旧行为保持。原音代理采用下述独立严格操作对象，不给旧请求增加隐式参数。

音频结果给 media_kind:"audio"、audio_origin_us、duration_us、真实 container/streams；省略 video_origin_us、video_frame_count、video_cfr_rate，不造假视频。音频时间原点为首实际解码内容 PTS，时长至末内容结束，均整数微秒。只接受本地普通文件、单音频流、无视频/封面/附件，WAV PCM16/24/float32、FLAC、MP3、M4A AAC-LC，44.1/48kHz mono/stereo，1–600 秒。未知或复杂时轴拒绝，原输入身份/外部引用/元数据及输出预算仍受限。

AAC 尾帧可能包含编码填充。仅在全部解码帧连续、stream start_pts 与首内容 PTS 相符（≤一个有效时间 tick）、声明末点晚于最后帧起点且不超过解码末点加一个有效 tick 时，取声明末点和解码末点中较早者为内容尾；不跨多个帧缩短内容。包括合法恰好末点及量化，其他格式不套此规则。矛盾起点/时长拒绝，不按 format.duration 猜测或放宽 600 秒上限；有效 tick 本身不大于一个样本。

MP4/MOV edit list 仅无 edit 或一个 rate=1 内容段、可带前置 empty 段属于视频导出支持范围。重复内容段/变速编辑保留视频探测信息并标不支持，所有视频导出拒绝；M4A 音频探测也拒绝复杂布局，不能用 flattened CFR 冒充原 edit 布局证明。MOV 缺语言与 MP4 的 und 均代表未指定，只有这种语义等价；已知语言仍严格匹配。

## 原音预览副本

真实 mono MP3 在 Apple 媒体栈缺 gapless priming 表，多个原生播放器均比 Core 内容时轴多出填充；不能放宽时长容差或硬编码解码延迟。新增严格显式请求 `prepare_audio_preview`，固定 profile `wav_pcm_f32_preview_v1`、本地 source/destination URI，无 range 或任意编码参数。需 core003/core004 同时启用；使用 transcode_timeout_ms。

Core 保持原采样率/声道，按已证明内容范围生成 float32 WAV，输出原点0，返回准确 sample_count 和 source origin 映射，标记 preview_only。样本数由精确时轴证明，不能从截断微秒再 floor 得出。依赖 core004 资源与原子队列，因此要求 core003/core004 同时启用；capabilities 的能力键为 `prepare_audio_preview`，不冒充用户音频转码。复用单重调度、Source 身份、storage/target leases、同父 Temporary + hard_link 原子创建新名（不覆盖），清理失败按身份回滚。完整探测/解码/有限值/样本数及 source unchanged 校验、可失败序列化都在发布前。取消、期限、真实 IO、坏输出及清理失败沿用原错误/owner语义；不是新增音频导出或 AI 分离能力。

Native 仅为当前不能证明原生映射的音频生成一个有界私有副本；源和目录授权持有至 Core 停止/清理确认。切换先停止 AVPlayer，再取消/等待 Core、删除自有副本；不能清理则保留 owner、隔离入口和关闭，不假称 ready。新增 operation/domain/result 分支不改八函数 ABI，关闭扩展时拒绝；旧请求行为保持。成功及 source=destination、已有目标、坏输入、取消和清理失败须有独立证据。

视频既有字段语义不变，可选 video_cfr_rate 为有理数字符串，仅实际帧 PTS/duration 证明固定网格并符合六个必选帧率才输出；VFR/未知时省略。新客户端接受旧结果缺失该证据并禁用逐帧，不默认 30fps。能力报告分别说明音频 probe 与视频导出限制。

旧客户端可能无法解码原本不支持的音频结果，不承诺新增输入域对旧客户端透明兼容；既有视频字段保持兼容。新客户端不因音频 probe 成功启用视频导出；误传视频请求仍明确拒绝，无输出。

## 验证与回滚

成功音频真实 probe/全解码及损坏、异常采样率/声道、视频导出拒绝等失败测试先行；验证旧视频及关闭扩展兼容。六帧率/VFR 负例验证 CFR 证据，原生验证播放/定位、切换隔离、帧往返与非法区间；最终包另做实际 GUI，不以模型测试冒充。

撤回可选分支及 UI 入口即可回滚，无持久数据迁移。其他平台、旧 OS、发行签名、公证及资源基线不继承本机证据。
