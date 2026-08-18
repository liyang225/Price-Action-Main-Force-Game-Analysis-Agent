---
status: accepted
---

# 大模型输入注入采用逐环节白名单

推演引擎喂给大模型的字段按「逐环节、逐字段」白名单收敛，字段级契约见 `docs/llm-injection-schema.md`，实现落点 `src/reasoning/prompt_materials.py`。

此前投影是「黑名单」：只剔除审计字段，其余整个 `materials` 全量注入，导致 `participant_priors`、`probability_chain`、`position_cases` 等程序专用字段泄漏给大模型，与「大模型只读结构化字段、不读原始序列」的原则不符。现改为三个环节（情绪周期判断 / 参与者识别 / 行为推演）各持独立白名单，非白名单字段一律不下发。

板块情绪周期判断不再依赖程序预计算的「趋势总结」字段，改为直接注入板块指数 K_120M 最近 60 根已收盘，供大模型读取趋势；该序列进入全部三个环节。个股博弈信号仍只注入单点快照（`game_signals`），不注入原始个股 K 线序列。

龙虎榜数据（AkShare）作为信号分档与原始席位明细二者，注入材料缓存并下发给参与者识别与行为推演环节，作为操纵型/配置型主力行为的额外观测证据，与既有的程序层 HMM 信念折算（`behavior_forecaster._apply_dragon_tiger`）并行，不互相替代。连板池（AkShare 涨停/跌停池）作为市场情绪广度证据，聚合分档 + 原始池注入全部三个环节；龙虎榜与连板池共用同一个 `AkShareMarketDataSource` 实例，分别经 `dragon_tiger_provider` 与 `breadth_provider` 挂载到 `FutuMarketDataSource`。

`participant_priors` 仅下发到行为推演环节；情绪指数（`sentiment_index`）注入全部三个环节作为辅助参考，但不得用阈值从指数机械映射到档位（ADR-0014，混淆矩阵 C 不得退化为恒等映射）。PA 的思考（`reasoning_content`）不注入二阶，二阶只接收结构化 `stage1_diagnosis` / `stage2_decision`；每次模型调用保持独立 `[system, user]`，不携带历史对话。
