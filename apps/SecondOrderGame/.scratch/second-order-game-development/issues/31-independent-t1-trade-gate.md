# 31 — 接入独立 T+1 下单闸门

**What to build:** 在 T+1 模式下把二阶博弈结论作为独立布尔闸门接入下单前流程，同时保持 PA 原有盈亏比逻辑不变。

**Blocked by:** 15 — 计算程序化 T+1 闸门; 29 — 建立 PA 阶段 2 数据桥; 30 — 增加完成回调及双决策点调度.

Status: ready-for-human
Delivery: complete (2026-08-12; automated verification complete, pending human acceptance)

- [x] T+1 模式必须同时通过 PA 原闸门和二阶独立闸门才能继续。
- [x] 二阶结果不改写 PA 胜率、目标价、止损价或盈亏比判断。
- [x] T+0 模式和二阶数据不足场景分别表现为不接入和明确不通过。

## Comments

### 2026-08-12 交付记录

- 新增 `IndependentT1TradeGate`：T+1 必须同时通过 PA 原闸门与二阶闸门；T+0 返回 `not_applicable`。
- 二阶数据不足返回 `insufficient_data` 并过滤新增买入动作；PA 原始价格、胜率与盈亏比字段不被修改。
- 新增双闸门、T+0 和数据不足测试。
