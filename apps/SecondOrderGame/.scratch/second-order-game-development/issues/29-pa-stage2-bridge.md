# 29 — 建立 PA 阶段 2 数据桥

**What to build:** 将 PA 阶段 2 的结构化结果转换为二阶博弈输入，同时保留技术分析语义并安全处理不交易分支的空字段。

**Blocked by:** 24 — 通过薄适配层复用 PA 模型客户端; 27 — 生成完整情景应对树.

Status: ready-for-human
Delivery: complete (2026-08-12; automated verification complete, pending human acceptance)

- [x] 下单与不下单两类 PA 输出均能转换，不交易时的空价格和胜率不会引发错误。
- [x] 数据桥只做格式适配与上下文注入，不改变 PA 原始胜率或盈亏比。
- [x] 转换后的输入可以完整驱动二阶推演，并保留来源追踪信息。

## Comments

### 2026-08-12 交付记录

- 新增 `PAStage2Bridge`、`BridgeContext` 与 `SecondOrderInput`，保留 PA 原始 payload、胜率、价格和不交易时的 null 字段。
- 桥接层只负责格式适配与上下文注入，来源追踪写入结构化 `source_trace`，并可直接生成 `ReasoningPipelineRequest`。
- 新增 no-trade、上下文注入与序列化测试。
