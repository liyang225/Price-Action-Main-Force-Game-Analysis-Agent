# 04 — 启动时强制校验配置

**What to build:** 让每次应用启动都先校验全部已启用配置，错误时拒绝继续运行并给出可定位的问题说明。

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] 合法配置允许进程继续启动。
- [ ] 任一配置非法时启动失败，不静默采用默认值或旧值。
- [ ] 错误信息包含配置来源和具体约束，现有配置校验回归测试全部通过。

## Required context

- `AGENTS.md`、`CONTEXT.md`、`ARCHITECTURE.md`、`docs/ROADMAP.md`
- `docs/adr/0001-no-learned-markov-chain.md`
- `docs/adr/0005-routing-pure-function-config-table.md`
- `docs/adr/0016-signal-parameters-from-reference-doc.md`
- `docs/adr/0017-labeler-rules-frozen.md`
- 可自行探索所有启动入口和配置消费方；若发现多个入口，必须让它们复用同一个初始化函数，而不是复制校验调用。

## Files in scope

- 创建：`src/app_init.py`、`tests/test_app_init.py`
- 修改：`src/config_validator.py`
- 参考：`config/hmm_prior.yaml`、`config/signals.yaml`、`config/labeler.yaml`、`config/sector_labeler.yaml`、`config/sectors.yaml`、`config/sentiment.yaml`
- 参考测试：`tests/test_hmm_engine.py`、`tests/test_config_signals_labeler.py`

## Constraints and non-goals

- 校验失败必须 fail fast，不回退默认值、不跳过未知配置、不静默修正用户输入。
- 本票只建立统一启动校验入口；不发明新阈值，也不实现尚未创建模块的运行逻辑。
- 不修改已冻结配置值，除非修复明确的 schema 错误并同步版本规则。

## Verification

在 `SecondOrderGame/` 根目录运行：

```powershell
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/test_app_init.py tests/test_hmm_engine.py tests/test_config_signals_labeler.py -v
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/ -v
```

交付时至少演示一个合法启动和一个包含完整路径/错误原因的拒绝启动。
