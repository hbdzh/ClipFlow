# 双 stem 混音导出（受控开发契约草案）

状态：Integration/Core 联合设计；以实现和实际测试为准，不代表发行接收或跨平台可用。依赖的音源分离结果仍受 [AI-001](AI-001.md) 限制。

## 接口与兼容

沿用八个 C ABI 函数、`schema_version: 0`、同一 engine 调用串行和原有所有权规则。新 operation 使用现有提交、快照和取消机制，拒绝未知字段，不以删字段或转换为其他操作重试旧 Core。默认 mock 不执行混音。`media_backend.mix_stems_enabled` 是严格布尔，省略为 false；为 true 时须同时启用 core003 和 core004。仅明确启用并探测组件可用后才由 `features.mix_stems_v1.available` 报告可用；客户端应检查 capability，不能仅从配置推断可用。

```json
{
  "schema_version": 0,
  "operation": "mix_stems",
  "vocals": {"uri": "file:///EXAMPLE/vocals.wav"},
  "accompaniment": {"uri": "file:///EXAMPLE/accompaniment.wav"},
  "destination": {"uri": "file:///EXAMPLE/mix.wav"},
  "include_vocals": true,
  "include_accompaniment": true
}
```

两个布尔值均必填且不能同时为 false。客户端在提交时把静音和独奏状态冻结为两个 include：某轨可听当且仅当该轨未静音且（没有任何独奏或该轨独奏）。后续 UI 状态变化不改变已提交的任务。

接收回执沿用媒体队列快照：`{"schema_version":0,"job_id":"...","operation":"mix_stems","state":"queued","mock_only":false,"progress":null,"error":null,"result":null,"request_summary":{},"created_sequence":1}`，`request_summary` 与序号以实际回执为准。预检拒绝返回 `operation:"submit", accepted:false, errors:[...]`，不创建任务。成功快照的 result 为 `{"schema_version":0,"mock_only":false,"file_created":true,"profile":"wav_f32_stereo_44100_v1","sample_rate":44100,"channels":2,"sample_count":N,"include_vocals":true,"include_accompaniment":true}`；`sample_count` 是每声道的采样帧数，include 与已冻结请求一致。失败使用现有队列错误模型，不伪造成功。

## 音频语义

两输入是匹配的 44,100 Hz、双声道 float32 WAV stem，实际格式、有效样本、通道与帧数必须校验，即使其中一轨未被选中；不同步、不完整或无法验证时拒绝。每一可听轨线性增益为 0.5，不可听轨增益为 0。输出固定为相同采样率、双声道、相同帧数的 float32 WAV；不重采样、添加延时、归一化或限幅。有限的超满幅样本原样保留；客户端提示后续播放或转换为整数格式可能削波，不声称这些数据已被限幅。非有限样本或无法确认有效音频不得发布。不得将试听成功当作文件导出或物理音频输出验收。

## 文件和生命周期

输入只读，输出不得与任一输入同一文件，不覆盖现有对象。输出先写入目标所在卷受控的任务私有暂存；校验成功后使用明确的 no-clobber 发布。取消、超时、失败或目标竞争时，先停止并回收所有写入者，再仅清理任务拥有且身份可确认的资源；无法确认时禁止误报成功或已清理。发布与取消应有明确线性化顺序，发布完成后的取消不得删除已发布结果。任务关闭、重试及资源所有权沿用 Core 队列约束，绝不能让取消仅停 UI 而遗留后台写入。

原生客户端负责从系统选择并在整个请求、执行、取消和清理期间持有输入及目标目录授权；不得在 Core 完成之前释放 security scope。此授权策略须在真实沙盒中单独验证。

当前计划复用路径级身份核查与私有目录中的硬链接排他发布；它不能证明抵御同一 UID 的恶意进程在检查与路径操作之间替换目录或文件。真实用户目录的并发替换安全性和不确定清理后的隔离应另行审核，不得把受控开发证据表述为通用对抗性安全保证。

## 必要验证

以自生成双声道样本逐采样验证双轨、单轨、静音/独奏组合、首尾帧与超满幅样本；同时用独立工具探测及完整解码输出。覆盖缺失/畸形输入、不同帧数或格式、全静音选择、已存在目标、取消及真实写入期间清理。旧请求、默认 mock、未知字段及能力协商须回归。长素材、权限变更、真实沙盒、资源占用和发行环境分别记录证据，不从单次成功推断通过。
