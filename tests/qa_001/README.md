# QA-001：合成媒体、黄金语义与直接 FFmpeg 参考

本目录是 CORE-002 的输入与参考基准，**不是已经实现的 Core 媒体处理功能**。所有画面来自 lavfi testsrc2、音频来自合成正弦音；不读取、上传或提交用户素材。媒体与 Rust 测试构建产物只放本目录被忽略的 `build/`，不强行加入 Git。

## 复现

从本工作树仓库根目录运行：

```sh
python3 tests/qa_001/run.py
```

该命令依次运行安全负例（正常 Python 与 `-O`）、生成器和独立验证器，保存 suite-run.json 的实际命令、返回码、原始输出及执行前后源码哈希。也可分别执行：

```sh
python3 tests/qa_001/test_safety.py
python3 -O tests/qa_001/test_safety.py
python3 tests/qa_001/generate.py
python3 tests/qa_001/validate.py
```

本次实测依赖：macOS arm64、Python 3.13.3、FFmpeg/ffprobe 7.1.1、Rust/Cargo 1.99.0。FFmpeg 为现有 Homebrew 本地工具，实际配置与 `-L` 文本见 generation.json；启用了 GPL 组件，未拷入仓库或应用包，也没有据此完成分发审查。没有新增 Python/媒体运行时依赖。

`reference-plan.json` 绑定本次 FFmpeg 版本/可执行文件哈希和源样本 SHA，换工具/输入时必须重新审阅计划，不能擅自更新断言使其通过。`--offline --locked` Core 构建要求缓存已存在；该构建的 target 是新建的受控临时目录，完成后清理。

## 样本与预期

黄金语义先定义在 [rules.json](rules.json)，原始 SHA-256 为 `9e387fcba30de2e1cb3e8054a1564c823dc226625489b235d2e835cfdd2a2e06`，生成与验证期间保持未变。每个输入的准确 SHA/大小/生成步骤见 generation.json；实际规范化 ffprobe 信息与帧/时间戳观察见 normalized-golden-info.json。后者是固定本地环境的观测基线，不能反过来代替先定义的期望。

| 样本 ID | 特征与独立检查 |
|---|---|
| cfr_bframes | 160×96、4 秒、30 fps H.264 + AAC；实际 B 图像、1 秒关键帧间隔、display PTS 恒定 1/30 秒、DTS 单调 |
| vfr | H.264、无音轨；选择非均匀输入帧，实际 72 帧，display PTS 间隔为 1/30000 time_base 的 1000/2000 ticks，不仅依据 avg_frame_rate 判断 VFR |
| multi_audio | H.264 + 两条 AAC，语言 eng/zho；两轨分别完整解码后的 SHA 不同 |
| no_audio | 只有视频；验证没有被错误补入音轨 |
| unicode_path | 与 CFR 样本字节一致，路径含中文、空格、分号、`$()`、方括号和单引号；探测/解码/参考复制均以参数数组调用，无 shell |
| truncated | 从自有 CFR 样本保留 32 字节；ffprobe 与实际解码均必须非零拒绝 |

每个有效输入都检查流编码/数量/尺寸/时长、真实帧 PTS、逐流 packet DTS、完整解码及首尾分段解码。B 帧的 packet PTS 可重排，**不要求 decode-order 的 PTS 单调**。AAC priming/末包和容器起始偏移保留在原始数据中；仅比较流终点接近不能证明主观音画同步。

## 流复制边界：原失败必须继续被拒绝

用户请求示例是开始 1.1 秒、时长 1.5 秒。原始直接参考命令 `-ss 1.1 ... -t 1.5 -c copy` 在此固定 B 帧输入上实际得到源帧索引 `30..78,81`，缺少 79/80；不能因为能解码或有文件就判为完整连续片段。

验证器始终执行这条原始命令，并用同一个连续性断言将其作为 **预期的边界校验失败负例** 拒绝，删除其自有临时输出，不开放成功产物。调查的真实命令和完整索引保留在 boundary investigation 与 variant investigation；最终自动负例结果在 normalized-golden-info.json 的 `naive_boundary_negative`。

Integration 根据媒体事实审阅的 [reference-plan.json](reference-plan.json) 单独采用执行时长 `1.45` 秒，并仍记录原请求 `1.1 + 1.5`。这只是绑定固定输入/工具的参考方案，**不是 Core/UI 的自动策略、不是通用裁剪算法，也不是原请求的精确执行**。在 CFR、多音轨和 Unicode 三个样本上，实际连续匹配源帧 `30..78`：

- 首帧源 PTS：1.0 秒，比请求提前 0.1 秒。
- 最末帧源 PTS：2.6 秒；它是该帧开始点，不是片段终点。
- 根据实际 decoded framehash time_base 与最后一帧 duration，源内容 end-exclusive 为 `79/30` 秒（约 2.633333 秒）。
- 输出 MP4 的重置 PTS、流起点、duration 独立记录，不用于反推源落点。

每个输出帧的 SHA 必须唯一匹配源帧，且源索引完整连续；每个压缩 packet payload 的 SHA 必须在对应源流中存在；再检查流数量/编码、完整/首尾实际解码、逐流 DTS 和有限音视频终点偏差。全部验证成功后才将 partial 原子替换为最终参考文件，失败/超时只清理自己创建的 partial，保留源哈希。

## 安全、错误与取消

- 路径门禁使用显式异常，`-O` 不会禁用。拒绝 build 根/任何中间或叶 symlink、树外路径、硬链接输出和特殊文件；generation sidecar 只可指向 rules 对应的固定输入文件名/ID 集，不能重定向到用户素材。
- 媒体生成/参考导出先写受控 partial，验证后替换；证据先写同目录唯一临时文件，再原子替换。15 项真实文件系统负例包括 symlink、hardlink、FIFO、失败清理和源保持，正常及 `-O` 均实测。
- 缺失输出父目录是实际写入错误参考，必须非零退出且无输出。`rules.json` 的零长度、逆序、负数、超时长范围只是给 CORE-002 的错误输入配置；当前 Core 不接受媒体请求，**未把这些配置声称为 Core 范围验证通过**。
- 取消只针对本脚本自己 `Popen` 的实时合成 FFmpeg worker；必须先观察到已处理帧，再发 terminate，超时则 kill 并 wait，最后删除自己的 partial。实际返回码/升级 kill/耗时/清理记录在 normalized-golden-info.json。当前实测使用了 kill 升级，不声称优雅停止或无泄漏。

## 已验证与未验证

本次 13 组输入/参考检查通过，另有 1 项 **Core 媒体 pipeline BLOCKED**：实际构建并调用当前 CLI，probe/trim capability 为 false，两命令返回 unsupported(-8)。这是正确的能力拒绝，不是 Core 媒体功能通过。

尚未验证真实 Rust Core 媒体导出/取消/超时/临时文件管理、Windows/鸿蒙、播放器、真实设备或签名；无 HEVC/WebM/Alpha/HDR/字幕/磁盘不足专项、感知音画同步、长时稳定性或泄漏分析证据。详见 REPORT.md 与 Integration 的 验收边界。
