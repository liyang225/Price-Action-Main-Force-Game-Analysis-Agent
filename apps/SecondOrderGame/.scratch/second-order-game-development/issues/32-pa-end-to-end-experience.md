# 32 — 打通 PA 端到端联动页面

**What to build:** 让用户在 PA 完成阶段 2 后自动获得二阶博弈结果，并在同一品种页查看原始 PA 指标、独立闸门和情景应对树。

**Blocked by:** 27 — 生成完整情景应对树; 28 — 增加品种页第三层子标签栏; 31 — 接入独立 T+1 下单闸门.

Status: ready-for-human
Delivery: complete (2026-08-12; automated verification complete, pending human acceptance)

- [x] T+1 模式下阶段 2 完成后五秒内开始二阶推演并显示运行状态。
- [x] 页面显示 PA 原始胜率而非“调整后胜率”，并单独展示二阶闸门与理由。
- [x] 三情景应对树、失败重试和数据不足状态均可在品种页完整查看。

## Comments

### 2026-08-12 交付记录

- 新增 `ProductionOrchestrator`，把 PA 阶段 2 回调、桥接、推演、独立闸门和 `PAWorkspaceState` 串成生产链。
- `submit` 先返回 loading 状态再后台执行，结果页保留 PA 原始指标、三情景树、各情景独立闸门和来源追踪；失败状态可 `retry`。
- 提供 `from_pa_model_client` / `build_production_orchestrator` 生产装配入口，并覆盖异步状态、重试与端到端测试。
