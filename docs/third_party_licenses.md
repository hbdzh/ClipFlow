# 第三方组件与模型许可台账

2026-11-16 更新。**本机受控开发与公开发行分别判断；当前公开发行 NO_GO。** 下表绑定 `MediaStudio-QA002-Release-v6.app`，不把框架许可覆盖模型权重，不把独立进程当 GPL 豁免，不把旧实验环境全部当生产依赖。

| 组件 | 实际用途与固定版本 | 当前许可与证据 | 未解决的发行边界 |
|---|---|---|---|
| FFmpeg / ffprobe | 包内独立 helper；FFmpeg 7.1.5，源码 SHA `de668509caf9e35e3cd162473441fdb29538c6d96ed080292b3cf9e6fc5d558f` | `--enable-gpl --enable-libx264 --disable-network --disable-autodetect --disable-shared --enable-static`；实际查询 GPL v2 or later；GPL/LGPL/组件 LICENSE.md 与 IJG 原文已入最终包。新工具直接构建输入至签名产物已独立绑定。 | 对应源码的实际交付、修改/构建安装信息、用户权利、渠道/EULA/专利与正式签名公证未接收；并非仅因 GPL 就自动禁止商业使用。 |
| x264 | `31e19f92f00c7003fa115047ce50978bc98c3a0d+mediastudio-open-cleanup-5`，静态链接进入 helper | 原archive SHA `d053c9d86988d6bc78237ca5205865c5ddf99c98ef4cd9927eec8f6d388f6dd9`；参数/构造/晚期/可选/MBAFF五patch有序固定，最终十一份C/H与helper独立绑定，包内附原GPL和五patch。 | 10位隔行切片的上邻居field偏置越过线程边界已修，另去除邻居缓存中同类无用共享读取；独立pixel矩阵81、组合对照63和最终helper12次正常编码重复性通过，8/10位TFF、BFF及非均分片高分别有界验证。当前baseline有限轮次也恰好稳定，不声称新AB动态重现；旧FAIL/空间证据保持。受影响两组新旧码流有意不同，原strict FAIL保留；16非影响组严格相同。RC/OpenCL旧有限修补按原证据复用；活动context/caller/temp合同、失败分支STATIC_ONLY及完整CVE边界保留。GPL实际交付/渠道另审。 |
| Rust registry crates | Core 11、native worker 33，共44唯一锁定包；两个 Cargo.lock | registry archive checksum、包内 inventory、声明许可及全部文本逐项核对；MIT/Apache-2.0、ISC、Unicode附加条款等按各包原文，不能概括成全部MIT。 | lock保守含可选/条件包，不自动证明每项链接进当前target。依赖安全审计另有实际结果，不由许可匹配替代。 |
| ONNX Runtime | 固定1.22.1提交 `89746dc19a0a1ae59ebf4b16df9acab8f99f3925` 的arm64 CPU受控衍生dylib；API21 | 实际641对象/46静态归档/2041非SDK物料来源核对闭合；ORT及12外部来源、嵌入声明、官方与本地patch、生成代码单列。40份精确材料及371份MPL覆盖源码入包，当前140份整包许可材料独立比对通过。 | 此闭包只适用于新dylib，不追认旧wheel。MIT、Apache/LLVMexception、MPL、BSD等按实际映射；不是框架MIT覆盖全部内容。公开分发与既有安全公告VEX边界仍分别审查。 |
| UMX-HQ 原权重 | 原作者Zenodo3370489，1.0.1；固定转换图来源链 | 作者记录API和v2原权重完整SHA核对证据保留；本轮转换模型/八图按同字节绑定复用，不凭框架MIT推断权重，也不写成本轮新推理/听感验收。 | 训练数据、其他权益与实际分发方案另审；MIT声明不等于所有风险清零。 |
| 固定 ONNX / 八子图 | 转换提交 `e06097d3193a4c3168b5ddd2a479a680b1176c16`；模型SHA `f27742bb52b24cb039614dc76d0764e279df3eb2a9d6daa82c6daee3369ec747` | 固定提交MIT原文与包内相同，八图匹配manifest，生成脚本SHA另列；转换者来源/改动说明归档。 | 权利依据、衍生来源说明、获取与分发方式分别记录；不宣称转换全流程独立重现或训练权利无争议。 |
| 系统 SDK / 硬件能力 | Apple系统框架；本轮仅macOS arm64本机开发 | 当前未扩SDK最低版本、sandbox entitlement或编码策略。 | 硬件、Intel、其他OS、正式渠道许可与发布审查不继承本机结果。 |

本轮x264隔行切片重复性报告、v6独立工件审查、v6清单与签名绑定为当前指针。旧v5可选路径报告及157文件/154封版证据、旧v4晚期中止报告及156/106、旧v3的155/85保持冻结；旧隔行失败不改写。ORT/worker/模型字节保持，仅按原v2精确闭包与CPU/质量证据复用，本轮未重跑AI，不写成v6新测。前次依赖闭包与运行验收、独立工件审查保留来源及范围。ORT精确材料闭包已通过；44个crate继续保守按锁清单列出，不宣称整应用逐目标链接精确。旧Release-v1的wheel未知闭包保持原工件身份。

本次决定见[ADR-0027](adr/0027-x264-mbaff-threadslice-neighbor.md)与独立准入；旧[ADR-0026](adr/0026-x264-optional-no-publish-and-scratch-order.md)保持冻结；旧[ADR-0025](adr/0025-x264-private-open-abort.md)保持冻结；前次[ADR-0024](adr/0024-x264-staged-open-failure-hardening.md)保持冻结。固定维护与前次本地衍生构建分别见 [ADR-0023](adr/0023-exact-runtime-closure-and-x264-cleanup.md)、[ADR-0022](adr/0022-ffmpeg-security-maintenance.md) 和 Integration静态复审。旧 [MAC-005许可报告](mac-005/LICENSE-REVIEW.md)、AI实验发行候选报告保留历史身份；其中“未打包”“开发包未定”等当时状态不再替代当前实际清单。此前研究环境的Python/PyTorch/SoundFile等依赖不能直接继承为此原生包，也不能据其旧许可缺口反推当前包含有它们。

不提交权重、用户素材、外部二进制或签名材料至Git；无正式发布动作。正式分发前仍需针对实际渠道和收件人履行适用义务，必要时取得专业法律意见。
