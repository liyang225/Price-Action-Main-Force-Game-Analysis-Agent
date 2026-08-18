---
name: PA Agent UI Design
description: 面向价格行为分析的桌面量化工作台界面设计系统
colors:
  app-bg: "#0C0E11"
  surface-1: "#12151A"
  surface-2: "#181C22"
  surface-3: "#22272F"
  surface-4: "#333A45"
  text-primary: "#E8ECF1"
  text-secondary: "#9AA5B1"
  text-muted: "#646E7A"
  accent-steel: "#4A7EBB"
  accent-steel-hover: "#5B8CC9"
  success: "#00D084"
  danger: "#FF4757"
  warning: "#C0913C"
  market-up: "#FF4757"
  market-down: "#00D084"
  chart-up: "#E03F4D"
  chart-down: "#00B775"
  chart-ema: "#B8933E"
  line-entry: "{colors.accent-steel}"
  line-take-profit: "{colors.market-down}"
  line-stop-loss: "{colors.market-up}"
typography:
  display:
    fontFamily: "Segoe UI, Microsoft YaHei UI, sans-serif"
    fontSize: "22px"
    fontWeight: 600
    lineHeight: 1.2
  body:
    fontFamily: "Segoe UI, Microsoft YaHei UI, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.5
  section:
    fontFamily: "Segoe UI, Microsoft YaHei UI, sans-serif"
    fontSize: "12px"
    fontWeight: 600
    lineHeight: 1.3
  mono:
    fontFamily: "JetBrains Mono, Cascadia Mono, Consolas, monospace"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.4
rounded:
  base: "4px"
  checkbox: "3px"
  status-capsule: "999px"
spacing:
  unit: "8px"
  control: "12px"
  panel: "16px"
  section: "18px"
components:
  button-primary:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.text-primary}"
    typography: "section"
    rounded: "{rounded.base}"
    padding: "5px 12px"
    height: "24px"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.text-secondary}"
    typography: "body"
    rounded: "{rounded.base}"
    padding: "5px 12px"
    height: "24px"
  input-default:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.text-primary}"
    typography: "body"
    rounded: "{rounded.base}"
    padding: "4px 8px"
  tab-active:
    backgroundColor: "{colors.surface-1}"
    textColor: "{colors.text-primary}"
    typography: "body"
    rounded: "{rounded.base}"
    padding: "5px 10px"
  card:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.base}"
    padding: "8px 10px"
  status-pill:
    backgroundColor: "{colors.accent-steel}"
    textColor: "{colors.text-primary}"
    typography: "section"
    rounded: "{rounded.status-capsule}"
    padding: "2px 10px"
---

# PA Agent 前端 UI 设计文档

> 文档状态：基于当前 PyQt6 实现提取的 UI 设计规范
> 
> 更新时间：2026-08-07
> 
> 设计来源：pa_agent/gui/theme/tokens.py、theme/dark.qss、核心 GUI 组件与 _design_review 截图
> 
> 适用平台：Windows 桌面端为主；当前不是 Web 或移动端设计规范

## 1. Overview

**操作模式：Operate。** 用户的首要任务是快速确认当前行情、理解 AI 判断、检查风险和决定是否继续观察。界面应该支持高频扫描与低频深读，不用装饰性视觉争夺注意力。

**视觉北极星：冷静的暗色量化工作台。** 这是从现有主题代码反推的设计方向，尚未作为品牌口号由用户单独确认。深冷灰表面、细发丝分隔线和克制钢蓝强调构成工作台骨架；价格、决策结论和风险状态拥有明确优先级，但不依赖巨大标题或高饱和整块填充。

图表是数据主角，AI 侧栏是解释层，顶部控制条是行动层，底部状态条是系统反馈层。所有内容围绕同一份 K 线快照组织，避免用户在图表、分析、决策之间来回猜测数据是否一致。

**Key Characteristics:**

- 深色、低饱和、细线分层。
- 单一钢蓝交互强调，红涨绿跌市场语义。
- 数值和原始输出使用等宽字体，解释文本使用中文 UI 字体。
- 结论通过位置、重量和左侧强调条建立层级，而不是通过喊话式 CTA。
- 复杂调试内容收纳在低频入口，主流程保持短而稳定。

