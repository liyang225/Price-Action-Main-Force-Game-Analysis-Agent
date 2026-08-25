# 05 — 持久化板块情绪台账

**What to build:** 为每个板块保存独立的情绪指数、惯性状态和更新时间，使进程重启及跨交易日后能够从原状态继续。

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] 写入板块状态后重新打开进程可恢复完全相同的值。
- [ ] 不同板块互不覆盖，情绪台账与分析历史相互独立。
- [ ] 临时数据库测试覆盖首次创建、更新、重启恢复和损坏记录处理。

## Required context

- `AGENTS.md`、`CONTEXT.md`、`ARCHITECTURE.md`、`docs/ROADMAP.md`
- `docs/adr/0002-sector-level-sentiment.md`
- `docs/adr/0008-pipeline-in-memory-no-intermediate-writes.md`
- `docs/adr/0014-sentiment-index-scoring.md`
- `docs/adr/0021-sector-cycle-and-consensus-model.md`
- `docs/adr/0020-test-seams-and-fixtures.md`
- 可自行探索后续会读取台账的信念更新和缓存边界；本票仍只负责持久化契约。

## Files in scope

- 创建：`src/data/sentiment_ledger.py`、`tests/test_sentiment_ledger.py`
- 修改：`src/data/__init__.py`（如果 02 已创建）
- 复用：`tests/conftest.py` 的临时 SQLite 夹具
- 参考：`config/sentiment.yaml`、`config/sectors.yaml`

## Constraints and non-goals

- 台账是一等领域状态，不与分析报告、当日材料缓存或预测历史共表。
- 不计算情绪周期位置，不把全市场压成一个情绪值。
- 周期位置由 HMM 信念更新持久化；板块级 `consensus_state` / `consensus_direction` 属于 T42 的独立 v2 影子字段，不得借本票写入或推导。
- 不在测试中断言具体 SQL 或表结构，只验证可观察的持久化行为。
- 不吞掉数据库损坏、schema 不兼容或板块键冲突。

## Verification

在 `SecondOrderGame/` 根目录运行：

```powershell
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/test_sentiment_ledger.py -v
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/ -v
```

交付证据需包含关闭并重新打开数据库后恢复同一板块状态的测试。
