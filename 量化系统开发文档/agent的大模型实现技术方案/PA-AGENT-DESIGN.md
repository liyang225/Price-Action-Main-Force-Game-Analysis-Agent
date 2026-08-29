# PA Agent 设计文档

> 文档状态：当前实现说明
> 
> 更新时间：2026-08-07
> 
> 适用范围：PA_Agent 目录及其 pa_agent Python 包

## 1. 定位与边界

PA Agent 是面向主观交易者的桌面端价格行为分析辅助工具。它从多个行情源读取 K 线，计算程序特征，并调用兼容 OpenAI Chat Completions 的 AI 服务完成市场诊断与交易决策。

系统的核心边界如下：

- 系统只提供分析、可视化、记录和提醒，不连接券商执行下单。
- AI 的输入是结构化 K 线、指标、几何特征和策略文本，不是截图识图。
- 阶段一负责市场状态与交易闸门，阶段二负责交易方案；两阶段结果必须经过程序校验。
- 所有分析以已收盘 K 线为准；正在形成的 K 线可以显示在图表中，但不得进入 AI 分析快照。
- 运行时数据、API 响应和个人配置属于本地数据，不应提交到代码仓库。

## 2. 设计目标

### 2.1 目标

- 用统一的 DataSource 接口屏蔽行情供应商差异。
- 用不可变 KlineFrame 固化图表与 AI 使用的同一份分析快照。
- 将提示词、策略库、经验库和校验规则分离，便于调优而不改动编排骨架。
- 对模型输出进行 JSON 解析、归一化、模式校验和跨字段语义校验。
- 在网络错误、模型输出错误、取消和磁盘错误时保留可诊断信息。
- 通过增量分析复用上一轮上下文，降低持续跟踪的延迟和 token 消耗。

### 2.2 非目标

- 不实现真实交易账户、订单路由、资金托管或自动交易。
- 不把 GUI 逻辑、行情供应商协议或模型特例泄漏到通用数据模型中。
- 不以模型的自然语言描述替代程序计算的价格、影线、盈亏比和交易约束。

## 3. 总体架构

系统采用六个主要层次：

| 层次 | 主要职责 | 主要包 |
| --- | --- | --- |
| 启动与上下文 | 初始化 Qt、日志、配置、事件总线和工作区 | pa_agent.main、app_context、config |
| 行情数据 | 连接供应商、订阅品种周期、获取最新 K 线 | pa_agent.data |
| 分析准备 | 构造快照、指标、几何特征、增量基线 | data.snapshot、ai.kline_features、gui.analysis_prep_worker |
| AI 与策略 | 组装提示词、请求模型、路由策略、校验和重试 | pa_agent.ai、orchestrator |
| 记录与通知 | 保存分析、追问、历史、交易记录和外部提醒 | pa_agent.records、notify |
| 展示层 | 图表、决策面板、决策树、流式输出、历史与设置 | pa_agent.gui |

一次完整分析的逻辑链路是：

行情源 → RefreshLoop → 原始 newest-first K 线 → KlineFrame → 阶段一诊断 → 策略路由/经验检索 → 阶段二决策 → 校验归一化 → GUI 展示/记录/通知

AppContext 负责依赖注入，不使用全局单例保存可变的行情或 AI 状态。工作区启动时只加载设置和事件总线；每个分析终端按需拥有自己的行情源与 AI 栈，避免启动阶段加载过重。

## 4. 核心模块

### 4.1 启动和配置

- pa_agent.main：应用入口，先配置崩溃诊断和日志，再启动本地 Futu OpenD 辅助进程，创建 QApplication，应用主题并显示 WorkspaceWindow。
- pa_agent.app_context.AppContext：向 GUI 和编排器传递 settings、event_bus、data_source、client、assembler、router、validator、pending_writer、经验读取器和 token ledger。
- pa_agent.config.paths：集中定义 PROJECT_ROOT、提示词目录、records、history analysis、experience、config 和 logs 路径。
- pa_agent.config.settings：使用 Pydantic 模型加载、校验、迁移和保存 settings.json。

