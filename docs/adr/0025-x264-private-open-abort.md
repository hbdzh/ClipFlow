# ADR-0025：x264 未发布编码器的晚期中止析构

日期：2026-11-16。状态：Proposed，实际采纳需独立 Integration 的具体配方准入与工件验收收据。本机macOS arm64受控开发候选，非公开发行批准。前次ADR-0024与Release-v3/85份证据保持冻结。

## 责任与决定

| 层 | 唯一责任/变化 |
| --- | --- |
| 原生UI / Rust Core | 继续现有请求、队列、文件原子提交、取消与错误语义；本轮无公共JSON、八函数C ABI或产品编码预设变化 |
| FFmpeg wrapper | 保留7.1.5、静态GPL/libx264及原选项；不在UI追加业务或任意x264参数 |
| x264构造 | 栈上阶段计数/位图记录成功获得的资源；未发布encoder的父级失败按所有权回滚，不调用完整encoder_close |
| Integration / QA | 先审实际patch/前后像/配方与披露，再运行正常有效媒体、资源和真实静音sandbox GUI；失败分支保持STATIC_ONLY |

保留原x264归档 `31e19f92f00c7003fa115047ce50978bc98c3a0d`，严格按原参数补丁、ADR0024组合构造补丁、本次late-abort补丁顺序构建 `+mediastudio-open-cleanup-3`。不叠加三个proposal副本或早期prefix草案。记录最后8份C/H文件的精确摘要、原库/签名helper和对应GPL材料；新Release-v4，绝不覆盖v3。

父级只使用每context成功位（mutex、condition、唯一fdec、bitstream、NAL、cache、thread缓冲），以及实际malloc/进入context数量。复制的context可能包含借用指针，不能按nonNULL释放；失败遍历也不能使用尚未验证的输入线程数量。sliced children逆序释放，root0始终保留到借用者全部结束。

lookahead成功后建立的空队列/工作线程先停止并join；其complete destructor只在成功构造且encoder尚未发布、没有任何帧/作业提交的边界调用。该destructor释放额外context后，父级移除重复容器责任。两个成功idle pool随后停止/join，再释放RC/context/frame/共享前缀。

帧构造在自身失败时清理唯一PREALLOC base和已成功mutex。宏块thread缓冲改成局部事务：raw border/deblock/scratch全部成功后再发布到context，失败不修改旧借用输出；父级仅对成功构造调用完整thread_free。cache只有PREALLOC base为所有者，子视图不可单独释放。

新增内部、8/10位命名空间 `int x264_ratecontrol_abort_open(h)`，不是公共x264/Rust FFI ABI。只支持无stat-read/write、无自定义i_zones/psz_zones的RC构造路径；清连续RC数组、pred/pred_b和各encoder thread别名，无统计文件提交。返回0表示无RC/回收，-1表示不支持且RC保持原状。RCnew的受支持失败自行回滚；parent在RC成功后的更晚失败调用。未发布、所有使用者已停时，RC不支持配置仍保留其残余资源缺口，独立context清理不伪装为RC也已修。

## 边界与回滚

晚期parent中止仅在HAVE_OPENCL=0启用；HAVE_OPENCL=1即使动态加载失败仍保留v3早期边界，不能猜测平台资源就绪。当前本机配置OpenCL禁用、POSIX、8/10位和interlaced编译。其他平台/Intel/启用OpenCL构建均NOT_TESTED。

统计文件/两遍/custom zones/MB-tree rescale的通用RC回滚仍需局部临时缓冲、zone参数借用/所有者、文件句柄和无rename的abort契约，技术未完；不是CNA澄清可以自动修复。没有扩大公开可输入参数，也没有实现新编码算法。

若正常成功路径出现回归，保留失败收据并继续使用原v3独立候选；不覆盖历史或降低oracle。可独立修补的所有权事务优先，避免把完整成功close接到partial状态导致重复释放、无效锁销毁或并发悬挂。

## 验证与非结论

静态案例至少包括：成功open/close；分配/锁失败前后各阶段；成功lookahead后父级中止；受支持RC失败/成功后中止；不支持RC与OpenCL退路。静态推导不冒称动态触发。禁止漏洞样本、恶意媒体、漏洞复现、OOM/线程申请故障注入；不打开VoiceOver或播放/试听。普通有效配置的基线/新库码流和全解码比较，包含frame/sliced线程、progressive/interlaced、8/10位与420/444的受支持组合；既有239媒体、队列/文件、20轮资源及真实签名包GUI另行补证。

ORT、worker、模型/图、SDK最低支持与sandbox权限不变，通过原字节绑定保留v3/v2证据，不重复写成新AI验收。无需重跑未变Rust源全部检查，ABI/媒体实际路径单列。源码补充包记录三份patch、原归档、配方、许可/修改说明，不等于公开分发履约完成。

CNA与NVD本轮官方元数据刷新仍无精确受影响/修复提交；AAC/RCE代码映射与issue/MR维护者裁定继续外部阻塞。完整CVE保持UNDER_INVESTIGATION、公开发行NO_GO、整个QA002和其他MUST不因此接收。

证据目录：`docs/qa-002/evidence/x264-late-abort-20261116/`；逐资源依据见parent/macroblock/ratecontrol审查和独立Integration收据。
