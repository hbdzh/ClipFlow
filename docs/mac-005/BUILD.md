# MAC-005：macOS arm64 本机开发包构建

此说明用于本机受控开发，不是公开分发许可。实际开发 App 使用 ad-hoc 签名、未公证；完整许可和待决事项见本任务许可审查。包与模型、上游归档、运行时均不提交 Git。下述开发构建使用 Python 进行构建与模型准备；应用和原生 worker 运行时不调用 Python。

> 2026-11-15 维护更新：当前媒体构建脚本锁定 FFmpeg 7.1.5，见[ADR-0022](../adr/0022-ffmpeg-security-maintenance.md)与第六项实际工件/回归报告。旧7.1.1证据保留为历史，不能用当前脚本搭配旧archive。公开分发仍NO_GO。

> 同日依赖闭包更新：当前 x264 配方强制应用参数清理补丁；ORT 增加同版本 arm64 CPU 受控源码构建，见 [ADR-0023](../adr/0023-exact-runtime-closure-and-x264-cleanup.md) 与新候选实际结果。实际接收以该报告和新包摘要为准，不把旧 wheel 的未知闭包改写成已知。

## 固定输入和准备

以下源文件保存在本机忽略目录 `apps/macos/build/MAC-005/sources/`，也可从上游独立获取后按摘要核对：

