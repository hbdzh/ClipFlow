# ADR-0022：FFmpeg 7.1 安全维护与本机开发工件

日期：2026-11-15。状态：Accepted，仅批准本机开发的固定依赖维护变更；Integration 静态复审不替代运行或发行接收。范围仅 QA-002 的 macOS arm64 受控开发候选，不授权正式发行。

## 问题与决定

现有 FFmpeg 7.1.1 缺少上游 7.1.2 及以后维护补丁。上游安全页所列 AAC 编码修复涉及当前 P01/P02 可达代码；禁用网络不能替代本地不可信媒体的安全更新。不运行漏洞样本，不实现或复现利用链。

拟固定同分支官方 FFmpeg **7.1.5**（2026-06-20），源码 HTTPS 地址 `https://ffmpeg.org/releases/ffmpeg-7.1.5.tar.xz`，SHA-256 `de668509caf9e35e3cd162473441fdb29538c6d96ed080292b3cf9e6fc5d558f`。签名文件留存，未安装 GnuPG 时不声称 PGP 验证通过。保留 x264 固定提交 `31e19f92f00c7003fa115047ce50978bc98c3a0d` 与原 archive SHA `d053c9d86988d6bc78237ca5205865c5ddf99c98ef4cd9927eec8f6d388f6dd9`。不得使用 latest。

构建保留 `--disable-autodetect --disable-doc --disable-debug --disable-ffplay --disable-network --enable-gpl --enable-libx264 --disable-shared --enable-static --pkg-config-flags=--static`；x264 保留 `--enable-static --disable-cli --disable-opencl`。不更改 Core 的固定 CRF/preset/codec、操作白名单、ABI、SDK、模型、ORT 或 sandbox entitlement。不添加新编解码策略或网络能力。

## 所有权与工件

Rust Core 仍拥有媒体接收、参数、范围、队列、取消和验证；Swift 只使用包内工具路径。构建脚本锁定源码版本和摘要，完整保留构建命令、源清单、未签名摘要、签名动作和签名后工件摘要；新建候选，不覆盖 Closure-v6。补齐 FFmpeg `LICENSE.md` 与 GPL/LGPL/x264、Rust、模型和 ORT 的原有许可文本。SBOM 区分 build-only/条件依赖和实际工件。

## 许可与发布边界

仍是启用 GPL 的独立 helper 和静态 x264 构建。子进程隔离不是 GPL 豁免；维护更新不自动证明完整闭源商业分发、专利或渠道合规。包内完整对应源码的提供方式、许可履约与渠道结论分别审核。对外分发保持 NO_GO，真实证书、公证和商店操作不在授权范围。

## 验证与回滚

更新前保留 7.1.1 的已知风险及原包冻结；更新后检查实际 `-version/-buildconf/-L`、无网络、依赖闭包、签名、来源与 SBOM；执行 Core/外部 ABI、媒体全矩阵、CPU AI、取消/失败及原生 GUI 静音冒烟。20 次生命周期和10分钟活动资源基线必须绑定最终工具与原始 RSS/PID/staging 曲线，未执行不填 PASS。正常正例、拒绝不支持布局及取消后无残留是必验路径。有限回归与公告检查不证明不存在所有漏洞。

若维护更新导致行为回归，保留新候选失败证据，修复兼容问题；不得用回退到已知易受影响版本充当安全通过。

## 依据

- [FFmpeg 官方下载与固定维护分支](https://ffmpeg.org/download.html)
- [FFmpeg 官方安全修复](https://ffmpeg.org/security.html)
- [FFmpeg 官方许可](https://ffmpeg.org/legal.html)
- 实际下载摘要：`docs/qa-002/evidence/release-20261115/ffmpeg-7.1.5-source-download.json`
