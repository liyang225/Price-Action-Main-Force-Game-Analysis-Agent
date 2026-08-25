# 09 — 实现板块层事后标注器

**What to build:** 根据冻结的板块指数 OHLCV 规则产生冰点、启动、发酵、高潮或退潮标签，并保存可审计的版本与规则哈希；板块级分歧属于正交共识字段，不由本票的周期标签替代。

**Blocked by:** 08 — 人工验证并冻结板块规则.

**Status:** ready-for-agent

- [x] 给定板块 OHLCV 可确定性地产生五档标签、`unlabeled` 或 `data_insufficient` 结果。
- [x] 标注器不读取板块情绪指数或另一层标签来推导结果。
- [x] 每条已标注输出携带 `evidence_mode` 与 `expansion_verified`；v1 发酵明确为 `price_trend_proxy` / `false`，无标签与数据不足不携带证据元数据。
- [x] Golden 回归覆盖五档、优先级冲突、窗口不足和规则哈希变化。

## Completion record

- 生产实现：`src/labeler/sector_labeler.py`
- Golden fixture：5 个板块、8,000 行 OHLCV；五档均有命中，manifest 保存冻结哈希和计数。
- 专项测试：`tests/test_sector_labeler.py`，9 项通过。

## Required context

- `AGENTS.md`、`CONTEXT.md`、`ARCHITECTURE.md`、`docs/ROADMAP.md`
- `docs/adr/0007-labeler-day0-independent-freeze.md`
- `docs/adr/0021-sector-cycle-and-consensus-model.md`
- `docs/adr/0019-sector-labeler-draft-rules.md`
- `docs/adr/0021-sector-cycle-and-consensus-model.md`
- `docs/adr/0020-test-seams-and-fixtures.md`
- T08 冻结的配置、manifest 和验证报告；可以探索个股标注器的实现风格，但两层不得共享标签逻辑。

## Files in scope

- 创建：`src/labeler/__init__.py`、`src/labeler/sector_labeler.py`
- 创建：`tests/test_sector_labeler.py`
- 创建：`tests/fixtures/sector_labeler_sample.csv.gz`、`tests/fixtures/sector_labeler_manifest.json`
- 修改：`src/config_validator.py`（仅补 sector labeler schema/冻结版本校验）
- 参考：`config/sector_labeler.yaml`、`src/labeler_constants.py`

## Constraints and non-goals

- 输入只允许板块指数 OHLCV 和冻结规则；禁止读取 `sentiment.yaml` 计算出的指数。
- 不复用个股的六类行为规则，不生成参与者或个股行为标签。
- 阈值全部来自配置，不在代码中出现业务阈值常量。
- 本票不改人工冻结结论；发现分布问题应退回 T08，而不是在代码中补丁修正。

## Verification

在 `SecondOrderGame/` 根目录运行：

```powershell
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/test_sector_labeler.py tests/test_config_signals_labeler.py -v
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/ -v
```

交付证据必须包含“输入不含情绪指数”的契约测试。
