# 12 — 统一概率结果契约

**What to build:** 为所有概率能力定义一致的有效结果和数据不足结果，使调用方始终看见先验权重、数据来源和可执行状态。

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] 有效概率携带 0–1 范围的先验权重和所用配置版本。
- [ ] 样本不足以独立状态返回，不包含带“低置信度”警告的概率数字。
- [ ] 契约可被 B 类、C 类、A 类和 UI 共同消费，序列化后语义不丢失。

## Required context

- `AGENTS.md`、`CONTEXT.md`、`ARCHITECTURE.md`、`docs/ROADMAP.md`
- `docs/adr/0001-no-learned-markov-chain.md`
- `docs/adr/0003-two-decision-points-per-day.md`
- `docs/adr/0020-test-seams-and-fixtures.md`
- 可自行探索 HMM 现有返回值和未来 UI/PA 消费需求；优先定义最小稳定契约，不提前实现概率算法。

## Files in scope

- 创建：`src/probability/__init__.py`、`src/probability/models.py`
- 创建：`tests/test_probability_contract.py`
- 参考：`src/hmm_filter.py`、`config/hmm_prior.yaml`、`src/labeler_constants.py`
- 参考文档：`CONTEXT.md` 中“先验权重”“数据不足”“决策点”定义

## Constraints and non-goals

- 有效结果和数据不足必须是可区分结构，不用 `None`、NaN 或警告字符串混充状态。
- 概率值、先验权重、配置版本和决策点语义必须可序列化。
- 不在本票实现 B/C/A 算法、T+1 闸门或 UI。
- 不使用“置信度”替代“先验权重”。

## Verification

在 `SecondOrderGame/` 根目录运行：

```powershell
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/test_probability_contract.py -v
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/ -v
```

测试需覆盖有效结果、数据不足、非法概率、非法先验权重和序列化往返。
