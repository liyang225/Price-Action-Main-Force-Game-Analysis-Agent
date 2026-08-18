---
status: accepted
---

# 大模型输入注入 Schema

> 推演引擎喂给大模型的结构化字段的唯一契约。实现落点 `src/reasoning/prompt_materials.py`，逐环节白名单投影。
>
> 决策依据见 ADR-0027；本文是 ADR-0027 的字段级补充，二者共同冻结注入边界。

---

## 1. 总原则

1. **大模型不读原始个股 K 线序列**，只读程序算好的结构化字段。板块指数 K_120M（60 根）作为唯一的序列化行情输入，供大模型读趋势。
2. **三个注入环节各有独立白名单**（严格逐字段枚举），不共用一套字段集。
3. **情绪指数（0–100）与五档周期位置隔离**：情绪指数是连续解释量，不作为判定周期的依据；它注入大模型作为辅助参考，但提示词明确「不得用阈值从指数机械映射到档位」（ADR-0014）。
4. **程序专用字段不注入大模型**：`material_cache`、`material_snapshot`、`probability_chain`、`position_cases`、`market_window`。
5. **PA 的思考（reasoning_content）不注入**：二阶只接收结构化 `stage1_diagnosis` / `stage2_decision`（含 `decision.reasoning` 文字）。PA 侧 payload 不改。
6. **每次模型调用独立对话**：`complete` 每次构造全新 `[system, user]`，不携带历史（现状已满足，无需改）。

---

## 2. 三个注入环节与投影函数

| 环节 | 模块 | 投影函数 | 系统提示词 | 输出 schema |
|---|---|---|---|---|
| 情绪周期判断 | `CycleClassifier` | `project_cycle_payload` | `通用/情绪周期判断.txt` | `CycleModelOutput` |
| 参与者识别 | `ParticipantClassifier` | `project_reasoning_payload` | `通用/参与者识别.txt` | `ParticipantModelOutput` |
| 行为推演 | `BehaviorForecaster` | `project_forecast_payload` | `人设与思维方式.txt` + 路由行为 txt | `MainForce/RetailBehaviorModelOutput` |

---

## 3. 逐环节白名单（字段枚举）

### 3.1 情绪周期判断（`project_cycle_payload`）

| 字段 | 说明 |
|---|---|
| `market_analysis` | DSA 大盘（元数据 + `display_sections`） |
| `sentiment_breadth` | 情绪广度 |
| `limit_pool` | 连板池（涨停/跌停，市场情绪广度证据） |
| `pa_stage1_analysis` | PA 阶段 1 结构化诊断（可选） |
| `sector_analysis` | 板块分析（含情绪指数，仅作辅助参考、不用于阈值映射） |
| `user_context` | 用户经验 |
| `news` | 原始消息 |
| `scored_news` | 评分消息 |
| `subject_purpose` | 主体目的（可选） |
| `previous_cycle_position` | 昨日周期（程序注入，可选） |

**剔除**：`pa_stage2`、`dragon_tiger`、`participant_priors`、`probability_chain`、`position_cases`、`market_window`、审计字段。

### 3.2 参与者识别（`project_reasoning_payload`）

在 3.1 基础上：`sector_analysis` **保留** `sentiment_index`（派生 `sentiment_signal`），并增加 `pa_stage2`、`dragon_tiger`、`capital_flow`（个股资金流，主力行为证据）。

**剔除**：`participant_priors`、`probability_chain`、`position_cases`、`market_window`、审计字段。

### 3.3 行为推演（`project_forecast_payload`）

在 3.2 基础上：增加 `participant_priors`（参与者先验，下发到行为推演环节）。

**剔除**：`probability_chain`、`position_cases`、`market_window`、审计字段。

---

## 4. 关键字段定义

### 4.1 板块 K 线快照（`sector_analysis.sector_kline_120m`）

板块指数 K_120M 最近 **60 根**已收盘，注入**全部三个环节**（通过 `sector_analysis` 进入每个白名单）。程序计算，大模型只读。

| 字段 | 类型 | 缺省 |
|---|---|---|
| `status` | "ready"\|"unavailable"\|"insufficient_data" | — |
| `bars` | list[OHLCV]（time/open/high/low/close/volume/turnover） | `[]` |
| `error` | str（非 ready 时） | — |

`status` 非 `ready` 时属**软降级**：仍随 `sector_analysis` 注入，但三个环节的提示词均约束大模型「不得臆造板块趋势与价格形态」，仅依据其余可用材料判断。

实现：`production_context._sector_kline_120m`，经 `PAMarketDataAdapter.get_kline`（非订阅 symbol 的 K_120M 走板块行情数据源）。

### 4.2 龙虎榜（`dragon_tiger`，仅参与者识别与行为推演）