| 输入 | 上游 | SHA-256 |
| --- | --- | --- |
| FFmpeg 7.1.5 | [ffmpeg-7.1.5.tar.xz](https://ffmpeg.org/releases/ffmpeg-7.1.5.tar.xz) | `de668509caf9e35e3cd162473441fdb29538c6d96ed080292b3cf9e6fc5d558f` |
| x264 r3108 | [固定提交 31e19f92 的源码包](https://code.videolan.org/videolan/x264/-/archive/31e19f92f00c7003fa115047ce50978bc98c3a0d/x264-31e19f92f00c7003fa115047ce50978bc98c3a0d.tar.gz) | `d053c9d86988d6bc78237ca5205865c5ddf99c98ef4cd9927eec8f6d388f6dd9` |
| UMX-HQ 转换模型 | [固定提交 e06097d3](https://huggingface.co/MixDirective/open-unmix-umxhq-vocals-onnx/tree/e06097d3193a4c3168b5ddd2a479a680b1176c16) | `f27742bb52b24cb039614dc76d0764e279df3eb2a9d6daa82c6daee3369ec747` |
| ONNX Runtime 1.22.1 macOS wheel（历史 Release-v1） | [官方版本文件](https://pypi.org/project/onnxruntime/1.22.1/#files) | `d29c7d87b6cbed8fecfd09dca471832384d12a69e1ab873e5effbb94adc3e966` |
| ONNX Runtime 1.22.1 受控源码 | [固定提交 89746dc 的源码包](https://codeload.github.com/microsoft/onnxruntime/tar.gz/89746dc19a0a1ae59ebf4b16df9acab8f99f3925) | `38f8ffa28a5ea9040431816c6204e6fc36d1b8eab43b33dbee92fceeb181d151` |

获取来源和摘要不代表已完成上游签名校验或再分发授权。历史 Release-v1 从 wheel 提取 dylib，保留来源及静态闭包未知的原结论。受控新库另以源码、实际链接对象和头文件绑定，不能混用两种来源收据。固定模型和八个子图由 worker 检查 SHA。构建使用已锁定的 Cargo 依赖，需先准备对应本机 Cargo 缓存和 Xcode SDK；MAC-005 验证目标是 arm64 macOS 27.0，不是对更低版本或 Intel 的支持声明。

## 构建媒体工具

```sh
python3 apps/macos/build_mac005_ffmpeg.py \
  --ffmpeg-archive apps/macos/build/MAC-005/sources/ffmpeg-7.1.5.tar.xz \
  --x264-archive apps/macos/build/MAC-005/sources/x264-31e19f92.tar.gz \
  --work-dir /private/tmp/mediastudio-mac005-715-rebuild --jobs 4
```

工作目录必须不存在。脚本先检查两个源归档的固定 SHA，再以 `x264 ./configure --enable-static --disable-cli --disable-opencl` 构建静态库；FFmpeg 使用 `--disable-autodetect --disable-doc --disable-debug --disable-ffplay --disable-network --enable-gpl --enable-libx264 --disable-shared --enable-static --pkg-config-flags=--static`，另设置本次本地 `--prefix` 与 x264 的 `PKG_CONFIG_PATH`。不启用 `--enable-version3`。输出 `ffmpeg-install/bin/{ffmpeg,ffprobe}`、`x264-install/lib/libx264.a` 和含各 SHA/参数的 `build-manifest.json`。

脚本在 configure 前强制核对并应用 `apps/macos/patches/x264-31e19f92-param-cleanup.patch`，SHA256 `736368e6444002409a8cc88017c79776c107ee59a642db2b695479294a21977d`。衍生身份为 `31e19f92…+mediastudio-param-cleanup-1`，manifest 记录修补前后 encoder 摘要。没有无补丁回退；不能声称这是上游已合并版本或完整 CVE 影响已消除。

历史7.1.1曾以两份全新目录重复构建，因嵌入prefix而SHA不同；不把该历史实验继承为7.1.5重复构建。本次7.1.5已完成一份全新构建，源archive/脚本/未签名输入到签名工件有直接绑定，**未证明逐字节可复现**。当前候选FFmpeg动态依赖仅为Apple系统库。`--disable-network` 保留本机 `file` 与 `pipe` 协议；实测能探测本地 WAV 和 H.264/AAC MP4，并解码 MP4 首帧。此检查不表示网络媒体或全部格式已验证。该构建含 GPL x264，不能作为已批准的闭源分发组件。

## 受控 ORT 构建

`apps/macos/build_qa002_ort.py` 固定源码、依赖下载摘要及 arm64 CPU full-operator/shared 配置，不使用 vcpkg 私有缓存、Python、训练、CoreML、GPU 或 LTO。构建工具在忽略目录私有 venv 内固定；版本、wheel SHA、编译器和 SDK 见本轮 `ort-controlled-toolchain.json`，不是产品运行依赖。先 `prepare` 固定源，再 `configure`；经独立 Integration 审核具体 recipe SHA 后才运行：

```sh
python3 apps/macos/build_qa002_ort.py build \
  --reviewed-recipe-sha256 INDEPENDENTLY_REVIEWED_RECIPE_SHA256
python3 apps/macos/build_qa002_ort_bitcast.py \
  --reviewed-recipe-sha256 INDEPENDENTLY_REVIEWED_DERIVATIVE_RECIPE_SHA256
python3 apps/macos/audit_qa002_ort.py
python3 apps/macos/collect_controlled_ort_notices.py
```

这些工具绑定本轮固定 QA 路径及已封存的原试建 manifest/语义基线；ORT报告记录实际运行、原试建封存和完整摘要。不能覆盖该历史路径后仍使用其旧收据；新的复建必须使用独立目录并重新审核绑定。局部补丁只把 `math_cpu.cc` 的两处位转换改为 `std::bit_cast`，上游实际已使用 C++20，全部编译命令保持一致；没有采用额外 hook。官方 CMake 对依赖的自动补丁也在清单内逐项披露，不把它们伪称未修改原归档。

实际采用还需要链接对象、所用头文件、内嵌来源、生成代码、每份许可及签名前后工件的独立闭包审核。仅预取依赖或成功编译不能记完整清单。许可 collector 的 `--ort-static-notices` 输入必须包含实际组件 `inventory.json`，文件集合/大小/摘要均校验，拒绝截断版权材料；`--x264-build-manifest` 输入同时核对实际 encoder 和补丁并打包披露。原始宽范围 ThirdPartyNotices 继续作为额外材料保留。

## 构建和组装应用

1. 用 `cargo build --release --offline --locked --manifest-path core/native-ai-worker/Cargo.toml` 构建原生 worker。使用其 [README](../../core/native-ai-worker/README.md) 中的固定版本 ONNX 工具，从固定模型在离线开发环境生成八个子图；不要把模型或子图提交 Git。
2. `python3 apps/macos/build_mac005_base.py --output /ABS/NEW-BASE.app --work-dir /ABS/NEW-BUILD` 从当前源码构建新基础 App；两个目标路径都必须不存在，不会覆盖 MAC-003 build/evidence。
3. 准备 ORT wheel 自带的许可/NOTICE、模型 MIT、FFmpeg的LICENSE.md/GPL/LGPL/IJG声明与对应源码头部、x264许可证及锁定Rust crate许可材料。使用 `python3 apps/macos/collect_development_licenses.py --help` 查看各源路径参数；输出到新的许可材料目录。列表和文件收集本身不等于发行许可已完结。
4. 用以下方式将组件复制入新的应用包并 ad-hoc 签名；所有大写参数均替换为已准备的绝对路径。原应用不会被覆盖。

```sh
python3 apps/macos/assemble_native_ai.py \
  --base-app /ABS/NEW-BASE.app --output /ABS/MediaStudio-MAC005.app \
  --worker /ABS/mediastudio-native-ai-worker --runtime /ABS/libonnxruntime.1.22.1.dylib \
  --graphs /ABS/graphs --model /ABS/model.onnx \
  --ffmpeg /ABS/ffmpeg-install/bin/ffmpeg --ffprobe /ABS/ffmpeg-install/bin/ffprobe \
  --licenses /ABS/licenses --sandbox
```

应用内路径为 `Contents/MacOS/{MediaStudio,media-ai-worker,ffmpeg,ffprobe}`、`Contents/Frameworks/{libmedia_ffi.dylib,libonnxruntime.dylib}`、`Contents/Resources/ai/{model.onnx,graphs}` 以及 `Contents/Resources/licenses`。`--sandbox` 签给应用选择文件读写和 app-scoped bookmark 权限，helper 签 sandbox/inherit；脚本执行 `codesign --verify --deep --strict`。如任一步失败，清理本次新建输出后重新执行；不要覆盖已有用户应用。

## 本机使用与移除

开发验证时可将 App 复制到新建的测试安装目录，从该副本启动、选择音频并授权目标文件夹，验证剪辑或分离及导出；不得把启动成功视为所有格式通过。卸载该开发副本可将其移至废纸篓；应用数据、书签和用户导出文件不随此操作自动清除。正式安装、升级、卸载、系统批准、正式证书和公证的结果，以实际验收记录为准。
