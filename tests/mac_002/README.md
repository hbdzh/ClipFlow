# MAC-002 独立 Native 验收

使用项目现有 Python、FFmpeg/ffprobe 和构建出的原生 app，不安装第三方依赖。先读取 [固定标准](ACCEPTANCE.md)。测试只创建 `tests/mac_002/build/` 下的自有合成媒体、私有历史和输出；不能指向用户历史文件。

```sh
python3 -B tests/mac_002/precheck.py
python3 -B tests/mac_002/run_native.py --binary apps/macos/build/MediaStudioSpike.app/Contents/MacOS/MediaStudioSpike --label fresh-run
```

`precheck.py` 需要空 fixture 目录；每次 Native `--label` 必须新建，避免覆盖首次失败/输出。macOS AppKit/AVFoundation 需要实际登录桌面访问；sandbox 内若在 NSApplication 注册处 SIGABRT，记录后在允许访问桌面的执行环境运行相同测试。`--only` 可选择单项，`--only final` 重跑新增并发回调及全部媒体/恢复用例，保留已经执行的100素材压力证据。

生产入口 `--workspace-self-test` 直接调用生产 WorkspaceModel/CoreBridge/AVFoundation。QA解析真实状态，不导入生产验证器。快速输出检查源解码帧 MD5、压缩包 SHA连续子序列及共同时间平移；精确输出检查全轨解码、烧录源帧号、Y/U/V各30dB、64px色块误差8、共同音频源映射。噪声相关测量是客观时间映射，不是听感/口型验收。

缺编码器 wrapper 只修改真实 `ffmpeg -encoders` 能力列表；取消 wrapper 先真实执行 FFmpeg 到 staging，再阻塞，独立线程物理观察 staging 后才请求取消/关闭。历史上限测试明确是注入元数据，不代表101次真实导出。原始 stdout 存忽略 build 树，提交 JSON保留其字节数/SHA及结构化完整内容。所有初次失败保留。

[GUI清单](GUI_CHECKLIST.md)由 Integration 在实际窗口独立执行。原生场景 E2E不能代替键盘/拖放/窗口/VoiceOver或主观观看；此目录不宣称其他平台、签名发行、无限资源压力或完整首版通过。

AC16全局关闭期限负例另跑：

```sh
python3 -B tests/mac_002/test_close_deadline.py --label fresh-deadline
python3 -B tests/mac_002/run_native.py --binary apps/macos/build/MediaStudioSpike.app/Contents/MacOS/MediaStudioSpike --label fresh-close-regression --only shutdown-regression
```

`close_delay.c` 只编译到忽略的测试树，不进入App或Core。DYLD interposer等待实际export marker存在后，将一次真实 `ms_cancel_job` 延迟17秒再转发原函数；不生成状态或结果。独立线程在15秒UI期限之后、实际取消转发之前观察真实活PID和非空staging，再检查owner/授权未释放、拒绝复用、同一cleanup task最后确认回收。必须允许此本机测试动态加载，不能把未触发interposer当作期限通过。
