# 画面变换开发契约草案（待 Integration 审核）

仅受控 macOS Core 开发验证，**不是发行或跨平台验收**。C ABI 签名、schema_version 与旧请求不变。`media_backend.video_transform_enabled` 是可选严格布尔，默认 false；true 必须同时启用 core003_enabled 与 core004_enabled。未开启时非空变换请求在打开源文件前返回 UNSUPPORTED_OPERATION，不降级、重试或忽略字段。启用后能力 `features.video_transform_v1` 在编码器可用时为 available。旧 Core 应拒绝未知字段，客户端不得删字段重试。

仅 `operation=transcode`、`profile=mp4_h264_standard_v1` 支持 `video_transform`；720p、流复制、精确剪辑不支持变换。转码明确有损，不会自动改变用户策略。省略字段保持旧行为；对象四个字段均必填，不能出现未知字段：

```json
{"rotation_degrees":90,"scale_percent":150,"offset_x":20,"offset_y":-10}
```

旋转枚举 0/90/180/270，正角度顺时针；scale 是整数 100…200；偏移为 -1920…1920 的偶数像素，正 X 向右、正 Y 向下。先执行标准预设几何（当前源尺寸偶数），再旋转，再以旋转后画布中心为基准等比例放大，再平移。90/270 时输出画布宽高交换；超出画布裁剪，空白补黑；画面完全移出画布则预检返回 INVALID_INPUT；放大后尺寸向上取偶数，输出 SAR 1:1、yuv420p。仅支持既有 CORE-003 输入范围；源文件旋转元数据仍受原规则限制，`-noautorotate` 保持。输入指令不接受任意 FFmpeg 参数或滤镜文本。

内部构造值也会重新检查范围。计算放大后画布宽高 `sw,sh` 和旋转后画布 `w,h`；临时 pad 宽高分别为 `max(sw,w)+2*abs(x)+4` 与 `max(sh,h)+2*abs(y)+4`，以 checked 运算检查 `pad_w*pad_h*16 <= 128 MiB`，超限返回 RESOURCE_LIMIT；这是估计值并非进程 RSS 保证。滤镜在 yuv444p 中居中 pad，crop 原旋转后画布大小并按偏移调整裁切位置，最后转回 yuv420p。处理复用既有期限、取消、身份检查、独占暂存、结果探测与完整解码；输出质量与同变换的完整源帧比较，不能替代独立图样检查和主观质量验收。

成功结果的 `transcode_plan.video_transform` 与冻结请求一致；`visible_content_rect` 是输出左上角起算像素 `{x,y,width,height}`，表示缩放后画面与输出画布的几何交集，而非检测源图像是否实际黑色。`content_width/height` 等于该交集的宽高，`padding_right/bottom` 为交集右侧/底侧空白；变换可能存在左侧/顶部空白，不能沿用旧的 content+padding=canvas 等式。无变换时 `video_transform` 与 `visible_content_rect` 为 null，原字段语义保持。`scale_filter` 为内部诊断，非客户端可输入的稳定接口。队列摘要携带变换，重试继承原冻结请求，同 ID 内容变化不能被视为相同提交。

失败例：旋转 45、比例 201、偏移 1、缺字段或额外字段为 INVALID_ARGUMENT；标准以外预设为 UNSUPPORTED_OPERATION；内存估算超限为 RESOURCE_LIMIT；未启用开关为 UNSUPPORTED_OPERATION。已有目标不得替换。失败、取消与清理遵守 CORE-004，除受控验证外仍需独立取消、磁盘空间、异常与导出覆盖检查。
