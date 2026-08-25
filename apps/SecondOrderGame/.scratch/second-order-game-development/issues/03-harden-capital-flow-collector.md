# 03 — 实现资金流向采集任务

**What to build:** 在系统基础设施落地后，通过统一数据接缝运行可恢复、可重复且可长期调度的资金流向采集任务。

**Blocked by:** 02 — 建立统一市场数据接缝.

**Status:** ready-for-human

- [x] 采集任务通过统一数据边界运行，不要求系统开发期间提前连接或采集。
- [x] 中途失败后可重跑且不重复写入，遗漏股票和失败原因可追踪。
- [x] 离线测试覆盖 40 个交易日窗口、幂等写入、空值拒绝和部分失败恢复。
- [x] 默认采集范围为用户自选池约 10–20 只股票及约 10–20 个在册板块，不扫描全市场。
- [x] 采集任务提供收盘调度 CLI（`secondordergame-capital-flow-daily`）与启动补跑；真实采集仍在系统正式落地后按调度启动，不以“立即采集”作为验收前置条件。

## Comments

2026-08-16：实现交付——ledger 增加 `flows_for`/`latest_date_for` 查询；新增 `src/data/capital_flow_daily.py`（交易日历窗口 + CLI + 启动补跑）；`nightly.py` 可选注入资金流到个股标注器；`production_context.py` 可选注入 `materials.capital_flow`。窗口由 100–120 调整为固定 40 个交易日（用户决策）。

2026-08-16（落地）：系统已落地，真实采集已开启——PA 自选池 16 标的（watchlist.json 同步进 `runtime/data/capital_flow_scope.json`）首轮 40 交易日入库 615 行；富途资金流接口实测约 10 次/30 秒配额 → 采集改为按代码批量拉取（新增 `get_capital_flow_range`，1 调用/代码）+ 8 次/30 秒阻塞式节流（`PRODUCTION_RATE_LIMITER`）；失败表按 (code, date) 保留最新原因（`record_failure` 替换语义 + 数据到位后 `clear_failures`）。C长鑫 688825 上市前 25 日无数据为真实缺口。程序打开自动补采集已生效（scope 文件 → 已最新则跳过）。

## Required context

- `AGENTS.md`、`CONTEXT.md`、`ARCHITECTURE.md`、`docs/ROADMAP.md`
- `docs/adr/0008-pipeline-in-memory-no-intermediate-writes.md`
- `docs/adr/0018-w-matrix-collapse-to-2participant.md`
- `docs/adr/0020-test-seams-and-fixtures.md`
- 先读取 02 的实际交付；可以自行探索 `src/data/`、`tests/` 和自选池来源，但不得重新定义第二套市场数据接口。

## Files in scope

- 创建：`src/data/capital_flow_ledger.py`、`tests/test_capital_flow_ledger.py`
- 修改：`src/data/__init__.py`
- 复用：`src/data/protocol.py`、`src/data/models.py`、`src/data/fake_client.py`
- 参考：`config/sectors.yaml`、`tests/conftest.py`
- 不修改：已取消的 `01-start-capital-flow-capture.md`

## Constraints and non-goals

- 历史窗口固定在 40 个交易日，不恢复旧的 242 天假设。
- 默认只覆盖约 10–20 只自选股和约 10–20 个相关在册板块，不扫描全市场或活跃 movers。
- 系统落地并通过离线测试后才启动真实采集；不存在“先跑生产、后补测试”的例外。
- 本票不负责自选池 UI、板块筛选算法、概率更新或长期数据仓库。

## Verification

在 `SecondOrderGame/` 根目录运行：

```powershell
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/test_capital_flow_ledger.py -v
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/ -v
```

在线 OpenD 验收只在离线测试全部通过后执行，并记录股票数、板块数、耗时、失败项和数据库增长量。
