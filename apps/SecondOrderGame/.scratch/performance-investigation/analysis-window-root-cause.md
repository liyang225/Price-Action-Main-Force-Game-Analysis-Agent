# 分析窗口加载与持续分析开窗：根因调查

调查范围：PA Agent 工作区的分析池持续分析、单个分析终端首次打开、异常退出/卡死。

调查方式：只读代码审查、现有运行日志/崩溃转储审查、当前配置规模盘点，以及针对开窗扇出的静态可重复审计命令。后续已实施多轮修复，并通过 Python 3.14 语法检查、二阶数据适配器/信号测试及 WorkspaceWindow 关键单测。

## 结论

当前最可能的主因不是单个指标计算慢，而是「一个分析池开关动作」被实现成了多个重量级终端和行情连接的同步扇出：

1. `_set_pool_tracking(True)` 在 UI 线程逐个调用 `_ensure_terminal()`，因此一次点击会同步创建分析池中的全部终端（已改为逐项队列）。
2. `_create_terminal()` 原先在同一条 UI 调用链中执行 `data_source.connect()`、`subscribe()`；这部分现已移到 `_TerminalDataSourceWorker`。
3. `MainWindow._build_workbench()` 原先还会同步构造完整 `SecondOrderWorkspace`；现已改为首次进入“二阶博弈”模块时懒加载，普通个股窗口不再支付这笔成本。
4. 工作区即使没有打开分析终端，也会由 `_WatchlistQuoteWorker` 为自选列表的每个品种创建独立数据源、连接并订阅。当前自选列表 16 个品种，分析池 3 个品种；全部是 Futu，因此点击持续分析后至少会形成约 19 个独立 Futu 上下文（16 个轻量报价上下文 + 3 个终端上下文）。
4. 每个启用持续分析的终端再启动一个独立 `RefreshLoop`。刷新间隔是 1 秒，且每个循环都拉取约 200 根 K 线；多个循环会同时向 OpenD 发起请求。
5. 首次分析还可能在刚启动 `RefreshLoop` 后立即启动 `SnapshotFetchWorker`，对同一个数据源再发起一次 `latest_snapshot()`，形成启动阶段的重复请求。
6. 终端初始化会同步刷新历史记录。当前历史分析目录约 96.6 MB、135 个 JSON；分析池三个品种对应约 32.4 MB。最大单品种 159732 有 33 个文件、约 23.3 MB。每个终端的历史面板都会重新扫描并反序列化所属品种的记录（首次刷新现已延迟）。
7. 连接或刷新调用阻塞时，停止循环只能等待固定超时并把线程标记为 zombie；日志已经出现大量 `RefreshLoop did not finish within 5000 ms; tracking as zombie`。这会使后续重启/切换继续积累线程和连接，最终出现卡死、Qt/COM 异常或进程退出。

这里的“分析窗口”在实现上是嵌入 `QTabWidget` 的 `MainWindow` 终端，不是独立 OS 顶层窗口；用户看到的“自动开很多窗口”实际是一次性创建多个分析页/终端。当前保存配置的分析池有 3 个项目；配置模型 `analysis_pool_tracking_on_start` 的默认值是 `True`，虽然当前 `settings.json` 保存值为 `false`，所以配置缺失或被重置时还存在启动即批量开终端的路径。

## 证据链

### A. 开关动作的同步扇出

- `WorkspaceWindow._set_pool_tracking()` 遍历 `analysis_pool_items()`，对每个项目同步 `_ensure_terminal()`。
- `_ensure_terminal()` 只有按 `item_id` 去重；没有排队、并发上限、取消或“只建轻量占位页”的状态。
- 静态审计命令已运行并通过，输出：

  `RED-CAPABLE FANOUT AUDIT: PASS; pool toggle creates one terminal per pool item; terminal creation sync-connects/subscribes; quote worker sync-connects/subscribes each watchlist source.`

### B. 终端创建阻塞 UI

此前 `_create_terminal()` 中，`data_source.connect()` 和 `data_source.subscribe()` 位于 `MainWindow(...)` 构造之前，调用方是 Qt UI 线程；现已改为后台 `_TerminalDataSourceWorker`。`MainWindow._setup_ui()` 创建完整图表、侧栏、历史面板和二阶工作区仍在 GUI 线程；历史记录首次刷新已延迟到首帧绘制之后。

### C. 未打开终端时已经存在的连接扇出

`_WatchlistQuoteWorker._source_for()` 对每一个自选项执行 `create_data_source → connect → subscribe`，并在后台每 3 秒遍历所有项目读取行情。注释称这些连接“lightweight”，但 Futu 的 `OpenQuoteContext` 本身会创建网络/回调线程；它不是零成本读取。

### D. 独立刷新循环和重复请求

