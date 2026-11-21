# MAC-005 开发包依赖和模型许可复核

日期：2026-11-13。范围：macOS arm64 受控开发候选，以及最终开发包中实际使用的组件；不是法律意见。原版实验环境的许可审查仍见 AI-001 发行候选复核。

## 当前结论

**受控本机开发：有条件允许；对外提供安装包或正式发行：NO_GO。** 依赖是否可以放入内部开发包，应以本报告明确的来源、实际包内文件、许可材料及使用边界为条件；开发包和测试成功不等于许可已闭合。特别是 FFmpeg/x264 的 GPL 方案需要明确适用义务、完整对应源码与依赖材料以及组织选择的发布方式；不因为进程隔离就断言整个产品的许可证结论。模型训练数据、编解码专利、签名/渠道限制与开源软件许可是不同问题。后续需对实际交付工件完成文件级复核及独立许可决定。

## 模型和推理运行时

| 项 | 本机核查及来源 | 许可与剩余事项 |
|---|---|---|
| UMX-HQ 原始人声权重 | [Zenodo 3370489](https://zenodo.org/records/3370489)，v1.0.1；历史复核的权重 SHA-256 `b62c91cedbc7a066f1778ead5b5cecb377aa3a46a31af1cce7c5c8769339d083`。 | 记录标注 MIT；保留版权及完整许可文本。训练数据或其他权利不能仅据此自动确认。 |
| 固定转换图 | [MixDirective 固定提交 e06097d3](https://huggingface.co/MixDirective/open-unmix-umxhq-vocals-onnx/tree/e06097d3193a4c3168b5ddd2a479a680b1176c16)，本机模型 SHA-256 `f27742bb52b24cb039614dc76d0764e279df3eb2a9d6daa82c6daee3369ec747`。 | 来源声明 MIT、Inria 版权；携带对应文本。八个子图由本仓库脚本从该固定图生成并由 worker 验证固定散列；子图属于衍生工件，分发材料需说明来源、生成方法与许可。未在本轮独立验证原权重转换全流程。 |
| ONNX Runtime | 官方 [PyPI 1.22.1 wheel](https://pypi.org/project/onnxruntime/1.22.1/#files) `onnxruntime-1.22.1-cp313-cp313-macosx_13_0_universal2.whl`，本地 SHA-256 `d29c7d87b6cbed8fecfd09dca471832384d12a69e1ab873e5effbb94adc3e966`；其原生 dylib 来源 SHA-256 `7dfd3b3116d02d984d516c741c01453ec22d7cfd36e16242626579d4f5bbdc80`。历史核对该 dylib 与 wheel 对应文件一致。 | ONNX Runtime 顶层 MIT；从**同一 wheel**携带 `onnxruntime/LICENSE` 和 `onnxruntime/ThirdPartyNotices.txt`，SHA-256 分别为 `2f07c72751aed99790b8a4869cf2311df85a860b22ded05fa22803587a48922c` / `e9e90971a8e75a9a8ac0c6412e29c1202d079998389915aa485f46c816c3b4cc`。wheel 内的 Python 扩展和解释器不是原生 worker 的运行时依赖。第三方声明不等于已逐个核对静态依赖及其义务；签名后的 dylib 文件摘要会变。 |

## Rust 软件依赖

针对 `core/Cargo.lock` 和 `core/native-ai-worker/Cargo.lock` 执行离线锁定 `cargo metadata`：Core 解析的 11 个 registry crate 声明主要是 MIT/Apache-2.0；其中 `unicode-ident` 声明 `(MIT OR Apache-2.0) AND Unicode-3.0`，`memchr` 声明 `Unlicense OR MIT`，须按所选分支收集完整文本。worker 锁文件解析出 32 个 registry package，包含条件或可选项，声明覆盖 MIT、Apache-2.0 和 ISC，未发现 GPL 声明；直接依赖为 `hound 3.5.1`（Apache-2.0）、`ort/ort-sys 2.0.0-rc.15`、`rustfft 6.4.1`、`sha2 0.10.9`（上述后三组声明 MIT OR Apache-2.0）；`libloading 0.9.0` 声明 ISC。此为 registry package 自报许可证元数据检查，不等于证明源码逐文件许可或全部条款已履行；锁文件含条件包也不等于每一项都链接进 macOS 二进制。

构建材料应按最终两个 lockfile 收集每个来源包的名称、版本、来源、checksum 和许可证表达式及可用的版权/`LICENSE`/`COPYING`/`NOTICE`，对缺失文本单独列出并核查。条件依赖可保守列入，但不得称其必然实际链接。Core 的部分文本已归档在 `docs/core-001/licenses/`，须与最终锁文件核对。

对准备中的本机材料 `/private/tmp/mediastudio-mac005-licenses` 做独立复核：`rust-lock-inventory.json` 与两份 lock 的 44 个唯一 registry 名称、版本和 checksum 完全一致，清单列出的许可文件全部存在且与本机 cargo registry 对应文件的 SHA-256 一致；含 `unicode-ident` 的 Unicode 文本。同一目录的 ORT LICENSE/ThirdPartyNotices 与上表 wheel 文件散列一致，FFmpeg GPLv2/LGPLv2.1 与 x264 COPYING 与下述候选源码包内文本散列一致。这只是临时材料收集结果，不证明最终包一定包含相同文本，亦不构成所有许可义务已履行。

当前供本机 QA 的候选包包含 FFmpeg GPL/LGPL 文本及 x264 COPYING，但尚未包含 FFmpeg 源码中的 `LICENSE.md` 组件说明；这是分发前需补齐和复核的材料，不影响该设备上的本机开发实验。FFmpeg/x264 源码 archive 保存在 `apps/macos/build/MAC-005/sources/`，应同时保留确切下载地址、散列与构建脚本说明；仅有许可文本不能取代源码及安装构建信息。

## FFmpeg 和 H.264 编码

本机历史验证使用 Homebrew FFmpeg/ffprobe 7.1.1，`-version` 显示 `--enable-shared --enable-version3 --enable-gpl --enable-libx264 --enable-libx265` 等大量开关；`ffmpeg -L` 和 `ffprobe -L` 均自报 GPL version 3 or later。历史原始文件 SHA-256：`ffmpeg` `edab6bcbbd5ee1d34be9dd088b52faf6bc75f1bf4d4693506b748f12d5526e81`，`ffprobe` `283bcd064c6176f2c20a100188a106cc8335c175827e8494073ad9343c10b5d2`。`otool -L` 显示 Homebrew Cellar/opt 绝对路径和大量依赖，复制这两个文件并不能形成独立包；这些哈希不是即将打包的新构建的哈希。

现有 `core/media-ffmpeg/src/accurate.rs` 的精确剪辑与转码明确要求 `libx264`；直接切换到不含 GPL 编码器的 FFmpeg 会使现有功能不可用，不能静默改为其他编码器。[FFmpeg 上游许可说明](https://ffmpeg.org/legal.html)明确指出 libx264 属于 GPL 构建路径。

本机 x264 r3108 Homebrew Formula 声明 `GPL-2.0-only`，但实际安装的 `include/x264.h` 头部声明 GPL v2 或更高版本、另提供商业授权；[FFmpeg 上游说明](https://ffmpeg.org/general.html)也描述 x264 为 GPL v2 or later。不同声明不能由 Formula 或单一头文件独立解决；需要针对**实际使用的具体 x264 源码**、构建配置和各组成文件核对适用授权并处理潜在不一致，再判断与 FFmpeg 所选 GPL 版本的组合。不可把未核清的二进制当作已获分发许可。

准备构建的 FFmpeg 7.1.1 源码 archive 本地 SHA-256 `733984395e0dbbe5c046abda2dc49a5544e7e0e1e2366bba849222ae9e3a03b1`；x264 固定提交 `31e19f92f00c7003fa115047ce50978bc98c3a0d` 的 archive 本地 SHA-256 `d053c9d86988d6bc78237ca5205865c5ddf99c98ef4cd9927eec8f6d388f6dd9`。该 x264 候选源码的 `x264.c`、`common/common.c` 和 `x264.h` 均声明 GPL v2 or later；这是定向抽查，并非所有源码逐项审核或对二进制许可证闭包的最终判断。

新建开发候选 `/private/tmp/mediastudio-mac005-deps/ffmpeg-install/bin/` 的 `ffmpeg -version` 显示 FFmpeg 7.1.1，实际配置为 `--disable-autodetect --disable-doc --disable-debug --disable-ffplay --disable-network --enable-gpl --enable-libx264 --disable-shared --enable-static --pkg-config-flags=--static`（另外指定本地构建输出 prefix），未启用 `--enable-version3`。`ffmpeg -L` 和 `ffprobe -L` 均自报 GPL version 2 or later；签名前二进制 SHA-256 分别为 `be3a1fe05a33973d41589c379932719361f28975d3e3b49d8c530fd3d84b8ca7` / `8bd451e4bd322adc7502f79d479e0015a1c1d699f670c5dc0695c81acb191e25`。`otool -L` 对两者只列 macOS 系统库及框架；FFmpeg `config.mak` 显示链接到单独构建的 x264，x264 配置为 `--enable-static --disable-cli --disable-opencl`。这些结果支持选定的静态构建方式，不证明所有嵌入代码、源文件授权和发布义务已经完全审定。精确源码的提供方式及包内签名后二进制散列仍需确认。

最终候选 FFmpeg 应记录官方来源、精确版本/源码散列、实际 `-buildconf`、`-L`、架构、编译链接方式及每个嵌入或动态依赖来源；打包工件需携带适用的完整许可、版权声明，并准备相应源码、修改及构建/安装信息。对于 GPL 或 LGPL 涉及的源码提供、用户权利、再链接及渠道约束，应根据最终构建与实际交付方式逐项审查。官方许可清单参见 [FFmpeg License](https://ffmpeg.org/doxygen/trunk/md_LICENSE.html)。

## 尚待最终工件复核

本机 QA 选用的 `apps/macos/build/MAC-005/MediaStudio-MAC005.app` 只读审查结果：`codesign --verify --deep --strict` 通过，主应用显示 ad-hoc 签名且无 TeamIdentifier；包内 FFmpeg helper 带 App Sandbox/inherit entitlement，不代表正式签名、公证或发行许可。包内 `Contents/Resources/licenses/` 的 91 个文件与上文准备材料逐文件 SHA-256 一致，无缺失或变化。模型与图目录的 manifest 所列八个子图均与其声明 SHA-256 一致，模型的包内散列仍是固定的 `f27742bb...396ec747`。`otool -L` 显示签名后的 FFmpeg/ffprobe 都仅动态链接 Apple 系统库/框架，无法据此枚举静态链接内容。已签名的包内工件 SHA-256 为：

| 工件 | 签名后的 SHA-256 |
|---|---|
| `Contents/MacOS/ffmpeg` | `f4d051535c9db13a3f6c66a5934d6a265f4bec27c603d74d9844d8106d8c57f3` |
| `Contents/MacOS/ffprobe` | `4d64f123c2eab385dd86aca93cdf88942c8351db4dd26270a026646e0dcbd32e` |
| `Contents/MacOS/media-ai-worker` | `afe29d355861a5a3c0d6f1feae2585df3de56ce9a10bd0d3d9b9ca64073f7b26` |
| `Contents/Frameworks/libonnxruntime.dylib` | `6096002c9a6bc64df7177be9a64b9b2229399810d794d8649afbb596f99f1c9d` |
| `Contents/Frameworks/libmedia_ffi.dylib` | `a0a27f3b8c2878073e286d95cbd6f723101fa89a5c8908401ec682f5315ca786` |

`apps/macos/build/MAC-005/sources/` 中的 FFmpeg/x264 两个源码 archive 散列与上文一致。`reproduction-build-manifest.json` 记录的是另一次从同一源材料执行脚本的构建，未签名产物散列与打包前候选不同；它是可再次构建的证据，**不能证明逐字节可复现，也不能冒充本次包内二进制的直接输入清单**。签名后散列与签名前不同亦不能单凭散列建立两者关系。发布前仍需核对最终构建链、实际动态及静态依赖闭包、源码提供和安装包的正式签名状态。不得提交模型权重、用户音频、外部二进制或签名材料至代码仓库。
