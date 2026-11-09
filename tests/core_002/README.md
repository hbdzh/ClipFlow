# CORE-002 独立外部验收

首批新接口与失败预期在后端实现前固定，后续专项沿用 Integration 冻结语义；只通过公开 C ABI 和 CLI 调用 Core，不导入实现，也不修改 CORE-001 / QA-001 的预期。素材为测试自己生成的 testsrc2 / sine / 文字字幕，位于被忽略的 `build/` 私有目录，测试结束清理，不读取用户媒体。

```sh
python3 -B tests/core_002/run_qa.py --library core/target/debug/libmedia_ffi.dylib --output tests/core_002/evidence/new-local-run.json
```

需已编译的匹配 `libmedia_ffi.dylib`、同目录 `media-cli`、FFmpeg / ffprobe 7，以及 Python 3；当前实际运行平台为 macOS arm64。命令均用参数数组启动。`run_qa.py` 连续运行旧 ABI 兼容 24 项、新契约 4 项、新媒体 15 项，记录库/CLI/测试源码 SHA-256 与执行前后 Core 源码快照；源码变化时拒绝整体 PASS。`--output` 的 stem 决定对应原始 ABI/media 证据名，保留历史轮次。

- `test_contract.py`：默认 mock 兼容、显式启用真实 backend、配置上下界、未知字段、URI/整数范围/策略/fallback 的严格失败及无 ghost job。
- `test_media.py`：实际 probe → Core trim → 独立 ffprobe / 完整解码 / 帧 SHA-256 / 压缩 packet SHA-256 / 每轨 DTS 与共同 PTS/DTS 平移验证，源 SHA-256 必须不变。
- 正常输入含 CFR / B 帧、双 AAC、无音轨、特殊 Unicode 路径、非零起始 PTS；VFR 只独立接收 probe。GOP 对齐检查 [1.1,2.6) → [1,3)，另验文件头、恰好关键帧、EOF，不以 QA-001 固定参考 1.45 秒代替通用 Core 算法。
- 原始 naive `-ss 1.1 -t 1.5` 是真实 FFmpeg 负例：即使可解码，其缺失 B 帧的内容序列仍被拒绝。颜色/rotation、字幕、其他 codec 均检验明确拒绝，避免无提示丢失。
- 真实 CLI 成功为 0，真实失败/取消非 0；旧 mock 的退出约定保持兼容。
- 取消/超时/stdout 超限使用有 PID/阶段标记的自有 Python 故障注入子进程，版本检查可通过，真实作业停在指定失败阶段。一个专项先调用真实 ffprobe/源解码，到 `ffmpeg -c copy` 才创建 partial staging 并挂起，再验证终态前 kill/wait、临时目录清零。它不模拟任何成功媒体结果。

每次临时媒体随测试删除，JSON 保留实际命令、退出码、元数据、请求/状态/结果、输入输出 SHA-256 与物理内容边界。完整结果与未测项见 `docs/core-002/INDEPENDENT_REVIEW.md`。不可将这里的 Core CLI/FFI 验收写成原生 UI、Windows、HarmonyOS、内存无泄漏、主观音画同步或发行许可验收。