### 4.2 行情与快照

- pa_agent.data.base：定义 KlineBar、IndicatorBundle、KlineFrame、DataSource 及数据源异常类型。
- pa_agent.data.factory：按 kind 创建数据源，并区分 GUI 可见数据源与代码可用的隐藏数据源。
- pa_agent.data.refresh_loop.RefreshLoop：独立 QThread 中周期抓取行情，负责防重入、状态信号和指数退避。
- pa_agent.data.snapshot：从 newest-first 原始数据构建显示帧或分析帧，并计算 EMA20、ATR14。
- pa_agent.indicators：提供 EMA、ATR 等纯计算指标。

### 4.3 AI 与编排

- pa_agent.ai.client_factory：根据模型别名选择 OpenAI 兼容客户端或 Cursor SDK 客户端。
- pa_agent.ai.deepseek_client.DeepSeekClient：封装流式请求、reasoning/content 分流、usage 统计、请求参数适配和取消。
- pa_agent.ai.prompt_assembler.PromptAssembler：组合系统提示、K 线表、程序特征、阶段结果、策略文本和经验案例。
- pa_agent.ai.router.route_strategy_files：根据已验证的阶段一 JSON 生成有序、去重且只包含已知文件的策略列表。
- pa_agent.ai.json_validator.JsonValidator：执行 JSON 抽取、语法修复、模式校验、归一化和跨字段业务校验。
- pa_agent.orchestrator.two_stage.TwoStageOrchestrator：驱动阶段一、路由、经验注入、闸门短路、阶段二、重试、故障切换与落盘。
- pa_agent.orchestrator.free_chat.FreeChatSession：以已完成分析为锚点，维护追问上下文并追加 followup JSONL。

### 4.4 记录与展示

- pa_agent.records.schema：定义 AnalysisRecord、FollowupTurn、ValidationError、AlarmPayload 和经验条目模型。
- pa_agent.records.pending_writer.PendingWriter：保存完整或部分分析，并递归清理 API Key。
- pa_agent.records.analysis_history：查找上一轮成功分析、计算新增已收盘 K 线，并维护历史记录缓存。
- pa_agent.records.history_analysis：按品种归档成功记录，并从后续 K 线推导交易计划的触发、止盈、止损和结果。
- pa_agent.records.trade_logger：把有交易机会的阶段二结果写入 CSV，并保存对应 K 线图。
- pa_agent.gui：负责图表、分析流、决策树、历史、追问、设置、调试和演示回放。

## 5. 行情数据设计

### 5.1 DataSource 接口

所有数据源实现以下生命周期：

1. connect：建立连接或初始化供应商客户端。
2. subscribe(symbol, timeframe)：设置当前品种和周期。
3. latest_snapshot(n)：返回最近 n 根 newest-first 的 KlineBar。
4. unsubscribe：清除订阅。
5. disconnect：关闭连接并释放供应商资源。

当前 factory 支持的 kind 包括：

| kind | 数据源 | 典型用途 |
| --- | --- | --- |
| mt5 | MetaTrader 5 | Windows 本地终端行情 |
| tradingview | TradingView / tvdatafeed | 跨市场、匿名或登录行情 |
| futu | Futu OpenD | 港股、A 股等 Futu 行情 |
| akshare | AkShare | A 股等代码级数据源 |
| eastmoney | 东方财富 | A 股行情 |
| eastmoney_futures | 东方财富期货 | 期货行情 |
| tushare | Tushare Pro | A 股行情 |
| yfinance | yfinance | 期货、股票或加密资产的代码级数据源 |

GUI 默认直接暴露 MT5、TradingView 和 Futu；其余来源保留为代码或配置级能力。

### 5.2 K 线顺序和收盘语义

