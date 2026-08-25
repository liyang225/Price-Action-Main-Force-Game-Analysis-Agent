# 11 — 运行消息后台预取与 Tavily 降级

**What to build:** 在决策发生前持续为在册板块预取消息，富途覆盖不足或失败时自动使用 Tavily，并把结果写入当日缓存。

**Blocked by:** 02 — 建立统一市场数据接缝; 06 — 管理分析材料缓存生命周期.

**Status:** ready-for-agent

- [ ] 20 个以内板块可在 60 秒内完成一轮搜索并写入缓存。
- [ ] 任意 30 秒内不超过 10 次受限调用，窗口恢复后任务继续。
- [ ] 富途失败、空结果和 Tavily 接管均可通过单一 fake 离线验证。

## Required context

- `AGENTS.md`、`CONTEXT.md`、`ARCHITECTURE.md`、`docs/ROADMAP.md`
- `docs/adr/0006-news-background-prefetch.md`
- `docs/adr/0013-sector-list-composition.md`
- `docs/adr/0020-test-seams-and-fixtures.md`
- 先读取 T02 与 T06 的实际公开接口；可以探索现有板块列表刷新逻辑，但不要另建板块注册表。

## Files in scope

- 创建：`src/data/news_prefetch.py`、`tests/test_news_prefetch.py`
- 修改：`src/data/__init__.py`
- 复用：`src/data/protocol.py`、`src/data/rate_limiter.py`、`src/data/daily_cache.py`、`src/data/fake_client.py`
- 参考：`config/sectors.yaml`、`tests/conftest.py`

## Constraints and non-goals

- 决策点只读缓存，不在用户等待路径中临时搜索网络。
- 限频是 10 次/30 秒，每约 3 秒一个板块；测试不得真实等待。
- 富途到 Tavily 的降级发生在统一数据边界内，不新增第二套业务接口。
- 本票不做新闻主体目的分析、政策判断或模型调用。

## Verification

在 `SecondOrderGame/` 根目录运行：

```powershell
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/test_news_prefetch.py tests/test_rate_limiter.py tests/test_daily_cache.py -v
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/ -v
```

必须证明 20 个板块的一轮调度、限频窗口边界、降级链和部分失败不会阻塞整轮。
