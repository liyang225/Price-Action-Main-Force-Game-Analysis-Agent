# 16 — 追踪预测结果与校准度

**What to build:** 保存每次概率预测及随后发生的实际结果，在样本量足够后计算校准度并指出先验应调整的方向。

**Blocked by:** 13 — 估计午盘与隔夜开盘区间分布; 14 — 估计 T+1 首次触及概率.

**Status:** ready-for-agent

- [ ] 预测记录包含概率类型、决策点、配置版本、先验权重和实际结果。
- [ ] 达到阈值后可计算 Brier score，阈值前返回数据不足而非评分。
- [ ] 评估作为离线流程运行，不改变实时推演中的概率。

## Required context

- `AGENTS.md`、`CONTEXT.md`、`ARCHITECTURE.md`、`docs/ROADMAP.md`
- `docs/adr/0001-no-learned-markov-chain.md`
- `docs/adr/0008-pipeline-in-memory-no-intermediate-writes.md`
- 先读 T12–T14 输出契约；可以探索未来分析记录格式，但校准记录必须保持独立可审计。

## Files in scope

- 创建：`src/calibration/__init__.py`、`src/calibration/tracker.py`
- 创建：`tests/test_calibration_tracker.py`
- 复用：`src/probability/models.py`、`tests/conftest.py` 的临时 SQLite 夹具
- 参考：`config/hmm_prior.yaml`

## Constraints and non-goals

- 只计算并记录校准度，不做在线学习、自动改先验或回写 HMM 配置。
- 样本阈值前返回数据不足，不展示可能误导的 Brier score。
- 预测记录必须绑定配置版本和先验权重，不能用当前配置解释历史预测。
- 本票不做交易回测、收益归因或 UI 图表。

## Verification

在 `SecondOrderGame/` 根目录运行：

```powershell
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/test_calibration_tracker.py -v
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/ -v
```

测试覆盖预测/结果配对、重复结果、阈值前后、版本隔离和已知 Brier score 样例。