- 原始 bars 使用 newest-first 顺序。
- 分析帧中 bars[0] 是 K1，即最近一根已收盘 K 线；bars[-1] 是更老的 K 线。
- 实时图表可以包含 forming bar，其 seq 为 0；分析帧会丢弃 forming bar。
- 数据源的时间戳最终规范为毫秒时间戳，并对 high、low、close 做基本范围归一化。
- frame.bars 和 frame.indicators 是 tuple，KlineFrame 是不可变快照，确保图表和 AI 不会看到半更新状态。

### 5.3 指标和分析窗口

build_analysis_frame 会额外获取最多 50 根旧 K 线用于 EMA20 和 ATR14 预热，但只把用户配置的分析窗口发送给模型。指标计算先转为 oldest-first，计算后再转回 newest-first 与 bars 对齐。

图表和 AI 必须使用同一套快照构造语义，否则 K1、K 序号、EMA/ATR 和决策价位会错位。

### 5.4 刷新和切换

RefreshLoop 默认每 1000 ms 获取一次数据。连续失败时采用 0.5、1、2、4 秒递增的退避，最大 10 秒；单次请求未结束时跳过下一 tick，避免 TradingView 并发连接和限流。

切换数据源、品种或周期时，GUI 应按以下顺序处理：取消分析 → 停止刷新线程 → 断开旧数据源 → 创建并连接新数据源 → 重新订阅 → 清空图表/追问会话 → 重启刷新。

## 6. 两阶段 AI 分析

### 6.1 阶段一：市场诊断

输入由 PromptAssembler 生成，至少包括：

- 已收盘 K 线 OHLCV 表。
- EMA20、ATR14 和 K 线几何特征。
- 程序预计算的市场结构辅助特征，例如区间、摆动点和趋势背景。
- 人设、市场诊断框架和模式判定提示词。
- 增量分析时的上一轮阶段一结果和新增 K 线任务。

阶段一输出必须是完整 JSON，包含市场周期位置、方向、置信度、模式标签、关键位、逐 K 分析、gate_trace 和 gate_result 等字段。模型的 reasoning_content 只用于显示和审计，程序只把 assistant content 作为结构化结果的主来源。

处理顺序：

1. preflight 检查数据量、K 线顺序和必要特征。
2. 调用模型并分别流式传递思考 token 与正文 token。
3. 先做 JSON 抽取和语法修复，再执行阶段一归一化。
4. 按配置执行 schema、闸门、逐 K 特征、增量一致性和 trace 语义检查。
5. 校验成功后，使用诊断 JSON 生成阶段二策略文件列表。

### 6.2 策略路由和经验注入

route_strategy_files 是无副作用纯函数。它根据 cycle_position、direction、spike_stage、alternative_cycle_position 和 detected_patterns 选择策略文件，并稳定去重。

路由可以加载通道、尖峰、交易区间等基础策略，并叠加楔形、二次入场、突破失败、AlwaysIn、铁丝网、磁力位、三角形、双重顶底等模式策略。extreme_tr 和 unknown 默认不加载交易策略，结果倾向于不交易或等待。

阶段二使用 ExperienceReader 从 experience/<cycle_position>/success_cases 和 failure_cases 中读取近期案例，再按方向和模式相关性排序。experience_max_entries 为 0 时不注入经验；每条经验还受字符上限控制。

### 6.3 闸门短路

如果阶段一 gate_result 为 wait 或 unknown，程序不再发起阶段二模型请求，而是由 decision_tree.build_stage2_gate_wait_response 生成等待结果。这条路径仍然保存完整记录、使用量和阶段一诊断，避免把低质量或不确定状态继续转成交易方案。

### 6.4 阶段二：交易决策

阶段二输入包括阶段一已验证 JSON、路由出的策略文本、相关经验、当前 K 线和交易倾向 decision_stance。输出包括诊断摘要、K 线信号/入场分析、decision、decision_trace、terminal 以及可选的下一周期和下一根 K 线预期。

阶段二不执行交易，只产生可展示、可验证和可记录的交易计划：

- 不下单。
- 限价单。
- 突破单。
- 市价单。

阶段二校验重点包括：

