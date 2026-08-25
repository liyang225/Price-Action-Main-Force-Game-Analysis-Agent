# 17 — 把 HMM 前向滤波接入板块状态流

**What to build:** 为每个板块维护独立 HMM 信念，在每根 K_120M 收盘后接收离散观测并将新信念同步到板块状态。

**Blocked by:** 02 — 建立统一市场数据接缝; 05 — 持久化板块情绪台账.

**Status:** ready-for-agent

- [ ] 不同板块具有互不共享的滤波实例和信念状态。
- [ ] 只在完整 K_120M 收盘事件上更新，重启后能继续此前信念。
- [ ] 更新只执行前向滤波，不训练或重估 HMM 参数。

## Required context

- `AGENTS.md`、`CONTEXT.md`、`ARCHITECTURE.md`、`docs/ROADMAP.md`
- `docs/adr/0001-no-learned-markov-chain.md`
- `docs/adr/0003-two-decision-points-per-day.md`
- `docs/adr/0004-use-k120m-not-k240m.md`
- `docs/adr/0018-w-matrix-collapse-to-2participant.md`
- 先读现有 `HMMFilter` 和 T05 台账接口；可自行探索 K 线完成事件来源，但不要引入训练循环。

## Files in scope

- 创建：`src/reasoning/__init__.py`、`src/reasoning/belief_updater.py`
- 创建：`tests/test_belief_updater.py`
- 复用：`src/hmm_filter.py`、`src/data/sentiment_ledger.py`、`src/data/protocol.py`
- 参考：`config/hmm_prior.yaml`、`tests/test_hmm_engine.py`

## Constraints and non-goals

- 每板块一个滤波实例，禁止跨板块共享 belief。
- 只在完整 K_120M 收盘事件更新；午盘/收盘的业务决策不能制造额外滤波步。
- 不运行 Baum-Welch、MS-GARCH 或任何参数训练。
- 本票不调用大模型、不计算信号、不生成情景应对树。

## Verification

在 `SecondOrderGame/` 根目录运行：

```powershell
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/test_belief_updater.py tests/test_hmm_engine.py tests/test_sentiment_ledger.py -v
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/ -v
```

测试需证明板块隔离、重启恢复、重复事件幂等和非完整 K 线不更新。
