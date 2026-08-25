# 14 — 估计 T+1 首次触及概率

**What to build:** 按条件单元估计下一交易日先触及目标位或止损位的概率，并在样本不足时安全降维或拒绝给数。

**Blocked by:** 02 — 建立统一市场数据接缝; 12 — 统一概率结果契约.

**Status:** ready-for-agent

- [ ] 条件单元至少包含波动率、换手率和情绪周期位置维度。
- [ ] 样本不足时按预定顺序降维，仍不足则只返回数据不足状态。
- [ ] 任意股票在三秒内完成，且不存在“低置信度概率”分支。

## Required context

- `AGENTS.md`、`CONTEXT.md`、`ARCHITECTURE.md`、`docs/ROADMAP.md`
- `docs/adr/0003-two-decision-points-per-day.md`
- `docs/adr/0021-sector-cycle-and-consensus-model.md`
- `docs/adr/0020-test-seams-and-fixtures.md`
- 先读 T02、T12 的实现；可以探索波动率、换手率和情绪周期字段的现有来源，但不得临时改写这些字段语义。

## Files in scope

- 创建：`src/probability/t1_first_passage.py`、`tests/test_t1_first_passage.py`
- 修改：`src/probability/__init__.py`
- 复用：`src/probability/models.py`、`src/data/protocol.py`、`src/data/fake_client.py`
- 参考：`src/labeler_constants.py`、`tests/conftest.py`

## Constraints and non-goals

- 条件单元和降维顺序必须显式、确定且可审计。
- 样本不足时不得外推、平滑出伪概率或返回“低置信度数字”。
- 本票不生成开盘区间分布、不做 T+1 动作判断、不使用前视信息进入实时特征。
- 情绪周期位置缺失时必须进入已定义的降维或数据不足分支。
- 条件单元的周期位置只能是冰点、启动、发酵、高潮、退潮；分歧及其方向是独立板块共识信息，不得伪装为该维度的第六个取值。

## Verification

在 `SecondOrderGame/` 根目录运行：

```powershell
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/test_t1_first_passage.py tests/test_probability_contract.py -v
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/ -v
```

测试覆盖完整条件单元、逐级降维、仍不足、目标先触及、止损先触及和同根歧义规则。
