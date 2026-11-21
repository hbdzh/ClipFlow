# ADR-0009：显式精确重编码与受限输出验收

- 日期：2026-11-10；状态：Accepted（Core接口/媒体后端与Integration联合审定，QA复审兼容与时间量化）。
- 基线：PROD-002提交52d6fc7，经2f26a210d5dee0f15da1c98f85909635d6479f51合入main并推送。

## 范围与责任

依据PROD-002和CORE-003任务卡，先扩契约/失败用例，再实现精确裁剪、严格709/范围与输出验证。Core domain/CLI负责强类型请求/配置；media-ffmpeg负责参数、原点、编码/解码/验证/文件事务；orchestrator沿用同一状态机；Integration管理公共contracts和决策；QA独立管理新增tests/core_003及结果复审。原生UI/转码队列/AI不在本轮实现。

决定采用[CORE-003兼容扩展](../../contracts/CORE-003.md)：八C ABI/schema0不变，准确策略必需固定P01 profile，另用显式copy profile开启709/新范围。旧无profile流复制和mock保持原行为，避免旧客户端突然接受原来拒绝的显示元数据。已有准确无profile负例仍拒绝，不为新代码改旧预期。旧外部媒体能力黄金要求准确available=false，故新增显式core003_enabled布尔缺省false，新CLI/profile场景显式true；旧配置不新增编码器调用，不在旧客户端悄悄开启新功能。新准确deadline600000默认/7200000上限，旧deadline30秒/300秒不动。

精确模式选择源PTS位于[start,end)的完整帧区间并重编码，不采用smart-cut/静默回退。音频和视频用共同实际首选帧原点（距请求≤一帧且如实报告）；B帧decode/presentation顺序分开验收。帧/轨/编码/元数据/全量解码/参考YUV质量和音画时轴均验证后才能提交，不能以exit0/文件存在当成功。

## 取舍与许可

只按关键帧copy不能满足精确需求；只加-ss/-t或检查时长不能证明首尾内容/音画，故单独准确模块和全帧验证。沿用隔离外部FFmpeg，不新增codec实现、native库或registry依赖。软件参考使用已有7.1.1 GPL构建/libx264/AAC，仅开发运行时，未复制/随包分发或认定闭源合规；发行门禁不变，MAC-005另审。版本/编码器实际检测后才宣称准确可用。

本机FFmpeg参数实验显示亚帧偏移+passthrough会产生现有inspect无法证明的MP4帧duration；故在先证明源CFR后保持同一有理rate，统一以实际首选帧归零并逐帧验证无重复/丢帧。不修改PROD-002的一帧边界/共同映射阈值，不据此接纳VFR。实验原文由后端保留。

官方依据：[trim/atrim/PTS与PSNR](https://ffmpeg.org/ffmpeg-filters.html)、[seek/时间基与fps_mode](https://ffmpeg.org/ffmpeg.html)。线上文档不替代本机7.1.1参数/结果实测，原始环境与许可记录见环境证据。

后端实际AAC roundtrip实验支持增加8kHz逐声道/逐窗口音频内容检查（≤60s窗口，误差≤0.35/cosine≥0.90，静音单独分支）。本机噪声/tone/pulse测量与错静音/40ms延迟负例原文保留，Integration据事实接受防损坏指标；它是带限内容对照，不扩称全频保真或主观同步，独立QA仍按PROD-002测实际延迟。

## 验证与风险

成功用例：非关键帧1.1–2.6秒明确P01重编码，所选第一/末源帧正确且全轨/颜色/时轴/质量通过，源hash不变，结果清楚标有损。失败1：缺profile/错误组合/非法deadline同步拒绝无ghost；失败2：输出已存在/输入不支持/验证坏输出不得发布，取消/超时需进程/staging物理观察确认。新契约红测试与媒体实测结果另存docs/core-003；此ADR不预先宣告完成。

全帧分析/质量验证耗时较大，受共享deadline、帧数/时长/输出预算约束；软件重编码PSNR不能证明全场景感知保真，packet时间不能替代听感。内存/长素材/磁盘/敌意文件系统、真实GUI和非macOS均需按实测声明边界。回滚保持legacy和mock，显式新profile不可用时报错，不静默降策略或抹去源结果。
