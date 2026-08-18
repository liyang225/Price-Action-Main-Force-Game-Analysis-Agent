# 推演进度流式映射到概览界面 · 设计方案

> 状态：待确认（未开始编码）
> 目标：把二阶博弈后台推演的「阶段进度 + 大模型思考流」实时流式显示到 PA「概览」标签页的长文本框中，让用户直观看到后台正在推演和计算。

---

## 1. 现状诊断（已逐文件确认）

| 事实 | 代码位置 | 结论 |
|---|---|---|
| 阶段进度已有骨架：11 节点流程图卡片，由 `progress(operation, stage, status)` 信号驱动 | PA `second_order_workspace.py` `_AnalysisFlowCard` / `_ApiWorker` / `_worker_progress` | 阶段状态已可视化，但**概览文本框本身不流式滚动**，只在结束后一次性 `set_grouped_payload` |
| 后台已跑在工作线程：`_ApiWorker(QThread)`，信号为 queued connection | 同上 | 线程模型正确，**无需改** |
| 模型客户端已支持流式，但 token 文本被丢弃 | 二阶 `src/integration/model_adapter.py` `PAChatClientAdapter._fetch` 里 `on_reasoning_token=on_activity`、`on_content_token=on_activity`，而 `on_activity(*_args)` 忽略参数 | **关键缺口 1**：token 文本没透传出来 |
| PA 的 `stream_chat` 回调签名为 `Callable[[str], None]`（逐个 chunk 传字符串） | PA `deepseek_client.py:614`、`cursor_sdk_client.py:327` | 透传无需改 PA 模型层，接住 chunk 即可 |
| PA 自己已有成熟的流式文本渲染范式（推理/回答双流） | PA `conversation_widget.py` / `ai_stream_window.py` | 可参照其追加/节流经验，但二阶侧不直接复用它（隔离） |
| 已有审计轨迹 `request_log` / `llm_trace` | 二阶 `model_adapter.py` | 保留不动，作为最终审计来源；流式展示是「过程可见」，审计是「结果可查」，两者分工 |

**结论**：这是「打通两处断点」而非「新造一套系统」。

---

## 2. 核心方案：事件驱动推送（不轮询、无 IPC）

- 后台在工作线程里**主动 emit** 结构化进度事件，UI 主线程经 Qt queued connection 接收并追加文本。
- 全程进程内函数调用 + 信号，**不用**读日志文件轮询、**不用** WebSocket、**不用**额外 IPC。
- 效率要点：token 回调是高频的（每 token 一次），不能每 token 直接刷 UI，必须**批处理 + 节流**（见 §5）。

---

## 3. 事件模型 `ProgressEvent`

新增一个框架无关的数据结构（放在二阶博弈侧，PA 侧只消费）：

```
ProgressEvent:
  ts        : datetime
  symbol    : str
  kind      : "stage" | "thinking" | "content" | "info" | "error"
  stage     : str      # 对应 _FLOW_STAGE_INDEX 的 11 个阶段名之一（可空）
  message   : str      # 人类可读文本；thinking/content 为 token 片段
  source    : str      # 产生方：participant_classifier / behavior_forecaster / ...
```

- `kind="stage"`：阶段里程碑，携带一句自然语言结论（如「主导参与者＝主力；候选行为＝拉升/出货」）。
- `kind="thinking"/"content"`：模型逐 token 的推理流 / 回答流。
- `kind="info"`：过程提示（如「HMM 信念已更新」）。
- `kind="error"`：错误文本，流入文本框并标红。

**设计原则**：事件只增不改，`finish` 阶段收尾；展示层只读，不反向写回。

---

## 4. 端到端数据流

```
二阶博弈后台（_ApiWorker 工作线程）
  PAChatClientAdapter.complete()
     └─ stream_chat(on_reasoning_token=emit, on_content_token=emit)   # 接住 chunk，不再丢弃
           └─ 回调 → ProgressSink(ProgressEvent(kind="thinking"/"content", message=chunk))
  ReasoningPipeline / prepare_materials / orchestrator
     └─ 各阶段边界 → ProgressSink(ProgressEvent(kind="stage"/"info", stage=..., message=...))
        │   （进程内函数调用，无副作用）
        ▼
PA 侧 _ApiWorker（QThread）
  model_activity_callback → 升级为 progress_event 信号（携带 ProgressEvent）
        │   pyqtSignal，跨线程 queued connection，Qt 保证线程安全
        ▼
SecondOrderWorkspace._worker_progress（主线程 slot）
     ├─ _AnalysisFlowCard.set_status()   # 已有：阶段卡片高亮/打勾
     └─ _overview_stream.append(event)   # 新增：概览文本框流式追加（节流）
```

阶段里程碑（`kind="stage"`）同时驱动「流程图卡片」与「流式文本框」；模型 token（`kind="thinking"/"content"`）只进「流式文本框」。

---

## 5. 改动点清单

### A. 二阶博弈侧（生产后端，`SecondOrderGame/`）

1. **新增 `src/integration/progress.py`**
   - `ProgressEvent`（dataclass，frozen）
   - `ProgressSink`：极简发布器（`emit(event)` + 可选订阅回调），纯内存、无副作用、默认空实现（不影响无 UI 的 CLI/测试路径）。