- 不下单时 entry、止盈、止损和方向字段必须为 null。
- 交易类型存在时，入场、止盈、止损和方向必须完整。
- 突破单必须绑定信号 K 线的 high 或 low，并且入场价必须真正越过对应极点。
- 信号棒、入场棒、跟随关系和 freshness 必须满足顺序约束。
- 程序重新计算风险、盈亏比和交易者方程，不能信任模型叙述中的距离或比例。
- next_cycle_prediction 和 next_bar_prediction 的概率、枚举和 unpredictable 语义必须一致。
- terminal.outcome 必须与决策树终点一致，只能使用 wait、reject、trade 或 proceed 等规定值。

### 6.5 归一化、重试和失败分类

JsonValidator 将错误分为以下主要类别：

| 类别 | 含义 | 处理 |
| --- | --- | --- |
| a | JSON 语法、截断或顶层类型错误 | 尝试修复；失败后按重试策略处理 |
| b | schema 必填字段缺失 | 生成结构化反馈并重试 |
| c | 枚举、类型或跨字段语义错误 | 归一化轻微偏差；必要时有限重试 |
| d | 纯文本或非 JSON 回复 | 按格式错误重试 |
| e | provider 配额、认证或服务错误 | 触发线路处理或终止并记录 |

阶段一和阶段二的重试次数分别受 validation.retry_max、retry_max_semantic 和 retry_stage2 控制。重试请求携带结构化错误反馈，而不是只重复原始 prompt。每次请求的 usage 都累加到 usage_total。

## 7. 增量与持续跟踪

上一轮成功记录由 analysis_history.find_latest_successful_record 按 symbol/timeframe 查找。compute_incremental_bar_delta 以旧记录的 K1 时间戳为锚点，只有在当前窗口仍能找到锚点时才计算新增已收盘 K 线数量。

当新增数量不超过 general.incremental_max_new_bars 时，GUI 可自动把提交按钮切换为增量分析。增量 prompt 复用上一轮阶段一的 system、user 和 assistant 上下文，只追加新增 K 线和更新任务；输出仍然必须是完整阶段一 JSON，而不是差异补丁。

keep_analysis 开启后，GUI 以最近已收盘 K 线时间戳作为 sentinel。新 K 线收盘且没有正在分析时，自动提交下一轮分析。提交时先记录锚点，避免分析期间刚收盘的 K 线被重复触发。

连续跟踪、增量基线和历史回放都必须以已收盘 K 线时间戳为准，不得以当前数组索引判断新旧。

## 8. 记录模型与持久化

AnalysisRecord 是一轮两阶段分析的权威审计对象，主要字段如下：

| 字段 | 内容 |
| --- | --- |
| meta | 时间、品种、周期、K 线数量、模型快照、交易倾向 |
| kline_data | 实际发送给 AI 的 K 线数据 |
| htf_text | 高周期上下文文本，若该流程提供则一并记录 |
| stage1_messages / response / diagnosis | 阶段一 prompt、原始响应和已验证 JSON |
| stage2_messages / response / decision | 阶段二 prompt、原始响应和已验证 JSON |
| strategy_files_used | 实际路由并加载的策略文件 |
| experience_loaded | 实际注入的经验条目 |
| exception | 取消、网络、校验、数据不足或程序错误 |
| usage_total | 所有模型调用累计 token 用量 |

默认路径由 config.paths 统一定义：

