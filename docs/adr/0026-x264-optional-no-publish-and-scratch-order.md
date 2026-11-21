# ADR-0026：可选RC无提交析构、OpenCL边界与切片scratch顺序

日期2026-11-16。Proposed，具体采纳由独立Integration配方准入、工件/QA收据记录；仅本机arm64开发，不批准发行。保留v4/106封版材料和原受控源码，构建全新v5。

## 责任、版本与接口

| 层 | 责任与变化 |
| --- | --- |
| Native/Core | 既有输入、固定8位420预设、队列、取消、原子提交；生产Swift/Rust与公共JSON/八函数ABI不变 |
| FFmpeg | 仍7.1.5，静态GPL/libx264、无网络/ffplay；不开放任意x264参数或产品OpenCL |
| x264 | 原31e19f92，依次参数、组合构造、late-abort、本次optional-cleanup四patch；衍生open-cleanup-4；最后10份C/H按阶段锁定 |
| QA/Integration | 独立审所有者/线程/文件政策，先准入后新目录构建；普通有效重复/可选路径和真实GUI分级，失败分支不冒称动态测试 |

本次改变RC私有struct内部stride（增加生成zones输入数组owner），并同步内部OpenCL initializer的out-flag参数与唯一调用方；所有私有对象/模板重编译，公共x264 opaque API和Rust FFI不变。没有新增依赖/许可证/SDK最低版本。ORT、worker、模型/图与sandbox entitlement原字节绑定，不写作新AI验收。

## RC无提交所有权

stats_buf在new顶层登记，所有错误（含CMP宏）经统一fail；RC array为零初始化owner，关闭已开stat/mbtree句柄、清文件名、entry/predictors/MBtree buffers与库生成zones，不调用成功delete的rename，也不按路径名unlink。init_pass2局部qscale/blurred数组分清借用别名并回滚。

parse_zone/parse_zones事务式构造：只有完整图才发布，失败恢复原param count/pointer；生成文本数组单独owner，正常成功delete原caller callback块逐字保持，仅补生成数组释放。abort清default/字符串生成的库参数，不回调/释放外部caller参数或其arena；调用方失败open后的转移及重复地址回调次数没有明确上游合同，单列保留。临时文件先前是否存在/是否被竞争无法由确定后缀证明，故关闭并保留，不假称安全删除/覆盖政策已修。

## 切片scratch顺序

普通固定raw输入中，旧10位slice420/444/隔行及8位C hpel路径重复差异，与源码的root scratch复用吻合：slice stage1广播后还在hpel，主线程同步lookahead的MB-tree已可使用同一scratch，原完整wait位于其后。将既有sliced wait移动到i_frame++/同步lookahead之前，保留原错误处理、不关MBtree、不改量化/编码公式或预设。非sliced不变；可能减少sliced的计算重叠，不能宣称无吞吐代价或性能收益。

修补后必须以相同正常输入/参数和实际模式验证重复性、完整解码，保留旧差异与原严格断言。不是以“基线不稳定”豁免新包，也不把此正确性缺口映射成已证实CVE攻击链。新旧原六配置/239黄金断言继续保留。

## OpenCL的可修范围与未决边界

增加core1.0 Unmap函数加载/表项；正常完整删除先在原in-order queue入队unmap，再Finish，再原释放顺序；driver异常仍best-effort，不能声称全部错误恢复。parent不再仅因HAVE_OPENCL就跳过CPU回滚：只有首次clCreateContext尝试才立边界（失败创建也可能异步通知），此前可清CPU/context/frame与最后的函数表/library。实际创建后的完整父级中止仍不启用。

Khronos说明context通知可异步，3.0 destructor callback才给明确的通知user_data回收机制；本机系统1.2且无该符号。不得用NULL context或某次release返回推断所有通知已结束，也不取消原错误通知或强加新最低平台。未创建context后的失败静态路径、活跃context/driver error/full callback lifetime分开记录。

产品仍--disable-opencl。若可用，以独立HAVE_OPENCL=1测试库做正常真实GPU构造/编码/关闭、实际启用状态与map/unmap配对观察；不能用查询到GPU、编译或CPU fallback写成GPU通过。默认工具sandbox的设备查询-30与普通只读查询GPU1均保留。OpenCL编译缓存是已有独立副作用，不删除未知缓存/日志，不承诺事务式回滚这些文件。

## 验证、回滚与非结论

至少静态覆盖：成功构造/关闭；各临时所有者的部分构造回滚；成功RC后的正常no-commit disposal；caller zone保留；OpenCL未创建context与已尝试后的退路；sliced前帧worker与lookahead顺序。禁止漏洞样本/复现、恶意输入及申请/线程故障注入。普通完整对象的新abort可以直接单测：真实namespace open、sync0/no frame/no activeRC user，先验no-rename/FD关闭/库别名NULL与caller存活，再重建nostats RC供正常完整close；不得用悬空FILE*或伪造句柄测试。

独立harness与可选stats/two-pass/zones/MBtree正常输入先源审，再对配对头/库运行。新helper要完成变更相关239媒体、队列/文件、20轮原资源门槛、实际静音sandbox GUI和独立解码。默认产品不新增未请求行为；失败/性能或模式回归保留原证据并修复，旧v4不覆盖、不降oracle。

CNA受影响/正式修复范围、AAC/RCE代码依据及issue/MR裁定继续独立外部阻塞。调用方callback转移/alias次数、临时文件exclusive identity、安全active-OpenCL callback平台中止等明确保留，不将它们和已修库owner混为“全部还没修”。完整CVE仍UNDER_INVESTIGATION、公开发行NO_GO、其他平台/QA MUST不继承本机结果。全程无VoiceOver/播放/试听，不发信或提交issue，不使用真签名材料。

证据：`docs/qa-002/evidence/x264-optional-abort-20261116/`。
