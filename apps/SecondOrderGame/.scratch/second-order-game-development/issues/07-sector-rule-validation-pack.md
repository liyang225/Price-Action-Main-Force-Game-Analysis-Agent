# 07 — 建立板块规则实证验证包

**What to build:** 准备板块情绪周期规则的实证材料，包括已核验代码、两年历史行情、均衡人工标注样本和机器规则对比工具。

**Blocked by:** 02 — 建立统一市场数据接缝.

**Status:** ready-for-agent

- [ ] 至少五个不同行业板块的代码经真实接口拉取验证，错误代码不会进入样本。
- [ ] 每个板块覆盖至少两年日线数据，并生成 50–100 个跨五档均衡的待标注日期。
- [ ] 工具能读取人工标签，输出机器标签分布、混淆矩阵和系统性偏差摘要。

## Required context

- `AGENTS.md`、`CONTEXT.md`、`ARCHITECTURE.md`、`docs/ROADMAP.md`
- `docs/adr/0007-labeler-day0-independent-freeze.md`
- `docs/adr/0021-sector-cycle-and-consensus-model.md`
- `docs/adr/0019-sector-labeler-draft-rules.md`
- `docs/adr/0020-test-seams-and-fixtures.md`
- 可自行探索 `.scratch/business-rules/` 的既有研究脚本与报告，优先复用其数据清洗和 manifest 习惯。

## Files in scope

- 创建：`.scratch/sector-labeler-validation/README.md`
- 创建：`.scratch/sector-labeler-validation/sector_codes.csv`、`sector_ohlcv.csv.gz`、`annotation_sheet_v1.csv`
- 保留：`.scratch/sector-labeler-validation/annotation_sheet_legacy.csv`（旧含分歧抽样表，仅历史审计）
- 创建：`.scratch/sector-labeler-validation/validate_sector_rules.py`、`tests/test_sector_validation_tools.py`
- 创建：`.scratch/sector-labeler-validation/reports/validation-report.md`
- 参考：`config/sector_labeler.yaml`、`config/sectors.yaml`
- 参考研究：`.scratch/business-rules/reports/`、`.scratch/business-rules/experiments/output/`

## Constraints and non-goals

- 板块代码必须通过真实接口核验，不能沿用 ADR 附录中的未核对猜测。
- 标注输入只能使用板块指数 OHLCV；禁止读取板块情绪指数。
- 本票只准备验证包和比较工具，不替代 T08 的人工标签判断，也不直接冻结 version 1。
- 新表只使用冰点、启动、发酵、高潮、退潮；分歧不进入周期标签，候选分层不得回用旧分歧样本。
- 大型原始数据留在 `.scratch/`，不得提交到正常测试夹具目录。

## Verification

在 `SecondOrderGame/` 根目录运行：

```powershell
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/test_sector_validation_tools.py -v
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe .scratch/sector-labeler-validation/validate_sector_rules.py --help
```

交付时附五个以上已核验板块、日期范围、缺失率和待人工标注数量。