## 2. Colors

颜色体系以冷黑灰表面建立纵深，以钢蓝表达交互和选中，以红/绿表达中国市场惯例的涨跌。状态颜色只在有明确文字或组件语义时使用，不能让用户仅凭颜色猜测含义。

### Primary

- **钢蓝交互色**（accent-steel）：用于焦点边框、选中态、主操作边框、信息链接、图表入场线和已走过的决策节点。
- **钢蓝悬停色**（accent-steel-hover）：只用于悬停或强调文本，不扩张为大面积背景。

### Semantic

- **上行红**（market-up）：中国市场惯例中的上涨、做多方向、止损线和危险提醒。
- **下行绿**（market-down）：中国市场惯例中的下跌、做空方向、止盈线和成功状态。
- **警示琥珀**（warning）：等待、过渡、不确定、上下文接近阈值等提醒。
- **成功绿**（success）：完成、可继续、上下文正常等系统状态；在市场语义中优先沿用 market-down 的明确标签。
- **危险红**（danger）：错误、拒绝、取消或需要立即注意的系统状态。

### Neutral

- **应用底色**（app-bg）：全局背景和图表底板。
- **一级表面**（surface-1）：侧栏、菜单、表格主体和状态栏。
- **二级表面**（surface-2）：卡片、输入框、结论块和抬升区。
- **三级表面**（surface-3）：悬停、按下、行交替背景和工具条按钮的交互层。
- **四级表面**（surface-4）：强分隔、焦点附近的边界和弹出容器边框。
- **主文字**（text-primary）：正文、标题、价格和当前选中内容。
- **次文字**（text-secondary）：说明、标签、状态栏和次要数据。
- **弱文字**（text-muted）：占位、禁用相邻提示和未走过的决策树节点。

### Chart

- K 线上行实体使用 chart-up，下行实体使用 chart-down；图表颜色比 UI 状态色略收敛，避免蜡烛盖过结构信息。
- EMA 线使用 chart-ema 的赭黄色；其他均线使用灰蓝和赭橙作为次级层级。
- 入场线使用 line-entry，止盈线使用 line-take-profit，止损线使用 line-stop-loss；价格线必须配合图例、标签或决策字段。

**The Data Protagonist Rule.** 高饱和颜色只标记事件、方向或焦点；不把整块页面填成红、绿或蓝。

## 3. Typography

**UI 字体：** Segoe UI，中文回退 Microsoft YaHei UI。

**数据字体：** JetBrains Mono，回退 Cascadia Mono、Consolas、monospace。

**Character：** UI 字体负责中文阅读和界面动作，等宽字体负责价格、K 线、token、原始 JSON 和指标读数。字号层级保持紧凑，用字重和位置区分优先级。

### Hierarchy

- **最新价格**（22px、600）：市场摘要条的最大视觉锚点；通过字重而非彩色整块背景突出。
- **结论**（16px、600）：决策面板唯一允许明显放大的结论级文字。
- **工具条标题**（15px、600）：工作区、面板和主要区块标题。
- **正文**（13px、400）：说明文本、普通控件文本和决策依据。
- **区块标题**（12px、600）：阶段标题、表头、字段标签和次级分组。
- **说明/占位**（11px、400）：状态提示、辅助说明、上下文进度和低优先级信息。
- **数据/原始输出**（12px 等宽）：价格、指标、K 线表、JSON、推理流和 token 读数。

标题不使用全大写，不用超大 hero 文案；中文界面优先保证行距和数字对齐。

**The Weight Before Size Rule.** 先用位置和字重建立结论层级，再考虑增大字号；不要用大标题替代信息组织。

## 4. Layout

### 4.1 空间语法

使用 8px 基础间距，常规控件和表格采用紧凑密度。主要表面用 1px 发丝线分隔，不依赖厚边框或卡片阴影。

界面从外到内分为：

1. WorkspaceWindow：工作区顶层，包含自选股、分析池和终端页签。
2. MainWindow：单品种终端，包含顶部控制条和工作台主体。
3. 工作台主体：左侧图表/指标，右侧 AI 侧栏。
4. AI 侧栏：历史、交互、决策、未来走势、决策树和可视化；原始与调试放入更多菜单。
5. 复合内容：卡片、表格、流式文本、决策树和状态反馈。

