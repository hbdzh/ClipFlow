# ADR-0023：受控 ORT 静态闭包与 x264 初始化失败清理

日期：2026-11-15。状态：Accepted，仅批准本机受控开发候选；独立封版审核与工件复审已完成，实际运行见本轮验收。仅针对 macOS arm64 本机开发候选；不授权公开分发。

## 问题与决定

Release-v1 使用的官方 ORT 1.22.1 universal2 wheel 具有可核对的来源、摘要和许可，但未取得绑定该二进制的实际静态链接及头文件依赖清单。源码默认依赖列表不能替代此清单。决定保留旧包与旧结论，另行构建同版本、固定提交 `89746dc19a0a1ae59ebf4b16df9acab8f99f3925` 的原生 arm64 CPU shared runtime。官方源码归档 SHA256 为 `38f8ffa28a5ea9040431816c6204e6fc36d1b8eab43b33dbee92fceeb181d151`，其下载来源和实际配方另行绑定到构建收据。

新构建不启用 Python、训练、CoreML、GPU 或 vcpkg 私有缓存；保留完整 CPU 算子、版本 1.22.1、worker 使用的 API21 和 macOS27 部署要求。关闭 LTO 以保留可审计的对象归属。固定构建工具与每个实际下载源，归档 CMakeCache、编译命令、链接命令、link map、Ninja 依赖结果，区分实际静态对象、实际使用的头文件/生成代码、构建工具、未链接候选与系统动态库。只有实际产物的闭包通过审核后，才替换新候选中的 dylib；不追认旧 wheel 已有精确清单。

内嵌审查发现 `math_cpu.cc` 的 BitCast 模板明确注明来自 cppreference，而该站当前说明为 CC-BY-SA3.0 或 GFDL，未取得代码例外。不把整文件 Apache 或 ORT MIT 自动覆盖这段来源。拟用 C++20 标准库 `std::bit_cast` 替代该模板及两处调用；上游本次实际构建已使用 C++20，无需 hook 或编译标准/选项变化，不改转换公式、算子、模型、ABI 或部署目标。补丁、文件差异、全部编译命令与历史一致及全 half 位型/浮点边界检查必须独立审核；构建标识记为本地衍生版本，不能冒称原版逐字节构建。首次未修补试建和审计保留历史。早期“其他单元为 C++17”的假设被 guard 在重建前拦截，以实际 compile_commands 纠正，未将该假设用作工件通过证据。该决定也不意味着仅凭代码短就可忽略来源义务。

x264 固定提交 `31e19f92f00c7003fa115047ce50978bc98c3a0d` 的编码器初始化失败路径没有释放内部参数副本。FFmpeg wrapper 清理自己的参数无法回收已经返回 NULL 的编码器内部副本。采用上游未合并 MR183 中带空指针守卫的最小参数清理修补，作为明确标识的本地衍生版本；补丁文件、原始归档、应用后源码和新 helper 摘要均绑定到收据。此决定不声称 MR 已获上游合并、不声称覆盖全部初始化资源，也不把公开记录中未经证实的 AAC/RCE 描述当成已复现事实。

## 所有权与兼容性

| 层 | 本次责任与变化 |
| --- | --- |
| Swift 原生 UI | 不改界面、任务语义或播放行为；使用新包内组件 |
| Rust Core / C ABI | 不改业务决策、公共类型、8 个 ABI 入口或固定编码参数 |
| native worker | 保留现有签名以外的字节、固定模型与图摘要、CPU/API21 路径 |
| 构建与平台适配 | 审核 ORT 固定源码闭包、x264 本地补丁、许可材料及签名前后对应关系 |

仅 macOS arm64 验证。新 ORT 无 Intel slice 和 CoreML provider；本产品当前受验路径为原生 arm64 CPU，不扩展为 Windows、鸿蒙、Intel 或 GPU 能力。若未来启用这些能力，重新建立依赖与运行验收。

## 工件、许可与回滚

不覆盖 `MediaStudio-QA002-Release-v1.app` 或其原始证据。新建 Release-v2，签名前后摘要、ad-hoc 签名命令、所有包内文件清单和原包不变证据必须保留。ORT 精确闭包附实际所用依赖的许可；旧大范围 ThirdPartyNotices 可保留作额外材料，但不能代替精确映射。FFmpeg/x264 仍为 GPL 静态 helper；保留补丁、完整对应源码、构建脚本及许可。子进程隔离不产生 GPL 豁免。

公开分发继续 NO_GO：对应源码提供方式、GPL/专利/渠道、真实签名与公证、其他 QA 和真人听感仍各自验收。若新组件回归，保留失败证据并修复；旧包仅作为不变比较基线，不把回退到已确认缺陷组件写成安全通过。

## 验证

独立 Integration 先审具体配方/补丁，再审实际构建结果和闭包。静态验证架构、API、动态依赖、部署目标、许可证与包摘要。使用正常有效媒体验证流复制、精确导出及媒体矩阵；使用固定模型验证真实 CPU、数值质量、双 stem 原子失败/取消/清理、资源与实际 sandbox GUI 分离/导出/重启恢复。失败例包括非法参数拒绝和双 stem 第二输出失败，保持源文件与成品安全。资源结果分别记录旧基线与新测量，不能把旧组件运行直接冒充新组件证明。

全程静音，不打开 VoiceOver，不调用播放器或旧 `--ai-self-test`，不下载或执行漏洞样本、不构造漏洞复现。有限正常回归只能证明已执行路径；公开漏洞描述中无源码支持的更广影响及其他初始化资源风险必须明确保留。

## 依据

- 独立验收准则
- [ORT 官方构建文档](https://onnxruntime.ai/docs/build/inferencing.html)
- [固定 ORT 源码](https://github.com/microsoft/onnxruntime/tree/89746dc19a0a1ae59ebf4b16df9acab8f99f3925)
- [cppreference 官方版权说明](https://cppreference.com/Cppreference%3ACopyrights)
- [x264 上游问题75](https://code.videolan.org/videolan/x264/-/issues/75)
- [x264 上游 MR183](https://code.videolan.org/videolan/x264/-/merge_requests/183)
- [CVE 官方记录](https://www.cve.org/CVERecord?id=CVE-2025-25467)
