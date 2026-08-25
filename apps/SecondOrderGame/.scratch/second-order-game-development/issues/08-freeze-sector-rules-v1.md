# 08 — 人工验证并冻结板块周期 v1 规则

**What to build:** 由领域负责人标注板块日、检查机器规则偏差并调整阈值，最终把板块情绪周期规则冻结为首个生产版本。v1 仅使用板块指数 OHLCV；它以价格趋势代理判定发酵，不把尚未积累的成分股扩散数据伪装成历史事实。

**Blocked by:** 07 — 建立板块规则实证验证包.

**Status:** ready-for-agent

- [x] 新五阶段盲标表中的 50–100 个板块日已人工标注，冰点/启动/发酵/高潮/退潮各有足够样本且覆盖牛熊震荡时期；旧含分歧表不得计入完成数。
- [x] 调整后的规则具有合理的五档覆盖和可解释混淆，不以追求样本内完全一致为目标。
- [x] v1 缺少回看窗口、前视窗口或必需 OHLCV 字段时返回 `data_insufficient`，不输出标签、不更新 C 计数。
- [x] 规则版本升级为 1，规则哈希和验证报告一并保存。
- [x] v2 的影子运行规则和自动切换门槛已独立冻结：每个在册板块 5 个合格交易日、连续 3 个交易日无结构性错误；合格日已保证必需字段完整；切换执行全量重标、C 计数重建和原子切换。

## Completion record

- 冻结日期：2026-08-12
- 冻结版本：1
- 规则哈希：`b36718dcbc61cedbacd982c98ded6c09331274ae2c0951c96e84580fc455bb8f`
- 人工样本：75 条，五类分别为冰点 13、启动 17、发酵 14、高潮 15、退潮 16。
- 人机一致率：56%；主要混淆为启动→发酵 8 条、发酵→启动 6 条、冰点→退潮 5 条、退潮→冰点 4 条。
- 阈值结论：保留验证前规则，不用 75 条样本回拟合；完整证据见验证报告和 `rule_manifest.json`。

## Required context

- `AGENTS.md`、`CONTEXT.md`、`ARCHITECTURE.md`、`docs/ROADMAP.md`
- `docs/adr/0007-labeler-day0-independent-freeze.md`
- `docs/adr/0021-sector-cycle-and-consensus-model.md`
- `docs/adr/0022-sector-cycle-is-not-board-count-clock.md`
- `docs/adr/0019-sector-labeler-draft-rules.md`
- T07 的完整验证包及其 `README.md`、历史人工标注档案、新五阶段人工标注表、机器对比报告
- 可以自行查看 `.scratch/business-rules/reports/` 中个股规则冻结过程作为方法参考，但不得复制个股阈值到板块层。

## Files in scope

- 修改：`config/sector_labeler.yaml`
- 创建：`.scratch/sector-labeler-validation/annotation_sheet_v1.csv`
- 保留：`.scratch/sector-labeler-validation/annotation_sheet_legacy.csv`（只读历史档案）
- 修改：`.scratch/sector-labeler-validation/reports/validation-report.md`
- 创建：`.scratch/sector-labeler-validation/rule_manifest.json`
- 如结论改变 ADR 草案：新增后续 ADR；不要直接把草案历史改写成看似最初就已确定

## Constraints and non-goals

- 这是人工决策票，工程师可以运行工具和整理证据，但不得替用户代填“凭直觉判断”的人工标签。
- 不追求样本内 100% 一致，不用情绪指数切五段，不把五档改回八档。
- 阈值调整必须有标注分布和混淆证据；不能为满足目标覆盖率而无依据调参。
- 分歧是与周期位置正交的板块级共识状态，不得作为 v1 的周期标签。既有旧抽样表含分歧层，完成新样本表前不得用于冻结验证。
- v1 不得读取或伪造 v2 的成分股扩散指标；v2 不得在数据不足时回退到 v1 标签。
- v1 的 OHLCV 价格代理不得把连续大阳线数量硬编码为高潮或退潮持续时间；v2 的连板证据也不得按固定板数机械映射阶段。
- 不实现生产标注器代码，生产实现属于 T09。

## Verification

在 `SecondOrderGame/` 根目录运行：

```powershell
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe .scratch/sector-labeler-validation/validate_sector_rules.py --check-frozen-config
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/test_sector_validation_tools.py -v
```

完成时必须由用户确认人工标签与冻结阈值，并记录 version、规则哈希和各档混淆摘要。