### 4.2 关键比例与尺寸

| 区域 | 当前实现 | 设计意图 |
| --- | --- | --- |
| WorkspaceWindow | 最小 960×620；自选股与分析池约 40/60 | 先管理关注对象，再进入终端 |
| MainWindow 工作台 | 图表与 AI 侧栏约 45/55；侧栏最小宽度 400px | 同时看数据和解释，不让侧栏塌陷 |
| 市场摘要条 | 44px 高 | 在图表上方放置最新价、涨跌、今开、最高/最低 |
| 顶部控制条 | 两行控制、上下 1px 分隔 | 把数据源、品种、周期、获取和提交集中在一处 |
| 状态条 | 28px 高 | 持续反馈状态、上下文占用和 TPS，不抢主内容注意力 |
| 图表与指标 | 垂直 QSplitter，可调整 | 允许用户临时扩大指标区，但默认图表占主高度 |
| 侧栏页签 | 低频原始/调试移入更多菜单 | 保留高级能力，减少主导航拥挤 |

### 4.3 信息流

顶部控制条回答：看什么、从哪里取、何时刷新、何时提交。

市场摘要和 K 线回答：现在发生了什么。

交互、决策、未来走势和决策树回答：程序与模型为什么这样判断。

历史和状态条回答：过去结果如何、当前任务进行到哪里。

### 4.4 桌面适配

当前产品是桌面端，不设计移动端断点。窗口缩小时优先保护以下顺序：图表可读性 → 决策结论 → 交互输入 → 解释细节 → 原始/调试内容。

使用 QSplitter 让用户调整区域，不用硬编码一套固定比例。低宽度场景应保持侧栏最小宽度，隐藏低频页签，允许表格水平滚动，并对品种、来源和长文本做省略与 tooltip 补全。

## 5. Elevation & Depth

这是一个平面优先的量化终端。深度来自表面阶梯、细分隔线、选中态和局部强调条，而不是阴影堆叠。

### Surface hierarchy

- app-bg 是最深底板，承载图表和工作区留白。
- surface-1 承载侧栏、菜单和表格。
- surface-2 承载卡片、输入框、推理块和结论块。
- surface-3 只用于悬停、按压、交替行或短暂聚焦。
- surface-4 只用于强边界和弹出层。

### Shadow vocabulary

- 常规窗口、卡片和按钮不使用重阴影。
- QComboBox 私有弹出容器可以使用短暂的柔和阴影，用于与暗色背景分离；它是弹出层例外，不应推广到普通卡片。

**The Flat by Default Rule.** 静止状态保持平面；只有弹出层、明确焦点或交互反馈才允许增加深度。

## 6. Shapes

基础形状是轻微圆角的矩形和细线边界。常规控件、卡片、输入、表格和页签使用 base 圆角；复选框使用更小圆角；图表实时/快照状态可使用胶囊形状态标记。

- 边框默认为 BORDER_SOFT 的 1px 发丝线。
- 强边框只出现在焦点、弹出层、选中控件和需要高可见度的分隔处。
- 不使用大圆角 SaaS 卡片，不使用玻璃拟态，不使用渐变。
- 内容块可以通过左侧 2–3px 强调条表达阶段或结果，不改变整体矩形轮廓。
- 表格和树形结构优先用行、列和细网格组织，不用大量独立卡片。

## 7. Components

### Workspace shell

- 顶层使用暗色页签和薄分隔线；当前页签通过一级表面、主文字和底部钢蓝线标记。
- 自选股页是工作区入口，分析终端以品种/周期作为页签标题。
- 终端页签支持关闭和切换；不要把每个终端再做成浮动窗口。

### Watchlist and analysis pool

- 左侧自选股表负责添加、编辑、选择和进入分析池。
- 右侧分析池表负责批量分析、移出股票和持续跟踪。
- 表格使用表头、细网格、交替行和复选框表达密集信息；行悬停改变表面，不改变文字层级。
- 加入自选、加入分析池和批量分析属于 primaryButton；编辑、移出和辅助入口使用普通或 ghostButton。
- 持续跟踪按钮的鲜明红/绿背景只在开启或停止跟踪这个高风险状态控制中使用，其他按钮不得复制这种高饱和整块填充。

