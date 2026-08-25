# 42 — 实现板块标注器 v2 影子运行与自动切换

**What to build:** 按 ADR-0021 持续采集板块成分股扩散证据，让 v2 标注器与 v1 并行影子运行；达到已冻结门槛后自动全量重标、重建独立 C 计数并原子切换生产版本。

**Blocked by:** 09 — 实现板块层事后标注器.

**Status:** ready-for-human

- [x] 按证券代码把 AkShare 个股数据映射到富途板块成分股，不使用 AkShare 行业字符串代替板块归属。
- [x] 每板块每日保存最高连板高度、达到该高度的股票数及成分股占比、涨停数、跌停数、涨跌停平衡度/活跃度和相对前五日成交量。
- [x] 独立输出 `consensus_state`（一致/分歧）与 `consensus_direction`（转强/转弱/未确认）；二者不得覆盖或派生为 HMM 周期位置。
- [x] 分母为零或字段缺失时返回 `data_insufficient`，不回退到 v1 标签，也不写生产 C 计数。
- [x] v2 在影子期间使用独立规则哈希、标签和 C 计数；生产输出继续来自 v1。
- [x] 每个在册板块同时满足 5 个合格交易日、连续 3 个交易日无结构性错误后触发自动切换；合格日定义已保证必需字段完整。
- [x] 切换前全量重标所有可用历史并重建 v2 独立 C 计数；保存切换报告后原子切换，失败时生产版本保持 v1。

## Comments

- 2026-08-12：作为压缩交付包 B 完成。新增独立 v2 配置/哈希、富途成分股与 AkShare 涨跌停池数据接缝、成分股扩散指标、SQLite 影子状态、5+3 门槛、全量重标/C 重建与原子切换回滚；报告或活动指针发布失败会清理 release/report，生产指针保持 v1。

## Required context

- `AGENTS.md`、`CONTEXT.md`、`ARCHITECTURE.md`、`docs/ROADMAP.md`
- `docs/adr/0007-labeler-day0-independent-freeze.md`
- `docs/adr/0021-sector-cycle-and-consensus-model.md`
- `docs/adr/0022-sector-cycle-is-not-board-count-clock.md`
- `config/sector_labeler.yaml` 的 `shadow_v2` 门槛
- T09 的 v1 标注器、规则哈希和持久化契约
- 富途板块成分股接口文档、AkShare 个股涨跌停/连板数据接口文档

## Files in scope

- 创建：`src/labeler/sector_labeler_v2.py`、`src/labeler/shadow_cutover.py`
- 创建：v2 指标与影子状态的持久化模块（位置按现有数据层结构确定）
- 创建：`tests/test_sector_labeler_v2.py`、`tests/test_shadow_cutover.py`
- 修改：`src/data/` 的统一数据接缝和 fake 实现，仅增加 v2 所需方法
- 修改：`src/config_validator.py`，校验 v2 指标和切换配置

## Constraints and non-goals

- 分歧是板块级正交共识状态，不新增为 HMM 周期位置。
- 最高连板不能由单只股票独立决定标签，必须同时保存达到高度的数量和成分股占比。
- 连板高度和连续大阳线是相对强度证据，不得写成固定“几板即高潮”的阶段开关；少量极强连板可进入高潮。高位兑现后可从高潮直接转退潮，不得要求最短高潮持续日数或强制中间状态。
- 不保存原始“涨跌停比”；保存原始计数，并按 ADR-0021 计算平衡度和活跃度。
- 不混用 v1/v2 哈希、标签或 C 计数，不在切换失败时产生半切换状态。
- 本票不修改已冻结的 v1 人工标签和阈值。

## Verification

在 `SecondOrderGame/` 根目录运行：

```powershell
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/test_sector_labeler_v2.py tests/test_shadow_cutover.py -v
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/ -v
```

必须覆盖门槛差一个板块/一天/完整率时不切换、分母为零、全量重标失败、C 重建失败和原子切换回滚。
