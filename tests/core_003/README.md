# CORE-003 独立验收

先行接受标准见[ACCEPTANCE](ACCEPTANCE.md)，公共请求以`contracts/CORE-003.md`为准。只通过真实CLI/C ABI调用；测试自建的粉噪/帧号/色图位于忽略的build树。故障shim只用于挂起/失败/编码成功后故意改坏音频或色度，不提供成功Core证据，不读取用户媒体。

在根目录实际构建匹配Core后运行（以下是复现入口，不自行声称已经通过）：

```sh
python3 -B tests/core_003/run_qa.py --library core/target/debug/libmedia_ffi.dylib --output tests/core_003/evidence/local-run.json
```

- `precheck.py`：合成全部支持/拒绝素材，独立帧号/1080p逐plane PSNR/64px色块正例及保Y错U/V负例checker自检。正例是checker试验，不是Core成功。
- `test_contract.py`：固定profile/显式开关/期限类型边界、URI/范围/schema/UTF8/长度拒绝与无ghost；默认mock/旧backend继续不声明准确可用。
- `test_media.py`：fraction FPS、B帧、offset、mono/stereo、44.1/48kHz、audio-first、0/1/2轨、709/无标签、Unicode、头/尾、错误拒绝，实际CLI和C ABI；物理源帧号、全轨probe/解码、DTS/presentation PTS、音频波形映射与质量；非空staging/PID观察后cancel/timeout/BUSY-close；成功编码后改坏音频或色度必须失败不发布。
- `run_qa.py`：运行新外部契约与媒体检查；旧ABI/CORE-002整套回归由Integration原样运行归档，避免重复；记录源码前后快照、库/CLI/测试hash和命令，源码变更不接受整体PASS。未改旧测试预期。旧媒体全套回归由Integration另行运行/归档，不用这几个旧契约用例冒充旧媒体全验收。

所有新功能结果必须看实际JSON证据，保留首轮失败。取消故障子进程不是完整真实长片编码性能证明；C ABI active destroy按契约BUSY保留handle，随后cancel/wait/destroy才是此处close流程。主观听感、完整观看、原生GUI/Intel/Windows/HOS、最大时长负载/堆泄漏/正式发行等未运行即明确未验证。
