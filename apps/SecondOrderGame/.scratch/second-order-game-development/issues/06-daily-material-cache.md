# 06 — 管理分析材料缓存生命周期

**What to build:** 提供当日共享的分析材料缓存，允许后台持续填充，决策过程只读，收盘归档后于下一交易日重建。

**Blocked by:** 02 — 建立统一市场数据接缝.

**Status:** ready-for-agent

- [ ] 同一交易日的多次决策读取同一份冻结快照，不受并发写入影响。
- [ ] 收盘可归档当日材料，下一交易日不会误读昨日缓存。
- [ ] 假时钟测试覆盖盘中、收盘边界、跨日和进程重启场景。

## Required context

- `AGENTS.md`、`CONTEXT.md`、`ARCHITECTURE.md`、`docs/ROADMAP.md`
- `docs/adr/0003-two-decision-points-per-day.md`
- `docs/adr/0006-news-background-prefetch.md`
- `docs/adr/0008-pipeline-in-memory-no-intermediate-writes.md`
- `docs/adr/0020-test-seams-and-fixtures.md`
- 可以自行探索消息预取、政策检测和 PA 数据桥的预期消费者，但不要提前实现这些消费者。

## Files in scope

- 创建：`src/data/daily_cache.py`、`tests/test_daily_cache.py`
- 修改：`src/data/__init__.py`
- 复用：`src/data/models.py`、`src/data/fake_client.py`、`tests/conftest.py`
- 参考：`config/sectors.yaml`

## Constraints and non-goals

- 缓存只保存当日跨决策点共享材料，不把分析流水线的每个中间步骤落盘。
- 决策读取必须使用一致快照；后台写入不能让一次决策看到半更新状态。
- 不实现新闻搜索、情绪计算、推演或长期历史仓库。
- 日期边界必须由可注入时钟决定，不直接绑定系统挂钟。

## Verification

在 `SecondOrderGame/` 根目录运行：

```powershell
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/test_daily_cache.py -v
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/ -v
```

必须覆盖 11:30、15:00、跨午夜/下一交易日和归档失败的行为。