### Instrument control bar

- 数据源标签与品种/周期 breadcrumb 组成一个紧凑的 instrumentControlGroup。
- 数据源、交易所、品种和周期使用透明组合框，保持在同一组表面内；不要为每个字段再加独立卡片。
- 获取数据是明确动作，提交分析是默认 AI 动作；提交按钮可根据状态显示普通提交、增量分析或停止。
- 等待收盘、数据不足、品种无效和连接错误必须在控件附近给出文字说明，不能只改变边框颜色。

### Market summary strip

- 置于图表上方，展示最新价格、涨跌、今开、最高/最低。
- 最新价使用等宽字体和最大字号；涨跌百分比使用 market-up 或 market-down，并保留正负号。
- 字段通过垂直细线分隔，标签使用次文字，值使用主文字或明确语义色。

### Chart and indicator panel

- 图表底板使用 app-bg，网格使用更低对比度的 chart grid。
- K 线实体、影线、均线和决策价线必须有独立图例；不能让所有线都使用同一个高亮色。
- 图表支持滚轮缩放、拖拽平移、悬停读取和序号标记；拖拽时可延迟刷新，避免视图抖动。
- 指标工具条使用紧凑按钮和选择器；选中指标使用钢蓝边框/低透明度背景。
- K 线图和 AI 决策必须基于同一快照，页面不应出现两个相互矛盾的 K1。

### AI sidebar tabs

- 主导航顺序：历史记录、交互、决策、未来走势预期、决策树、决策树可视化。
- 原始和调试是低频审计入口，放入更多菜单，不占用主页签宽度。
- 历史页签左侧提供上一条/下一条导航按钮，并支持滚轮浏览；按钮尺寸紧凑，禁用时降为弱文字。
- 当前页签使用一级表面和底部钢蓝线；悬停只提升背景和文字对比度。

### Decision panel

- 先显示市场诊断摘要，再显示交易决策；用户首先看到趋势、周期、阶段和置信度。
- 诊断字段使用成组小卡片，内容短、可快速扫描；解释文本放入下面的推理区。
- 交易结论放在 conclusionBar 内，通过左侧强调条表达方向或结果。
- 入场、TP1、TP2、止损、盈亏比和胜率使用等宽数字；字段标签弱化，数值保持对齐。
- 不下单、等待、放弃和交易必须同时显示文本与语义颜色；禁止让颜色成为唯一信号。
- 置信度使用细进度条和数值，正常/警示/危险三种状态分别对应钢蓝/琥珀/危险红。

### Streaming and conversation

- 推理区使用 surface-2 与左侧钢蓝条；正式回答区使用 app-bg 和细边框，区分思考与交付。
- 原始 JSON 和流式 token 使用等宽字体；自然语言解释仍使用 UI 字体。
- 时间线用于在多轮分析和追问之间定位，不把所有历史内容一次性铺开。
- 输入框保持足够高度承载多行问题；发送和停止是同一位置的状态切换，避免用户寻找第二个动作。
- 流式输出期间必须显示阶段、进度或可取消状态；结束后保留完整结果，不因自动滚动遮挡用户当前浏览位置。

### Decision tree and visualization

- 二元决策树同时提供路径回放表和完整树，路径是解释主线，完整树是审计补充。
- 已走过的节点用钢蓝高亮并加粗；回答使用是/否/中性/等待等文本和语义色。
- 终点 banner 置于面板顶部，显示节点、结果和标签；闸门短路要明确写出没有调用阶段二模型。
- 自动播放是可选增强，不得阻止用户手动浏览节点或阅读理由。

### History panel

- 历史记录以表格和过滤器为主，支持品种、时间、结算方式和人工入场修正。
- 交易计划和后续结果应在同一行或紧邻事件行中呈现，避免把入场、止损和结果拆到不可追溯的页面。
- 未完成或无法结算的交易显示明确状态，不用灰色空白伪装成没有数据。

### Settings and dialogs

