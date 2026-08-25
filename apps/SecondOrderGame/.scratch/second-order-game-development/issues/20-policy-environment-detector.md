# 20 — 检测政策环境

**What to build:** 综合宽基 ETF 异常放量和新闻证据识别政策环境，并选择对应的概率乘数。

**Blocked by:** 02 — 建立统一市场数据接缝; 06 — 管理分析材料缓存生命周期; 11 — 运行消息后台预取与 Tavily 降级.

**Status:** ready-for-agent

- [ ] 硬信号和软信号均可独立触发可解释的政策环境结果。
- [ ] 缺少软信号且未提供用户材料时关闭对应环节，不臆造政策结论。
- [ ] 政策环境只选择概率乘数，不重置 HMM 状态或改变推演流程。

## Required context

- `AGENTS.md`、`CONTEXT.md`、`ARCHITECTURE.md`、`docs/ROADMAP.md`
- `docs/adr/0001-no-learned-markov-chain.md`
- `docs/adr/0006-news-background-prefetch.md`
- `docs/adr/0009-game-signals-program-computed.md`
- 先读取 T02、T06、T11 的实际接口和 HMM 配置中的政策乘数；可探索宽基 ETF 代码来源，但不能硬编码未经核验的代码。

## Files in scope

- 创建：`src/reasoning/policy_detector.py`、`tests/test_policy_detector.py`
- 修改：`src/reasoning/__init__.py`
- 复用：`src/data/protocol.py`、`src/data/daily_cache.py`、`src/data/models.py`
- 参考：`config/hmm_prior.yaml`、`config/sectors.yaml`

## Constraints and non-goals

- 国家队是政策环境，不是第三个参与者；不得扩大 W 矩阵。
- 硬信号来自已核验 ETF 行情，软信号来自缓存新闻或用户材料。
- 无软信号时关闭该环节，不由模型补写新闻事实。
- 本票只选择已有 `policy_multipliers`，不改 HMM 结构或另建推演分支。

## Verification

在 `SecondOrderGame/` 根目录运行：

```powershell
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/test_policy_detector.py tests/test_daily_cache.py tests/test_news_prefetch.py -v
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/ -v
```

测试覆盖四种政策环境、硬/软信号冲突、无新闻、接口失败和“不得重置 belief”。
