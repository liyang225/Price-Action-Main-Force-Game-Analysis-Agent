---
status: accepted
---

# W 行为矩阵回灌 + HMM 后验消费（标签反哺先验闭环）

ADR-0007/0017 要求标注器 Day 0 上线，目标是让 HMM 先验向数据过渡。但此前「标签反哺先验」只接通了**计数存储**这一半：`ConfusionCountStore` 与 `LabelLedger` 在积累计数，而 `HMMFilter` 只读 `hmm_prior.yaml` 的手写先验，从不消费计数——计数是死数据。本 ADR 打通完整闭环。

## 一、完整闭环（四段）

```
事后标注器（每晚 + 启动补跑）
  → 标签落库 LabelLedger
  → 计数回灌（C 混淆 + W 行为）
  → HMM 后验消费（先验 + 计数 → 后验矩阵）
```

1. **C 混淆矩阵回灌**（已有，本次激活）：板块真实标签 × LLM 观测 → `ConfusionCountStore`。
2. **W 行为矩阵回灌**（本次新增）：板块真实周期 z × 个股主力行为 → `BehaviorCountStore`（`runtime/labeler/behavior.db`）。
3. **后验融合**（本次新增）：`src/labeler/calibration.py` 的 `load_production_hmm_config()` 把 `hmm_prior.yaml` 先验 + 计数融合成 `(alpha·先验 + 计数)/(alpha + n)` 后验，逐行归一化。
4. **生产链消费**（本次新增）：`production_context._load_calibrated_hmm` 与 `production_orchestrator.from_pa_model_client` 改用融合后的 config；空库时回退纯先验，零回归。

## 二、关键决策

- **散户 W 行不回灌**：需要散户层标注器（`RetailLabeler`，设计草案）产出 FOMO追高/恐慌割肉/… 标签，本轮只回灌主力行（ADR-0018：不得把主力标签复制进散户行）。散户行保持先验。
- **participant 分档**：`BehaviorCountStore.reconcile` 只统计 `participant == "主力"` 的个股行。资金流不可用时 `classify_participant` 判为散户，主力行不回灌——这是正确行为，不是缺陷。
- **stock 标签补板块归属**：`LabelLedger.record_stock_labels` 新增 `sector_code` 参数（写入 feature_json），使 W 回灌能配对 (板块周期 z × 个股行为)。不改变行主键。
- **规则哈希隔离**：W 计数键 = (cycle_rule_hash, behavior_rule_hash, cycle, participant, behavior)，改任一标注规则不混用旧计数。
- **先验权重**：后验仍带 `alpha` 字段，下游可按 `alpha/(alpha+n)` 报告先验权重；满足 ADR「先验权重降至 20% 以下」的审计要求。
