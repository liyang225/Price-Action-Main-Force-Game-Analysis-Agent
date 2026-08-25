# 13 — 估计午盘与隔夜开盘区间分布

**What to build:** 根据历史价格分别估计午盘下一根 K 线和隔夜下一交易日的开盘区间分布，使两种决策语义互不污染。

**Blocked by:** 02 — 建立统一市场数据接缝; 12 — 统一概率结果契约.

**Status:** ready-for-agent

- [ ] 午盘与隔夜使用独立样本和独立分布，更新其中一套不会改变另一套。
- [ ] 使用分层日块 bootstrap，结果为合法概率分布并携带先验权重。
- [ ] 任意股票在三秒内返回结果或明确的数据不足状态。

## Required context

- `AGENTS.md`、`CONTEXT.md`、`ARCHITECTURE.md`、`docs/ROADMAP.md`
- `docs/adr/0003-two-decision-points-per-day.md`
- `docs/adr/0004-use-k120m-not-k240m.md`
- `docs/adr/0020-test-seams-and-fixtures.md`
- 先读 T02、T12 的实际接口；可以探索历史行情形状和已有统计工具，但不要引入训练型模型。

## Files in scope

- 创建：`src/probability/opening_distribution.py`、`tests/test_opening_distribution.py`
- 修改：`src/probability/__init__.py`
- 复用：`src/probability/models.py`、`src/data/protocol.py`、`src/data/fake_client.py`
- 参考：`tests/conftest.py`、`config/hmm_prior.yaml`

## Constraints and non-goals

- 午盘使用 `intraday_next_bar`，收盘使用 `overnight_next_bar`，样本、缓存和输出标识不得共用。
- 使用分层日块 bootstrap，不把独立 K 线随机打散。
- 本票不估计 T+1 首次触及概率、不做闸门、不调用大模型。
- 样本不足返回统一状态，不能给带警告的数字。

## Verification

在 `SecondOrderGame/` 根目录运行：

```powershell
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/test_opening_distribution.py tests/test_probability_contract.py -v
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/ -v
```

测试必须证明两套分布互不污染、概率归一、块结构保留和三秒性能边界。
