---
status: accepted
---

# 事后标注器接入生产链：夜间调度、标签落库与 C 计数回灌

ADR-0007 要求标注器 Day 0 上线且每晚运行，但此前标注器只实现了独立模块，生产链（`production_context.py` / `production_orchestrator.py`）从不调用任何 Labeler，无夜间调度入口，板块 v2 仍在 shadow 未切流，标签从未回灌 C 计数。本 ADR 冻结接入形态，实现落点 `src/labeler/`。

## 一、接入形态

新增 CLI 入口 `secondordergame-labeler-nightly`（`src/labeler/nightly.py`），每晚独立于决策链运行：

1. **标签流落库**：板块 v1（`SectorLabeler`）与个股主力层（`StockLabeler`）标签写入 `LabelLedger`（`runtime/labeler/labels.db`），按 `(scope, entity, trading_date, rule_hash)` 幂等 upsert。延迟窗口内未来日期不写。独立哈希隔离新旧规则计数。
2. **板块 v2 影子**：`SectorLabelerV2` 产出写入 `ShadowStateStore`（`runtime/labeler/shadow_v2.db`），达门槛后 `ShadowCutoverManager` 全量重标、独立重建 C 并原子切换 `runtime/labeler/production/active.json`。
3. **C 混淆矩阵回灌**：生产链每次 LLM 周期观测经 `llm_observation_sink` 写入 `ConfusionCountStore`（`runtime/labeler/confusion.db`）；夜间调度把板块真实标签与同日 LLM 观测配对，累加 `C[true_z][llm_ℓ]`，后验 = (α·先验 + 计数)/(α + n)。

## 二、关键决策

- **调度与决策解耦**：夜间调度是独立批处理，不嵌入 `production_context` 实时链；决策链只多一个可选的观测记录 sink，失败静默不阻断决策。
- **启动自动补跑**：`PAEmbeddedService` 初始化时后台线程执行 `run_labeler_catchup`，按 `CATCHUP_LABEL_WINDOW`（前视 10+5+3=18 交易日）检测落后板块并逐日补跑；板块列表默认读 material_cache 的 `sector_registry`，可注入；数据源不可用提示并跳过，不阻塞程序打开；幂等。
- **数据源复用**：板块 K_DAY 走统一板块行情源，个股 K_DAY 走订阅源；单板块失败不中断整夜调度。
- **v2 阈值未冻结**（ADR-0022）：v2 观测数据源与 TrendState 规则当前为可注入、可降级设计；数据不足时记录原因、不产出标签、安全不切流。散户层标注器（架构表第三行）仍为设计草案，不在本次范围。
- **C 计数是校准输入，不是实时观测**：混淆矩阵仍由 HMM 前向滤波消费；回灌只更新计数存储，不直接改写 `hmm_prior.yaml` 的手写先验。
