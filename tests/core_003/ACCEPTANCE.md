# CORE-003 独立验收：实现前固定预期

日期：2026-11-10。基线：`2f26a210d5dee0f15da1c98f85909635d6479f51`。以下是接受标准，不是执行结果；对应 PROD-002 AC05–08/10/15–20，待公共契约冻结后固定外部请求。旧 CORE-001/002/QA-001 的黄金预期不改。

| ID | 独立接受条件 | 初始状态 |
|---|---|---|
| Q01 | default mock仍不可执行真实媒体；旧stream_copy无profile的能力/字段/颜色拒绝保持兼容；新profile必须显式且严格校验 | NOT_RUN |
| Q02 | 新accurate profile及timeout上下界、错误profile/策略组合、未知字段、非法URI/范围/UTF8/过长输入，拒绝后无ghost job | NOT_RUN |
| Q03 | 真实C ABI与CLI精确重编码：非关键帧请求首帧为源PTS>=start的第一帧、末帧PTS<end；24/25/30/60及24000/1001、30000/1001，B帧和+5秒原点，帧序完整 | NOT_RUN |
| Q04 | 0/1/2 AAC mono/stereo轨均保留，输出H.264 yuv420p/AAC和P01语义；每轨解码、packet DTS按解码序/decoded frame PTS按显示序验证，不能要求B帧packet PTS单调 | NOT_RUN |
| Q05 | 输出归零后共同AV映射误差<=max(40ms,一视频帧+一AAC帧)，音频实测波形对齐/尾部无累计漂移；主观听感另报未测，不以包时间替代听感 | NOT_RUN |
| Q06 | 真实1080p 709参考：Y/U/V各plane平均PSNR>=30dB、固定64x64色块U/V均值误差各<=8码值，错误色度/红蓝交换负例必须被checker拒绝；新profile流复制保留709标签与压缩内容 | NOT_RUN |
| Q07 | 超范围/损坏/VFR/HEVC/HDR/Alpha/旋转/非单位SAR/字幕/多视频/不支持音轨等明确拒绝，不静默改策略或丢流 | NOT_RUN |
| Q08 | 源==目标、已有文件/目录/链接、特殊Unicode与shell字符、晚创建目标竞争：源hash与已有内容不变，无shell执行、无半成品发布 | NOT_RUN |
| Q09 | 可用编码器必须实际检查；缺libx264/AAC时available=false且准确请求失败，不退回stream_copy；成功路径绝不使用故障库伪造 | NOT_RUN |
| Q10 | 真实进程/非空staging观察后取消/超时/关闭：确认PID退出与staging清理，5秒正常取消门槛；无法确认清理时保持错误所有权，不报告cancelled/succeeded | NOT_RUN |
| Q11 | 输出校验能拒绝错误帧、色度、流/时间/坏编码产物，失败不提交；格式/probe+完整解码/质量/边界判据分别记录 | NOT_RUN |
| Q12 | Rust fmt/clippy/test、旧ABI/新ABI、源码/构建/fixture/产物SHA、强制FFmpeg argv/FFI所有权/取消/覆盖复审；只声明macOS arm64本机证据，Intel/Windows/HOS/UI/发行未测 | NOT_RUN |

输入只用自有合成测试图案/音调，不复制用户媒体。命令参数数组、临时目录在本任务忽略的build树，JSON保留实际argv/返回码与hash；二进制/媒体不入库。任何失败保留首轮证据，不改门槛迎合实现。