**信号分档 + 原始席位明细二者都注入**。实现：`production_context._dragon_tiger_material`。

| 子字段 | 说明 |
|---|---|
| `status` | `ok`\|`no_data`\|`missing_fields`\|`conflict`\|`source_error` |
| `signal` | 信号分档：机构净买/净卖、游资净买/净卖、机构席位、游资席位、上榜原因、来源 |
| `raw` | 原始席位明细：净买额、买卖额、机构/游资/买/卖四类席位、来源引用 |

### 4.3 连板池（`limit_pool`，全部三环节）

涨停/跌停池作为市场情绪广度证据，聚合分档 + 原始池一并注入。实现：`production_context._limit_pool_material`。

| 子字段 | 说明 |
|---|---|
| `status` | `ready`\|`no_data`\|`source_error` |
| `rise_count` / `fall_count` | 涨停/跌停家数 |
| `max_rise_streak` / `max_fall_streak` | 最高连板数 / 最高连续跌停数 |
| `rise_pool` / `fall_pool` | 原始池（code + limit_streak） |

### 4.4 连续情绪信号（`sentiment_signal`，仅参与者识别与行为推演）

由 `project_reasoning_payload` / `project_forecast_payload` 从 `sector_analysis.sentiment_index_details` 派生：`index`、`previous_index`、`daily_delta`、`news_delta`、`price_action_delta`、`usable`。`usable=false` 时大模型须忽略其数值。不得用于推导五档周期。

### 4.5 昨日周期位置（`previous_cycle_position`，仅周期判断）

来自 HMM 情绪台账 / 前次 `CycleObservation`，程序注入，`previous_state in CYCLE_STATES` 时才写入。

### 4.6 消息事件

`news`（原始，缺省 `not_prefetched`）、`scored_news`（评分，缺省 `empty`）、`subject_purpose`（主体目的，可选）。「无消息」是合法状态，不强制补数据。

### 4.7 板块级聚合（`sector_analysis.board_analysis`，全部三环节）

板块级资金流 / 连板 / 龙虎榜聚合，由 `production_context._board_analysis_material` 复用 `SectorAnalysisService.collect` 产出，**既供 UI 展示也注入大模型**（通过 `sector_analysis` 进入每个白名单）。属证据性材料，任一维度失败只降级为 `status`/`errors`，不阻断确定性链路。

| 子字段 | 说明 |
|---|---|
| `status` | `ready`\|`partial`\|`unavailable` |
| `capital_flow` | 板块资金流窗口（date + main/super/big/mid/sml_in_flow，读 P0-8 台账按板块代码） |
| `limit_pool` | 板块成分股过滤后的连板（date/code/limit_streak/direction） |
| `dragon_tiger` | 板块成分股过滤后的龙虎榜（date/code/reason/机构与游资净买净卖/席位） |
| `errors` | 各维度降级原因 |

### 4.8 个股资金流（`capital_flow`，仅参与者识别与行为推演）

`production_context._capital_flow_material`，读 P0-8 台账按个股代码。资金流是主力行为证据，不进情绪周期判断。

| 子字段 | 说明 |
|---|---|
| `status` | `ready`\|`no_data`\|`unavailable`\|`not_configured` |
| `code` | 个股代码 |
| `window_days` / `items` | 窗口天数与逐日明细 |
| `main_flow_5d` / `10d` / `20d` | 主力净流入分段求和 |
| `latest_main_flow` | 最新主力净流入 |

---

## 5. 禁止注入清单（硬约束）

1. 原始个股 K 线序列（`bars` 数组、`game_signal_series` 20 根回放序列）——个股博弈信号只注入单点快照（`game_signals`），板块趋势注入板块指数 60 根 K_120M。
2. 用阈值从情绪指数机械映射到五档周期（提示词明确禁止；情绪指数只作辅助参考）。
3. `probability_chain`（B/C 概率与 T+1 闸门是程序产物）。
4. `material_cache` / `material_snapshot` / `position_cases` / `market_window` 审计与程序结构。
5. 任何让大模型「估算概率数字」的字段（ADR-0001/0009）。

---

## 6. 实现清单

| 文件 | 改动 |
|---|---|
| `src/reasoning/prompt_materials.py` | 白名单投影：`project_cycle_payload` / `project_reasoning_payload` / `project_forecast_payload` |
| `src/reasoning/cycle_classifier.py` | 改调 `project_cycle_payload` |
| `src/reasoning/behavior_forecaster.py` | 改调 `project_forecast_payload` |
| `src/integration/production_context.py` | `sector_kline_120m` 入 `sector_analysis`；`dragon_tiger` 入 materials |
| `src/integration/pa_market_adapter.py` | 非订阅 symbol 的 K_120M 走板块行情数据源 |
