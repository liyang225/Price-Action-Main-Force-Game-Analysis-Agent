# 研究数据底座

该工具只位于 `.scratch/business-rules/research/`，不依赖项目 `src/`。公开入口是：

- `load_research_config(path)`：安全读取并校验 YAML。
- `HistoryProvider.fetch_history(request)`：历史数据供应者边界。
- `replay(config, provider) -> ReplayReport`：按「标的 × 交易日」统计标签、冲突与未命中。
- `python -m research_harness replay --config ...`：命令行回放。

## 使用

在本目录下执行：

```powershell
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m research_harness replay --config example_config.yaml --format json --output replay.json
```

如需保留每个「标的 × 交易日」的命中明细，加 `--include-matches`。改变阈值只修改 YAML 中对应规则的 `value`。

测量配置中每个标的在日线与 `K_120M` 上的可用历史深度：

```powershell
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m research_harness depth --config example_config.yaml --start 1990-01-01 --format markdown --output depth.md
```

`depth` 默认同时测量 `day` 和 `120m`；可用 `--period day` 或 `--period 120m` 限制周期。它逐组合记录富途实际返回的最早日、最新日、行数、交易日数、分页数、调用用时和接口错误；单个组合失败不会抹掉其他组合的结果。

## 规则格式

规则是结构化数据，不接受 Python 表达式，也不使用 `eval`。可使用 `all` / `any` / `not` 嵌套条件，叶子条件格式为：

```yaml
field: close
op: gte
value: 10.0
```

可用操作符：`eq` / `ne` / `gt` / `gte` / `lt` / `lte` / `between` / `in` / `not_in` / `is_null` / `not_null`。日线一日一行；`K_120M` 同日多行分别判定后，在 `(code, trading_date)` 层面合并标签，所以同日重复命中同一标签只计一次。

## 富途连接

Futu 适配器延迟导入 `futu-api`，离线测试不需要安装它。实测前需要启动并登录 OpenD，且账户必须具备相应市场的历史行情权限。适配器对 `request_history_kline` 持续翻页，并显式映射 `day -> K_DAY`、`120m -> K_120M`。`page_delay_seconds` 可用于按当前 OpenAPI 限频要求节流。

实时测试默认跳过，只在用户明确开启时运行：

```powershell
$env:RUN_FUTU_LIVE_TESTS = "1"
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/test_live_futu.py
```
