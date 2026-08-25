# 10 — 实现个股层事后标注器及 Golden 回归

**What to build:** 按冻结规则为股票日产生参与者与个股行为标签，并用固化样本保护标签分布、无标签语义和规则哈希。

**Blocked by:** 02 — 建立统一市场数据接缝.

**Status:** ready-for-human

- [x] 输出参与者仅为主力或散户，行为仅为六种统一枚举，单股单日最多一个标签。
- [x] 六条规则均未命中时保持无标签，绝不兜底成观望。
- [x] CI 样本含六类行为和无标签行，能够复现 manifest 的精确命中数。
- [x] 全量本地验收复现冻结分布与 26.99% 覆盖率，并留下交付记录。

## Required context

- `AGENTS.md`、`CONTEXT.md`、`ARCHITECTURE.md`、`docs/ROADMAP.md`
- `docs/adr/0007-labeler-day0-independent-freeze.md`
- `docs/adr/0017-labeler-rules-frozen.md`
- `docs/adr/0018-w-matrix-collapse-to-2participant.md`
- `docs/adr/0020-test-seams-and-fixtures.md`
- `.scratch/business-rules/reports/` 和冻结研究数据；可自行探索现有校验测试以复用枚举与 hash 规则。

## Files in scope

- 创建：`src/labeler/__init__.py`、`src/labeler/stock_labeler.py`
- 创建：`tests/test_stock_labeler.py`
- 创建：`tests/fixtures/golden_sample.csv.gz`、`tests/fixtures/labeler_manifest.json`
- 修改：`tests/conftest.py`，替换 Golden 占位夹具
- 参考：`config/labeler.yaml`、`src/labeler_constants.py`、`tests/test_config_signals_labeler.py`
- 本地全量参考：`.scratch/business-rules/experiments/output/`

## Constraints and non-goals

- 无标签交易日保持无标签，不兜底为观望；不可用行不进入覆盖率分母。
- 前向收益使用相对主板块的超额口径；不临时实现多板块归属。
- 六类行为规则、优先级和阈值完全来自冻结配置。
- Golden 小样本进入测试目录，全量研究数据继续留在 `.scratch/`。

## Verification

在 `SecondOrderGame/` 根目录运行：

```powershell
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/test_stock_labeler.py tests/test_config_signals_labeler.py -v
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/ -v
```

此外按验证报告命令运行一次全量本地标注，记录六类绝对数、无标签数、不可用数、覆盖率和多命中数。

## Comments

### 2026-08-10 交付记录

- 参与者按 ADR-0018 固定为二方：正的 `main_in_flow` 中，`(super_in_flow + big_in_flow) / main_in_flow > 0.60` 标为主力，其余标为散户；不拆分操纵型与配置型主力。
- Golden 样本固定种子 `20260810`，共 2,000 行：建仓 90、震仓 40、拉升 35、出货 35、观望 300、狩猎止损 40、无标签 1,460。行为特征来自固化 OHLCV，参与者字段是专门覆盖 60% 严格边界的合成资金流值；两者的来源及夹具哈希均记录在 manifest。规则 SHA256 为 `3e101dd9b499edbe9a9dc2b23ca0eb7a91a44fb1425d2f6d617078d0a5bac3a8`。
- 全量本地验收：有效 94,594 行；建仓 4,279、震仓 561、拉升 479、出货 614、观望 19,401、狩猎止损 202；无标签 69,058；不可用 13,488；覆盖率 `26.9953696852%`；多命中 13。
- `tests/test_stock_labeler.py tests/test_config_signals_labeler.py`：112 passed。
- 全套 `tests/`：215 passed、1 skipped。
