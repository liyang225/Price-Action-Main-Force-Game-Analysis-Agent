# 板块情绪周期规则实证验证包

本目录保存板块周期 v1 的可复现冻结证据，但不把机器标签当作人工真值。ADR-0021 已将周期位置改为冰点、启动、发酵、高潮、退潮；分歧是正交的板块共识状态，不是人工周期标签。

## 当前数据

- `sector_codes.csv`：12 个经真实富途接口成功拉取的行业板块。代码来自 2026-08-10 的真实采集清单，不沿用 ADR 附录里的猜测代码。
- `sector_ohlcv.csv.gz`：12 个板块各 1,600 根日线，共 19,200 行，范围为 2020-01-02 至 2026-08-10。
- `collection-manifest.json`：数据哈希及上游真实采集 manifest 的来源指针。上游采集共 73 个目标，成功 73、失败 0。
- `annotation_sheet_legacy.csv`：旧状态空间盲标表的只读历史档案。它按旧状态空间抽样，不能用于新五阶段的最终冻结验证。
- `annotation_sheet_v1.csv`：当前五阶段的 75 条新盲标表，人工填写后才进入 T08 对比。
- `annotation-sampling-manifest.json`：记录旧表的状态空间和作废原因。
- `annotation_sheet_v1-manifest.json`：记录当前五阶段表的抽样状态空间、分层计数、生成器版本、规则配置哈希和输入数据哈希。
- `reports/validation-report.md`：数据质量与人工/机器对比结果。
- `rule_manifest.json`：冻结版本、规则哈希、输入证据哈希、人工混淆和全历史覆盖摘要。

`sector_codes.csv` 的 `missing_rate` 是相对自然工作日的保守缺失率（7.19%），其中包含法定休市日，因此不是接口丢数率。12 个板块在真实交易日上的日期集合完全一致。

## 人工标注记录

75 条人工标注已经完成。标注时只查看 `sector_ohlcv.csv.gz` 中目标日及其前后验证窗口，不读取 `config/sentiment.yaml`、板块情绪指数或机器结果。允许标签仅为冰点、启动、发酵、高潮、退潮；旧含分歧表没有计入冻结样本。

抽样器内部用 version 0 草案让五类候选形态各占 15 条，但 `annotation_sheet_v1.csv` 不写机器分层，保持人工判断盲态。

## 命令

查看帮助：

```powershell
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe .scratch/sector-labeler-validation/validate_sector_rules.py --help
```

用当前数据重新生成 75 条均衡标注表（不会覆盖已有人工标签）：

```powershell
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe .scratch/sector-labeler-validation/validate_sector_rules.py sample --total 75 --seed 20260810
```

重新生成分布、混淆矩阵和系统性偏差摘要：

```powershell
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe .scratch/sector-labeler-validation/validate_sector_rules.py compare --json
```

默认输入就是当前的 `annotation_sheet_v1.csv`。旧档案不会被默认读取；如需审计历史表，必须显式指定 `--input annotation_sheet_legacy.csv`。

需要重新在线采集时，先确保 Futu OpenD 可用，再运行：

```powershell
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe .scratch/sector-labeler-validation/validate_sector_rules.py collect --candidates .scratch/sector-labeler-validation/sector_candidates.csv --start 2023-08-10 --end 2026-08-10
```

在线采集逐代码调用统一的 `FutuMarketDataSource.get_kline(..., "K_DAY", ...)`。接口错误、空历史、少于两年覆盖的代码写入核验记录但不会进入 `sector_ohlcv.csv.gz`；少于五个板块通过时命令拒绝交付。

## 对比输出语义

- 机器未命中保持 `unlabeled`，不兜底为任何周期状态。
- 前视窗口、特征窗口或必需 OHLCV 字段不完整记为 `data_insufficient`，不混入未命中率。
- 混淆矩阵以人工标签为行、机器标签为列，并保留 `unlabeled` 与 `data_insufficient` 两列。
- 当某一状态的机器占比相对人工占比偏差至少 10 个百分点时，列入 `systematic_biases`。
- 工具只读取板块指数 OHLCV；输入中出现 `sentiment` 或「情绪」字段会直接拒绝。

冻结结论为 version 1，规范化规则哈希见 `config/sector_labeler.yaml` 和 `rule_manifest.json`。修改任何规则内容都会导致冻结校验失败，必须走新版本、全量重标和独立 C 计数重建流程。

## 测试

```powershell
C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/test_sector_validation_tools.py -v
```