2. **改 `src/integration/model_adapter.py` `PAChatClientAdapter`**
   - 把 `activity_callback` 的语义从「心跳」扩为「token 透传」：新增可选 `on_token: Callable[[str, str], None]`（`(kind, chunk)`），在 `_fetch` 中把 `on_reasoning_token=lambda c: on_token("thinking", c)`、`on_content_token=lambda c: on_token("content", c)`。
   - 兼容旧行为：无回调时保持现状；`request_log` 照旧完整记录。
   - `PAModelAdapter`（非流式 `call_text`）不动，仅当 provider 支持 `stream_chat` 时才有 token 流。

3. **改 `src/integration/pa_embedded_service.py`**
   - `PAEmbeddedService.__init__` 增加可选 `progress_sink: ProgressSink | None`。
   - `prepare_materials`：主体目的分析、消息情绪打分两步前后发 `kind="stage"/"info"`。
   - `run_analysis`：`orchestrator.run` 前后发阶段事件（见 4 点）。

4. **改 `src/integration/production_orchestrator.py`**
   - `ProductionOrchestrator` 增加可选 `progress_sink`，在 `_observe_cycle`（情绪周期判定 + HMM 信念更新）与 `run` 各步骤边界发 `kind="stage"`，携带结构化结论（主导参与者、模型行为、情景树/闸门结果）。
   - 事件内容一律来自**程序已算好的字段**，大模型不生成进度文案（符合 ADR-0001「大模型不生成数字/不受约束文案」的边界精神）。

### B. PA 侧（UI，`PA_Agent/`）

5. **改 `second_order_workspace.py` `_ApiWorker`**
   - 新增 `progress_event = pyqtSignal(object)`（或 `pyqtSignal(str, str, str)` 携带 kind/stage/text）。
   - `_model_activity` 升级为「接 chunk → 节流缓冲 → `progress_event.emit`」。

6. **改 `second_order_workspace.py` `SecondOrderWorkspace`**
   - 在「概览」页 `overview_tab` 新增一个只读流式文本区（复用 `QPlainTextEdit`，等宽字体，或复用 `_text_tab` 的容器样式），置于 `_overview_flow` 与最终摘要 `_overview` 之间/之下。
   - `_worker_progress` 同时处理两类：`(operation, stage, status)` 仍更新流程图卡片；`progress_event` 追加流式文本。
   - 切换品种 / 重新运行时清空流式缓冲（对齐现有 `set_pa_payload` 的 reset 逻辑）。

---

## 6. 关键工程决策

1. **节流（必须）**：token 回调每 token 触发一次，直接 emit + append 会刷爆主线程。策略二选一：
   - 后端（sink 侧）聚合：累积 50–80ms 或若干字符后合并为一条事件再 emit；或
   - UI 侧：`QPlainTextEdit` 用 `QTimer` 批量 flush。
   - **推荐后端聚合**，理由：跨线程信号本身有开销，聚合后再跨线程更省；PA `ai_stream_window` 已有同类节流经验可参照。

2. **追加与自动滚动**：`appendPlainText` + `moveCursor(QTextCursor.End)`；若用户正在上翻则不强制滚动（对齐 PA UI 设计文档「不因自动滚动遮挡当前浏览位置」）。

3. **环形缓冲**：流式区最多保留 N 行（如 2000），超出裁头，避免长跑内存/渲染膨胀。

4. **线程安全**：所有 UI 更新经 queued signal 回主线程；`ProgressSink` 若被多个 worker 并发调用，内部用 `threading.Lock` 保护回调列表，事件本身不可变。

5. **卡顿兜底**：复用 `_AnalysisFlowCard` 的 stall 计时器思路——模型 N 秒无 token 时在流式区显示「等待模型响应…」。

6. **生命周期**：worker 结束 `kind="stage", stage="finish"` 收尾；`_worker_failed` 时发 `kind="error"` 并标红。事件不落盘（落盘仍走既有 `analysis_history` / `request_log`）。

---

## 7. 风险与权衡

| 风险/权衡 | 处理 |
|---|---|
| token 高频刷 UI 导致卡顿 | 后端聚合节流 + 环形缓冲（§6.1/6.3） |
| 模型思考文本可能冗长/敏感 | 思考流只进「实时」展示区，不进默认首屏摘要（符合 PA UI 规范「原始/调试不进默认首屏」）；最终审计仍走 `request_log` |
| 与现有 `_overview` 一次性摘要的关系 | 建议「概览页 = 阶段流程图 + 流式过程 + 最终摘要」三段，最终摘要保留现状；是否拆成独立「实时推演」页待确认（§8 Q2） |
| `PAModelAdapter`（非流式 provider）无 token 流 | 优雅降级：只有阶段级事件，无思考流；不影响功能 |
| 改动横跨两个代码库 | 先二阶侧（可独立测试），再 PA 侧；接口用可选参数，向后兼容 |

---

## 8. 待你确认的开放问题

1. **流式文本区的位置**：直接嵌在「概览」页内（流程图下方），还是新建一个独立「实时推演」标签页？
2. **思考流的展示形态**：逐字滚动的思考流较长，是否需要「折叠/只显示最近一段 + 可展开」？
3. **历史回放**：是否需要在「历史回测」里回放某次推演的完整流式过程（需把事件序列落盘）？

---

## 9. 建议实施顺序（确认后执行）

1. 二阶侧 `progress.py`（`ProgressEvent` + `ProgressSink`）+ 单测。
2. 二阶侧 `PAChatClientAdapter` token 透传 + 单测。
3. 二阶侧 `PAEmbeddedService` / `ProductionOrchestrator` 阶段事件注入 + 单测。
4. PA 侧 `_ApiWorker` 信号升级 + `SecondOrderWorkspace` 流式区 + 节流。
5. 端到端手测：真实跑一次推演，观察概览页流式滚动与阶段卡片同步。