- 对话框沿用一级/二级表面、4px 基础圆角和细边框。
- 字段标签说明意图，复杂设置提供 tooltip 或次级说明；默认值和范围要在控件附近可见。
- API Key 默认遮罩；显示/隐藏是显式操作，保存成功要有明确反馈。
- 高风险开关如持续跟踪、外部通知和下一根 K 线预测需要解释其影响，不用模糊的开启/关闭标签。

### Status bar and notices

- 状态条固定在底部，高度紧凑；左侧显示系统消息，右侧显示上下文占用和 TPS。
- 上下文进度条无文字堆叠在条内，使用外部数值标签；正常为钢蓝，接近阈值为琥珀，危险为红色。
- 连接失败、数据延迟、校验失败、取消和记录保存应有可读文案；Toast、弹窗和状态条职责不同，不重复轰炸。

## 8. Do's and Don'ts

### Do

- **Do** 使用 theme/tokens.py 作为所有颜色、字体、字号、间距和圆角的来源。
- **Do** 保持 chart、AI 侧栏和状态栏的暗色层级一致。
- **Do** 用等宽字体对齐价格、指标、token 和 JSON。
- **Do** 在颜色状态旁边同时提供文字、图例、图标或位置线索。
- **Do** 把主流程控制放在顶部，把解释内容放在右侧，把低频调试内容放入更多入口。
- **Do** 用用户可调 QSplitter 适应桌面窗口，而不是增加更多固定布局分支。
- **Do** 为加载、实时、快照、错误、取消、短路、不下单和交易机会维护独立可读状态。

### Don't

- **Don't** 新增未登记的蓝色、灰色或状态色；先扩展 tokens.py，再同步 dark.qss 和组件。
- **Don't** 使用渐变、霓虹发光、重阴影、毛玻璃或大面积高饱和填充。
- **Don't** 用字号放大掩盖信息架构问题，也不要让所有区块都变成标题级。
- **Don't** 用红/绿颜色单独表达交易方向、错误或完成；始终搭配文字。
- **Don't** 把原始 JSON、调试日志和复杂决策树放进默认首屏。
- **Don't** 让正在形成的 K 线、旧记录或异步结果静默覆盖当前用户正在看的快照。
- **Don't** 为了视觉统一隐藏取消、网络失败、API Key 缺失或数据不足等重要反馈。

## 9. 信息架构与核心任务

### 9.1 首要任务

1. 选择数据源、品种和周期。
2. 获取行情并确认图表进入实时刷新或快照状态。
3. 提交全量或增量分析。
4. 阅读阶段一市场诊断和阶段二交易结论。
5. 在图表、决策树、历史和追问之间验证依据。
6. 需要时开启持续跟踪或外部通知。

### 9.2 页面关系

WorkspaceWindow → 自选股 → 分析池 → 品种终端 → 图表 + AI 侧栏 → 决策/历史/追问/审计

这条路径应保持可逆：用户能从终端回到池列表，也能从历史记录回到对应的图表快照；关闭页签不应删除已保存记录。

## 10. 状态矩阵

| 状态 | 主视觉 | 必须可见的文案/动作 |
| --- | --- | --- |
| 空白/未连接 | 暗色底板，控件可用 | 提示选择数据源并获取数据 |
| 连接中/探测中 | 状态条更新，提交不可用 | 当前尝试的交易所或连接状态 |
| 实时刷新 | chart status live，状态清晰 | 实时刷新中、当前源和刷新反馈 |
| 分析快照 | chart status snapshot，琥珀提示 | 当前为分析快照，形成 K 线不参与分析 |
| 数据不足 | 弱化提交按钮或禁用 | 需要的最少 K 线数量和当前数量 |
| 分析中 | 流程条推进，发送按钮变停止 | 阶段一/阶段二、思考流、可取消 |
| 校验重试 | 阶段卡片保留已有内容 | 重试阶段、失败原因和次数 |
| 闸门短路 | 决策树显示等待/未知 | 明确未调用阶段二模型 |
| 分析完成 | 决策页显示结论，记录可回看 | 保存状态、置信度和下一步 |
| 交易机会 | 结论左侧强调条和明确方向 | 订单类型、三价、风险、提醒动作 |
| 不下单/等待 | 中性或琥珀，不用危险红误导 | 不下单原因和需要观察的触发条件 |
| 错误 | 危险色仅作用于局部状态 | 可理解的错误、重试或切换线路 |
| 取消 | 保留已收到结果和部分记录 | 已取消、可重新提交 |
| 历史回放 | 图表与侧栏冻结 | 当前记录时间、来源和回放状态 |

