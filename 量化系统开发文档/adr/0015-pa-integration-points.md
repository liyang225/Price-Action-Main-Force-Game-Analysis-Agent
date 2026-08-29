---
status: accepted
---

# PA 联动的三个接入点：独立闸门、品种页内子标签栏、显式回调

ADR-0010 定了 T+0 / T+1 两种模式，但没有落到 PA 的具体代码结构上。读过 PA_Agent 6.24 源码后，三个接入点各有多个可行方案，本 ADR 记录选择。

## 一、胜率不改 PA 闸门，二阶博弈输出独立闸门

**PA 的实际结构与原先假设不同。** ADR-0010 写「二阶博弈调整胜率 → 影响交易者方程 → 决定下单」，但 PA 里胜率与下单闸门是解耦的：

- `estimated_win_rate`（整数 0–100，可为 null）只被 `format_estimated_win_rate` 格式化用于**显示**
- 真正的下单闸门在 `pa_agent/util/trade_metrics.py`：`compute_risk_reward()` 算盈亏比，`MIN_RISK_REWARD_RATIO = 1.0` 是准入线，`MAX_TP1_RISK_REWARD_RATIO = 1.0` 是上限（超了调宽止损而非缩小目标）
- 胜率没有参与任何计算

因此「调整胜率」不会影响下单。二阶博弈改为输出**独立的布尔闸门 + 理由文本**，在 T+1 模式下作为下单前的最后一道判断。

### 拒绝的方案

- **改 PA 闸门让它同时看胜率阈值**：两个系统的下单逻辑纠缠，PA 日后单独升级容易踩坏
- **让二阶博弈调整 `stop_loss_price` 间接改变盈亏比**：越过职责边界。二阶博弈判参与者意图，不该动价格参数

## 二、UI：品种页内新建第三层子标签栏

PA 现有两层 QTabWidget：

| 层 | 位置 | 内容 |
|---|---|---|
| 工作区 | `gui/workspace_window.py:1380` | 「自选股」+ 每个品种一个 terminal 页 |
| 分析侧栏 | `gui/ai_sidebar.py:61` | 历史记录 / 交互 / 决策 / 未来走势预期 / 决策树 / 决策树可视化 / 原始 / 调试 |

二阶博弈**不进这两层的任何一层**，而是在品种页内部新建第三层子标签栏：打开一个品种后，其页面内有一个子标签栏，把「PA 技术分析流程」和「二阶博弈流程」并列为两个独立工作区。

这个子标签栏在视觉上必须与工作区那层（自选股 + 品种并排）明确区分，避免用户混淆层级。

理由：两个流程各自完整、可独立使用。放进 `ai_sidebar` 会让二阶博弈变成 PA 分析流程的一个视图，而它是一个平级的分析体系，有自己的完整流程（消息预取 → 情绪判定 → 参与者识别 → 推演 → 应对树）。放进工作区那层则会与品种概念冲突——二阶博弈是对某个品种的分析，不是另一个品种。

### 实现位置

`_ensure_terminal()`（`workspace_window.py:1595`）当前把 terminal 直接 `addTab` 到工作区。改为：terminal 外包一层子标签 QTabWidget，PA 现有 terminal 作为第一个子标签，二阶博弈面板作为第二个。

## 三、加显式 `on_stage2_complete` 回调，不借 router 注入

`TwoStageOrchestrator.__init__` 的 `router` 参数已支持传对象（docstring：「either the `route_strategy_files` function or an object with a `.route()` method」），构造点在 `workspace_window.py:1680`（`router=route_strategy_files`）。因此技术上可以包一个 router，在 `.route()` 被调用时顺带触发二阶博弈。

**不这么做。** 路由必须是无副作用纯函数（ADR-0005），把触发逻辑塞进去会让它不可测试，且 `.route()` 在 stage 1 之后调用，不是 stage 2 完成的时机。

改为在 `TwoStageOrchestrator` 加一个可选的 `on_stage2_complete` 回调参数。这是 PA 侧最小的改动，意图清晰。回调边界必须同时满足 T+1 模式与 PA 阶段 2 决定下单；T+0 模式或 PA 决定不下单时均完全关闭自动运行，仅同步结果供独立查看。

## 附带确认的事实

- **K 线周期**：`data/futu_source.py:23` 的映射是 `"2h": "K_120M"`，传 `"2h"` 即可拿到 K_120M，不需改 PA（印证 ADR-0004）
- **PA 的路由映射硬编码在模块常量**（`_BULLISH_SPIKE_FILES` 等），与本项目 ADR-0005「映射表放配置文件」不同。这是刻意分歧：PA 的路由随代码演进，本项目的路由随经验调优
- **Stage 2 schema 的 conditional required**：不下单时 `entry_price` / `stop_loss_price` / `estimated_win_rate` 必须为 null，下单时必须有值。二阶博弈读取时必须处理 null 分支
