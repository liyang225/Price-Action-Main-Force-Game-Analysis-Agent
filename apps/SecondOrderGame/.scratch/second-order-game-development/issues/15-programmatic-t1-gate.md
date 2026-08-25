# 15 — 计算程序化 T+1 闸门

**What to build:** 根据 B/C 类结果、持仓账龄和当前决策点，程序化计算允许执行的动作及 T+1 闸门状态。

**Blocked by:** 13 — 估计午盘与隔夜开盘区间分布; 14 — 估计 T+1 首次触及概率.

**Status:** ready-for-agent

- [ ] 午盘和收盘对同一输入使用各自正确的概率语义。
- [ ] 输出结构化可执行动作集合和通过、不通过或数据不足状态。
- [ ] 任一必要概率数据不足时闸门不通过，大模型不能覆盖该结论。

## Required context

- `AGENTS.md`、`CONTEXT.md`、`ARCHITECTURE.md`、`docs/ROADMAP.md`
- `docs/adr/0003-two-decision-points-per-day.md`
- `docs/adr/0010-pa-integration-t0-t1-modes.md`
- `docs/adr/0015-pa-integration-points.md`
- 先读 T12–T14 的公开契约；可以探索 PA 的动作枚举，但本票保持在 SecondOrderGame 内实现纯程序闸门。

## Files in scope

- 创建：`src/probability/t1_gate.py`、`tests/test_t1_gate.py`
- 修改：`src/probability/__init__.py`
- 复用：`src/probability/models.py`、`src/probability/opening_distribution.py`、`src/probability/t1_first_passage.py`
- 参考：`CONTEXT.md` 中“可执行动作集合”和“决策点”

## Constraints and non-goals

- 闸门由程序决定，不接受大模型的买/不买意见覆盖。
- 午盘、收盘和持仓账龄共同决定动作集合，不能只看一个概率阈值。
- 数据不足必须导致不通过；不把它当作中性或允许继续。
- 本票不改 PA 下单代码、不调 PA 胜率和盈亏比。

## Verification

在 `SecondOrderGame/` 根目录运行：

```powershell
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/test_t1_gate.py tests/test_opening_distribution.py tests/test_t1_first_passage.py -v
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/ -v
```

测试矩阵至少覆盖两个决策点、不同持仓账龄、B/C 数据不足和动作集合边界。