| 路径 | 用途 |
| --- | --- |
| config/settings.json | 运行配置，包含 provider、general、prompt、validation 等组 |
| records/pending/*.json | 完整或部分分析记录，也是增量分析的基线来源 |
| records/pending/*.followups.jsonl | 追问会话的逐轮追加记录 |
| history analysis/<symbol>/*.json | 成功分析的按品种历史归档 |
| experience/<cycle_position>/success_cases/*.json | 成功经验库 |
| experience/<cycle_position>/failure_cases/*.json | 失败经验库 |
| trade_records/*.csv 和 *.png | 交易机会表格及对应 K 线图 |
| logs/pa_agent.log、logs/crash.log | 运行日志和崩溃诊断 |

PendingWriter 的行为：

- 成功流程使用 save_full；取消、网络错误、数据不足、校验失败和程序异常使用 save_partial。
- 文件名包含本地时间、symbol 和 timeframe，便于增量查找与人工定位。
- 写盘失败只记录日志并通过 EventBus 发出 disk_error，不把磁盘问题扩散为 GUI 崩溃。
- 保存前递归替换 API Key，meta 中只保留经过清理的 provider 配置快照。

历史模块只归档 exception 为空且有 stage2_decision 的成功记录。它还可以根据后续 K 线判断限价触发、止盈、止损和结算规则，但该计算是回测/复盘用途，不是订单执行。

## 9. 追问、提醒与回放

### 9.1 追问

FreeChatSession 以已完成 AnalysisRecord 为锚点，固定加入追问系统提示、压缩后的阶段一/阶段二 JSON 和由程序验证字段生成的事实回忆。发送每一轮追问时，可附加当前已收盘 K 线表。

追问的 assistant reasoning 默认不回传给下一次 API；对支持该语义的 provider 可保留 reasoning_content。每轮都累加 token ledger，并写入 followups JSONL；取消的轮次也会记录。

### 9.2 交易机会提醒

当阶段二给出满足置信度阈值和字段约束的交易机会时，GUI 可以播放提示音、弹窗并切换到决策页。后台同时可写入交易 CSV/图像，并向已配置的飞书或 PushPlus 发送提醒。

### 9.3 演示回放

demo 模块从 records/pending 中读取已保存记录，重新驱动界面展示，用于没有实时行情或 API Key 时检查 UI 与结果呈现。

## 10. GUI 与线程模型

Qt 主线程只负责窗口、控件、图表和信号槽。耗时操作不得阻塞主线程：

| 工作 | 执行位置 | 与主线程通信 |
| --- | --- | --- |
| 行情轮询 | RefreshLoop QThread | frame_ready、status_changed |
| 分析准备 | AnalysisPrepWorker QThread | ready、failed |
| 两阶段分析 | MainWindow 内的 _AnalysisWorker QThread | 分析事件、reasoning/content token、record_ready |
| 追问 | ConversationWidget 的 _ChatWorker QThread | 流式 token、finished、error |
| 交易记录/通知 | GUI 侧后台任务 | 结果信号和日志 |

取消使用 CancelToken，在每个阶段前、模型调用后以及流式请求中检查。窗口关闭、数据源切换和用户停止分析都必须触发取消，并通过 worker 信号回收 UI 状态。

TradingView 客户端不是线程安全的，TradingViewSource 使用锁串行化快照请求，并在切换或请求结束时关闭旧 WebSocket，避免并发请求、连接泄漏和限流。

EventBus 用于跨组件传递日志相关事件和磁盘错误；单个终端的流式 UI 更新优先通过 Qt signal 完成。

## 11. 配置设计

Settings 顶层分为：

| 组 | 关键内容 |
| --- | --- |
| provider | base_url、model、API Key、思考开关、推理强度、上下文窗口、按 URL 的 token 上限 |
| general | 数据源、品种、周期、分析窗口、刷新、交易倾向、增量和持续跟踪、界面偏好 |
| prompt | 策略库加载方式、经验数量/长度、阶段一模式 brief |
| validation | strict/lenient、schema/语义校验、截断修复、重试次数 |
| feishu / pushplus | 外部通知 |
| tushare / futu | 对应数据源连接配置 |

配置加载具备默认值、旧字段迁移和兼容未知字段能力。不存在 settings.json 时创建默认配置；无法读取或解析时使用默认 Settings。API Key 建议通过 GUI 保存，以加密字段持久化；日志、记录和调试导出必须避免泄露。

修改配置后的原则：

- 影响数据源、品种或周期的修改必须停止旧刷新与分析，再重建订阅。
- 影响 AI provider 的修改必须重建 client；不能只修改界面文本。
- 影响 prompt 或 validation 的修改只对新提交生效，历史记录必须保留当时的 messages 和 provider 快照。
- 任何新增配置字段都应同步 Settings 模型、example 配置、GUI 或文档，以及 round-trip 测试。

## 12. 可观测性与安全

- 启动阶段写入运行环境、配置摘要和连接诊断；日志配置带 API Key masking。
- 原始响应、思考内容、prompt、校验错误和每次调用 token 用量进入本地审计记录。
- provider 线路切换和双线路失败可触发飞书通知；通知失败只记录日志，不应让分析线程崩溃。
- 记录写盘前递归清理 secret；不要提交 config/settings.json、records、experience 内容、logs 和 trade_records。
- 本项目不应把用户的 API Key、持仓或交易隐私发送到未配置的第三方服务。

## 13. 测试策略

测试按风险分层：

| 层次 | 关注范围 | 目录 |
| --- | --- | --- |
| unit | 单模块归一化、数据源、指标、GUI 控件和配置 round-trip | tests/unit |
| property | 不变量、序列、掩码、记录往返和预测概率 | tests/property |
| integration | 两阶段边界、重试、超时、短路、取消和数据源接缝 | tests/integration |
| e2e | 完整 happy path、无下单、追问、切换中途取消 | tests/e2e |
| live | 需要真实网络或环境变量的供应商测试 | tests/integration，标记 live |

在 Windows 上运行测试使用项目约定的解释器，不使用 py -3：

    C:\Users\bai\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests

针对改动范围，应先运行对应 unit/integration 测试，再运行完整 tests。涉及 GUI、数据源、供应商或并发控制时，应优先补充失败路径测试。

## 14. 扩展约束

### 14.1 新增数据源

实现 DataSource 抽象接口，统一输出 newest-first、毫秒时间戳和 closed 标记；在 factory、settings 类型、默认值、GUI 选项和测试中注册。不得让供应商原始 DataFrame 或 SDK 类型穿过 data 层边界。

### 14.2 新增模型/provider

优先复用 OpenAI 兼容客户端；只有请求协议或认证完全不同才新增 client。把 provider 特例集中在 client_factory、deepseek_client 或对应 connector，不要散落到 PromptAssembler、GUI 或记录模型。

### 14.3 新增输出字段

同步修改提示词、schema、normalizer、validator、records/schema.py、GUI 展示和 fixtures。若字段涉及交易安全，必须增加数值或跨字段校验，并为不下单路径增加 invariant 测试。

### 14.4 修改 K 线语义

先检查 data.snapshot、PromptAssembler、analysis_history、chart_widget、增量逻辑和所有依赖 K 序号的测试。K1 的定义、forming bar 的 seq=0 和已收盘过滤是跨模块契约，不能只改单个组件。

## 15. 已知限制

- 行情源的交易所、时区、闭合判断和网络稳定性受外部供应商影响；自动探测成功不等于长期稳定。
- AI 的 reasoning/content 结构和 token usage 取决于网关实现，客户端需要持续保留 provider 适配分支。
- 经验库是只读输入，系统不会自动把每次分析判定为成功或失败经验；经验生产需要外部流程。
- 历史交易结果由后续 K 线推导，遇到同一根 K 线同时触及止盈与止损、T+0/T+1 或人工入场时，必须遵循历史面板的结算和覆盖设置。
- 设计文档描述当前代码行为，不替代具体提示词、JSON schema、配置说明或安全策略。

## 16. 相关入口

- README.md：安装、快速启动和产品范围。
- PA_Agent使用文档.md：面向用户的操作说明。
- config/README.md：运行配置和安全说明。
- docs/获取数据功能说明.md：数据源、刷新和 TradingView 探测行为。
- docs/图表K线与分析快照说明.md：图表、快照和 K 线一致性说明。
- pa_agent/orchestrator/two_stage.py：两阶段流程的实现入口。
- pa_agent/ai/prompt_assembler.py：提示词和分析上下文构造。
- pa_agent/ai/json_validator.py：模型输出的解析与业务校验。
- pa_agent/records/schema.py：持久化记录的规范模型。

