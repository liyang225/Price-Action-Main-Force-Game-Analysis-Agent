# 18 — 计算完整博弈信号

**What to build:** 从 K_120M 行情按冻结配置计算纳什均衡带、羊群行为、聪明钱、流动性陷阱及逆势、动量和回归观测特征。

**Blocked by:** 02 — 建立统一市场数据接缝; 12 — 统一概率结果契约.

**Status:** ready-for-agent

- [ ] 所有周期、阈值和可空参数来自配置，不在计算中发明或硬编码参数。
- [ ] 一字板和窗口不足返回数据不足，不返回 0 或沿用前值。
- [ ] 信号只作为观测特征，不合成买卖点、仓位或概率数字。
- [ ] 离线测试覆盖冻结参数语义及主要边界条件。

## Required context

- `AGENTS.md`、`CONTEXT.md`、`ARCHITECTURE.md`、`docs/ROADMAP.md`
- `docs/adr/0004-use-k120m-not-k240m.md`
- `docs/adr/0009-game-signals-program-computed.md`
- `docs/adr/0016-signal-parameters-from-reference-doc.md`
- `docs/adr/0020-test-seams-and-fixtures.md`
- 可自行探索配置校验测试和参考资料，但以已冻结配置/ADR 语义为准，不从外部文档重新取参数。

## Files in scope

- 创建：`src/signals/__init__.py`、`src/signals/game_signals.py`
- 创建：`tests/test_game_signals.py`
- 复用：`src/probability/models.py` 的数据不足契约、`src/data/models.py`
- 参考：`config/signals.yaml`、`src/config_validator.py`、`tests/test_config_signals_labeler.py`

## Constraints and non-goals

- 配置周期已经按 K_120M ×2 换算，不得再次乘二或改回参考文档日数。
- `nash.deviation` 是标准差倍数；配置中的 null 保持纯布尔条件，不自行填值。
- 信号是观测特征，不合成买卖点、仓位或主力行为标签。
- `high == low` 与窗口不足返回数据不足，不返回 0 或旧值。

## Verification

在 `SecondOrderGame/` 根目录运行：

```powershell
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/test_game_signals.py tests/test_config_signals_labeler.py -v
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/ -v
```

测试覆盖每类信号、心理价位、配置 null、一字板、窗口不足和“不得输出仓位”。