- `MainWindow._on_keep_analysis_checkbox_changed(True)` 会自动启动该终端自己的 `RefreshLoop`。
- `RefreshLoop.run()` 每个终端独立运行，默认每 1000 ms 调用一次 `latest_snapshot()`。
- `start_workspace_analysis()` 先启动刷新循环，再进入提交路径；若尚无缓存帧，提交路径会创建 `SnapshotFetchWorker`，对同一个数据源再次调用 `latest_snapshot()`。
- FutuSource 只对订阅状态使用 `_subscription_lock`，`latest_snapshot()` 本身没有跨线程串行锁；因此刷新循环与一次性快照线程可能同时使用同一个 Futu 上下文。

### E. 卡死/退出的直接机制

`_stop_refresh_loop()` 在循环未能于 5 秒内退出时只记录 zombie 并继续后续流程。TradingView 路径会尝试关闭 WebSocket；FutuSource 没有对应 `_close_tv_socket()`，因此阻塞中的 Futu 请求缺少同等强度的中断路径。重复操作会让 zombie 循环累积。

现有 `logs/pa_agent.log` 已记录多次：

- `RefreshLoop did not finish within 5000 ms; tracking as zombie`
- 多次 `Unable to create terminal ...`

现有 `logs/crash.log` 还记录了大量 `Windows fatal exception: code 0x8001010d`，崩溃转储中的线程栈反复停留在 `RefreshLoop → latest_snapshot → tvDatafeed/WebSocket SSL`，与“网络阻塞 + 多刷新线程 + UI 无法响应”的症状一致。该日志包含历史会话，不能单独证明每一次都来自当前配置，但能证明这条故障模式曾真实发生。

### F. 历史记录加载的同步成本

`MainWindow._setup_ui()` 直接调用 `_refresh_history_ui()`；历史面板的 `refresh()` 会扫描 JSON、逐个 `model_validate`，然后在 UI 线程构造表格行。当前数据规模为 135 个 JSON、约 96.6 MB；159732 单品种约 23.3 MB。多个终端同时初始化时，这部分 CPU、磁盘和内存分配会叠加在 UI 线程。

## 排名后的可证伪假设

1. **重量级同步扇出（最高）**：把分析池项目数从 3 减到 1，首次点击持续分析的 UI 阻塞时间应近似按终端数下降；若只打开占位页而不连接行情，卡顿应显著消失。
2. **行情上下文/刷新线程过多（高）**：关闭自选列表报价 worker 后，再点击持续分析，Futu 上下文和线程数应明显下降，卡死概率降低。
3. **重复 `latest_snapshot()` 竞争（中高）**：禁止启动阶段同时运行 `RefreshLoop` 与 `SnapshotFetchWorker` 后，首次分析等待时间和 OpenD 请求数应下降。
4. **历史记录同步解析（中）**：让历史面板延迟加载或只读索引后，终端创建耗时应下降，但不能解释 Windows fatal 线程栈本身。
5. **特定数据源/网络阻塞（中）**：在相同终端数量下切换 Futu/TradingView/MT5；若只有某一数据源出现 5 秒以上 zombie，则需单独处理该适配器的超时和取消。

## 当前调查的边界

- 当前仍没有用真实 OpenD/Qt 会话进行压测，因此尚未给出“每个终端耗时多少毫秒”的现场基线；代码已通过静态和针对性测试。
- 7 月和 8 月日志中还存在已修复的历史错误（例如旧版 `ShimmerButton.stateChanged`、旧版路径导入），这些不应直接当作当前根因；它们只说明批量创建时单个终端失败会被循环继续吞掉并弹窗/记录错误。
- 需要下一轮在可控环境增加阶段计时和资源计数：`_create_terminal`、数据源 connect/subscribe、历史扫描、首帧到达、`RefreshLoop`/`SnapshotFetchWorker` 数量，以及 OpenD 上下文/线程数。

## 建议的修复优先级（调查结论，不是本轮实施）

1. 先把“开启持续分析”改为受控调度：UI 线程只创建占位 tab，终端和数据源按队列逐个/限并发初始化，并支持取消。
2. 将自选列表报价改为共享数据源/批量快照，避免每个 watchlist item 一个 `OpenQuoteContext`。
3. 一个终端只保留一个行情读取管线；首帧复用 `RefreshLoop` 缓存，禁止 `SnapshotFetchWorker` 与刷新循环并发访问同一数据源。
4. 为所有数据源提供统一的可中断超时/关闭接口；停止超时不得无限制地产生 zombie。
5. 历史记录改为索引/按需加载，避免终端构造时在 UI 线程反序列化大 JSON 集合。
6. 增加性能回归测试：1/3/10 个分析池项目的开关响应时间、UI 事件循环延迟、线程数、OpenD 上下文数、首帧 P50/P95、取消后的残留线程数。

## 后续实施记录

- 单个终端的数据源连接/订阅已移出 GUI 线程。
- SecondOrderWorkspace 已改为懒加载，普通个股终端首次打开不再同步构造二阶页面。
- 二阶博弈个股 K_120M 一次性行情上限统一设为 250 根，图表取数与正式推演 adapter 共用同一常量。
