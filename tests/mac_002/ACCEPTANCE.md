# MAC-002 独立验收：实现前固定接受条件

日期：2026-11-10。基线 `e377800b85b43fce9a8b2379f804f68478aef239`。范围是本机macOS arm64完整剪辑工作区及CORE-003 profile接入；不是转码/AI、CORE-004批量队列、其他平台或正式分发验收。全部初始状态 **NOT_RUN**，不能以编写本表代替执行。

| ID | 固定接受条件 | 初始状态 |
|---|---|---|
| Q01 | 实际导入多文件、同一规范文件身份去重、最多100素材；101项/容量超限整批拒绝无半导入，取消面板不丢原工作区 | NOT_RUN |
| Q02 | A/B素材各自持有选区与明确模式；切换后恢复各自范围，元信息/预览/错误不串素材 | NOT_RUN |
| Q03 | 规范化预览/seek和源内容PTS映射正确，含+5s起点；快速切换不让旧异步回调/旧帧覆盖新素材；预览失效与Core导出能力分开 | NOT_RUN |
| Q04 | 快速copy-v1及精确P01都真实调用Core；请求/实际范围、lossy/重编码标志与模式一致；精确源帧PTS>=start且<end，流复制关键帧包围，不静默切换 | NOT_RUN |
| Q05 | 活动A导出期间切换到B/改B范围或模式，不改变已提交A source/range/mode/destination；不额外提交B或自造队列 | NOT_RUN |
| Q06 | 列表只记录真实Core job_id/状态，任务结果正确关联源/模式/目标；未知进度不造百分比，失败/取消不显示成功结果；本轮最多一活动导出 | NOT_RUN |
| Q07 | 损坏/不支持输入、缺组件/编码器、已有目标/非法范围后，可同会话选择有效素材或新目标恢复；不覆盖源/已有结果，无假ready/假成功 | NOT_RUN |
| Q08 | 实际观察进程及非空staging后取消/超时/关闭：PID退出、staging清零后才确认；BUSY不丢owner，清理未确认不释放在用授权或报告cancelled | NOT_RUN |
| Q09 | 最近摘要本地有界100条、可清除；隔离文件/重启后仅历史，不续跑/复用native handle或旧job_id，中断项不谎称成功；不把bookmarkbytes/完整路径写普通日志 | NOT_RUN |
| Q10 | ⌘O/⌘Return/Space/Tab/方向键、拖放、原生菜单、深浅色、1200×800及1600×1000点窗口、VoiceOver可读可操作；仅实际GUI证据可标DEVICE_VERIFIED | NOT_RUN |
| Q11 | Swift actor隔离、阻塞FFI在后台、buffer原ptr/len一次释放含错误/解码失败、scope granted/stops配对；真实MainActor heartbeat和shutdown诊断，不把有限循环当无泄漏证明 | NOT_RUN |
| Q12 | 旧mock/--media-self-test真实回归预期保持；准确result可按新契约严格解码，缺共同必需校验字段不得当通过 | NOT_RUN |
| Q13 | 来源/目标特殊Unicode、空格、引号/$()/分号按数据传递，不拼shell；源SHA保持，新目标无覆盖，结果移动/权限失效可解释重授权/重选 | NOT_RUN |
| Q14 | 新Core/旧请求兼容，Core/contracts/依赖/SDK最低版本无单端修改；源/测试/构建/输入/输出hash可追溯，执行前后源码/二进制一致 | NOT_RUN |

## 物理媒体判据

新准确结果使用独立probe/全轨解码、完整源烧录帧号序列和显示PTS/packet DTS分序、音轨布局与共同音频时间映射；保持PROD-002/CORE-003的阈值。1080p709测试Y/U/V平均PSNR各≥30dB、固定64×64色块U/V均值误差≤8；保Y错色负例须被checker拒绝。快速copy需完整源帧hash/压缩packet与关键帧边界。预览内容定位不等于完整颜色/Alpha保真，客观音频映射不等于听感或口型主观通过。

## 驱动与分级

生产app的新 `--workspace-self-test --scenario /absolute/file.json` 调用同一WorkspaceModel/CoreBridge/PreviewController，schema_version=1，隔离history_path及真实动作列表。QA不实现假工作区替代；step只观察真实状态/trace/diagnostics。CLI self-test提供E2E_TESTED生产Model/原生框架证据，不能自动替代真实窗口输入/VoiceOver/拖放。Integration另做CUA走查；本QA不与其争用桌面。

所有素材为自有合成图案/音调，位于忽略build树，二进制/媒体/图片不入库。首次失败、原始命令和原hash保留；不为实现错误降低阈值或修改旧黄金。
