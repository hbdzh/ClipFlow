# CORE-004 独立验收

先固定[接受条件](ACCEPTANCE.md)，再通过实际C ABI/CLI执行。QA不导入生产validator，不修改旧黄金。媒体只用自有帧号、色图和脉冲音调，全部位于忽略的build树；输入、工具、生产/测试源码及输出SHA记录在证据中。

本目录使用项目media-correctness/cross-platform-qa规则。下方是复现入口，命令存在不等于已经通过；实际结论见独立报告。每次使用唯一label，旧输出/失败证据不会覆盖。

```sh
cargo build --locked --offline --manifest-path core/Cargo.toml
python3 -B tests/core_004/precheck.py
python3 -B tests/core_004/test_contract.py --library core/target/debug/libmedia_ffi.dylib --label local-contract
python3 -B tests/core_004/test_media.py --library core/target/debug/libmedia_ffi.dylib --label local-media
python3 -B tests/core_004/test_selected_overlap.py --library core/target/debug/libmedia_ffi.dylib --label local-selected
python3 -B tests/core_004/test_processing_deadline.py --library core/target/debug/libmedia_ffi.dylib --label local-processing
python3 -B tests/core_004/test_cli.py --binary core/target/debug/media-cli --label local-cli
python3 -B tests/core_004/test_quarantine.py --library core/target/debug/libmedia_ffi.dylib --label local-quarantine
python3 -B tests/core_004/test_disk.py --library core/target/debug/libmedia_ffi.dylib --label local-disk
```

- `precheck.py`、`fixtures.py`、`media_verify.py`：独立几何公式、源帧身份、全轨解码、PTS/DTS、Y/U/V及64px色块、源全局音轨索引/语言/声道/带限波形。错帧/保Y错UV负例必须拒绝；预检查器PASS不是Core成功。`gap-fixtures.json`另记录延迟音轨补充素材生成。
- `test_contract.py`：显式开关/期限类型、互斥音轨字段/未知字段/错误profile、33项同步LIMIT_EXCEEDED及无ghost；旧Core不理解新配置的实现前红例保留。
- `test_media.py`：P01/P02横竖/取整/小图/0–2音轨/offset/分数FPS/Unicode；32接收、剩余容量/坏项整批拒绝、submission幂等、FIFO重排、跨engine含legacy单重permit、活动/queued取消、接收后源/目标/同卷父目录竞争、重试新真实位置、OS大小写/Unicode别名、103真实probe验证100终态回收/清除/重启。最大容量不等于32×10分钟最大负载通过。
- `test_selected_overlap.py`：视频0–4秒/部分音轨2–3秒；none或one未选无交叠轨必须真实成功；实际保留无交叠轨仍按旧规则拒绝，6ch/旋转不能借none绕过全源scope。
- `test_processing_deadline.py`：真实工具阶段延迟验证预检+执行共用操作总预算、各操作期限选用、排队/其他批项预检idle不消耗本项处理预算，以及准确Trim短非关键帧与copy严格区分。延迟wrapper转发真实工具，不造媒体或假状态。
- `test_cli.py`：真实转码、特殊路径/源stream index、JSONL控制/幂等/轮询/清除/显式close、超长行不可将尾部当新命令。调度始终由Core执行。
- `test_quarantine.py`：单独子进程中真实chmod自有staging目录为000触发OS清理权限失败，验证CLEANUP_FAILED/全局permit和真实卷/目标ledger保留/跨engine不继续/destroy BUSY；finally恢复权限、手工删除仅自有stage并退出隔离进程。这个手工恢复不冒充Core已恢复或跨重启自动续跑。
- `test_disk.py`：仅在自有256MiB HFS+镜像中操作。先要求宿主≥1GiB余量、挂载点空且非symlink、实际挂载device不同且容量≤270MiB；接收且runtime空间检查已过，在真实FFmpeg写前填镜像至free0及OS ENOSPC，验证真实写失败/已知不足预检/恢复导出并自动卸载。使用本机DiskManagement命令，缺权限时保留BLOCKED，不能填满宿主卷或以mock errno替代。`--prepared-directory`仅适用同目录下已经核实的隔离QA镜像，不接受任意用户卷。

标准输出包含合成源/目标路径仅为本机显式QA诊断；普通Core错误/历史另检查不泄漏完整路径。外部FFmpeg/ffprobe由用户开发环境提供，源码/二进制不vendoring；本轮无新registry依赖。native UI/MAC-003、其他系统/Intel/硬件编码、全负载/长期RSS和主观听看未验证时保持NOT_RUN。
