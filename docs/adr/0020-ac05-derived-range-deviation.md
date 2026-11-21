# ADR-0020：AC05 偏差数值的表示责任

2026-11-14。状态：ACCEPTED_STATIC_REVIEW。Integration 对既有契约的明确裁定；没有新增接口或验收条件。

**AC05 不要求 Core JSON 新增独立偏差字段。** Core 返回同一规范原点、整数微秒单位的 `requested_range_us` 和 `actual_range_us`；原生端核对同任务的不可变请求后，分别以 checked `actual.start-requested.start`、`actual.end-requested.end` 显示带正负号的具体数值，解释负值提前、正值延后。这完整提供产品要求的请求、实际和偏差信息。

Core 仍唯一决定关键帧/EOF 实际范围和媒体验证。上述确定减法与格式化属于表示责任，不允许 UI 重选剪切点、用当前可变选区替换原请求，或混用原始 PTS 的 `actual_source_range_us`。只把两区间并排展示仍不满足偏差数值要求。

依据：CORE-002/003 列明两个 range，既有契约没有第三个 deviation key 的名称、类型或单位定义。AC05 的“返回请求/实际/偏差”是产品可观察要求，不追加从未定义的 JSON 字段门槛。没有修改 AC05、媒体判据、请求/响应 schema 或八函数 ABI。

已审实现：`App.swift` 使用 checked subtraction 和六位小数秒显示；`WorkspaceQueue.observeQueue` 核对 Core requested 与冻结 draft，一旦不符便撤销成功展示。例：请求 [1.1,2.6)、实际 [1.0,3.0) 秒，应显示入点 −0.100000、出点 +0.400000 秒。

本裁定关闭独立字段的解释门槛。packet/帧映射、关键帧包围、时移/尾差和真实 GUI 数值仍各自验收；不因此宣布完整 AC05 或 QA-002 全卡通过。