## 11. Interaction and Motion

### 11.1 Interaction rules

- Hover 只提升局部表面和文字对比度，不改变布局尺寸。
- Focus 使用钢蓝边框或低透明度洗色；键盘焦点不能只依赖鼠标悬停。
- Pressed 使用更深的 surface-1，表示动作已被接收。
- Disabled 使用 text-muted 和 surface-1，且保留 tooltip 或原因说明。
- 长文本、长品种名和调试内容使用省略、滚动或 tooltip，不撑破主布局。
- 自动刷新、持续跟踪和自动播放都必须有停止入口，且停止后状态立即可见。

### 11.2 Motion rules

常规页面不使用渐变入场、缩放弹跳或持续发光。动效只服务于状态变化：

- 决策流程自动播放用于解释分析顺序，不用于吸引注意。
- 流式 token 按到达顺序追加，避免整个面板反复重排。
- 图表拖拽期间延迟刷新，松开后恢复，避免视图抖动。
- 弹出菜单出现时可以使用系统默认过渡；不得为普通按钮添加装饰性动画。

## 12. Accessibility and UI QA

### 12.1 可访问性基线

- 正文优先使用 text-primary 和 text-secondary；text-muted 只用于辅助或禁用语境。
- 红涨绿跌必须同时显示涨/跌、做多/做空、止盈/止损等文字或图例。
- 每个输入、开关、按钮和图表辅助动作都应有可见文本或 tooltip。
- 所有主要流程控件应支持 Tab、Enter、Space 和 Escape 等原生键盘行为。
- 焦点状态要在暗色背景上清晰可见；不能用透明边框消除系统焦点。
- 表格、树和时间线需要可选择、可定位、可滚动，并保持列标题语义。
- 中文 UI 字体和等宽数字字体需要在 Windows 无对应字体时有稳定回退。

### 12.2 每次 UI 改动的检查

1. 检查 960×620 最小工作区和 1440×900 常用尺寸。
2. 检查图表、侧栏和状态条在数据为空、数据不足、错误、加载和完成时的占位。
3. 检查键盘焦点、禁用态、tooltip、下拉弹窗和滚动条。
4. 检查红涨绿跌、入场/止盈/止损线和决策结论是否能靠文字独立理解。
5. 检查新增颜色、字号、间距和圆角是否已经回收到 theme tokens。
6. 检查异步刷新或分析完成时，不会覆盖用户当前选择、滚动位置或历史记录。

## 13. Implementation Mapping

| 设计来源 | 责任 |
| --- | --- |
| pa_agent/gui/theme/tokens.py | 规范颜色、字体、字号、圆角、间距和语义别名 |
| pa_agent/gui/theme/dark.qss | 全局 Qt 控件、页签、按钮、表格、输入和状态样式 |
| pa_agent/gui/theme/apply.py | QApplication 主题、下拉弹窗、Windows 标题栏适配 |
| pa_agent/gui/workspace_window.py | 自选股、分析池、工作区页签和批量操作 |
| pa_agent/gui/main_window.py | 单品种终端、控制条、图表与 AI 侧栏布局 |
| pa_agent/gui/chart_widget.py | K 线、均线、悬停、缩放、拖拽和决策价线 |
| pa_agent/gui/ai_sidebar.py | AI 侧栏页签、历史导航和低频菜单 |
| pa_agent/gui/decision_panel.py | 市场诊断、交易结论、置信度和风险指标 |
| pa_agent/gui/decision_tree_panel.py | 决策路径和完整树的可解释呈现 |
| pa_agent/gui/conversation_widget.py | 追问、时间线、流式输出和取消 |
| pa_agent/gui/history_panel.py | 历史记录、过滤、结算和人工入场修正 |

新增 UI 组件时，应先从 tokens.py 选取表面、文字、语义色、字体和尺寸，再在 dark.qss 中提供通用状态，最后用组件级 QSS 处理真正独特的局部样式。组件文档、单元测试和截图应同步更新。

