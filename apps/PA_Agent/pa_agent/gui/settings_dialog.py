"""Settings dialog for PA Agent — edits all Settings fields via a form."""
from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from pa_agent.config.settings import Settings, save_settings
from pa_agent.config.paths import SETTINGS_JSON_PATH
from pa_agent.ai.cursor_connector import (
    is_openclaw_cs_model,
    should_use_cursor_provider,
)
from pa_agent.ai.qclaw_connector import (
    detect_qclaw,
    is_openclaw_model,
    should_use_qclaw_provider,
)
from pa_agent.ai.workbuddy_connector import (
    detect_workbuddy,
    is_openclaw_wb_model,
    should_use_workbuddy_provider,
)
from pa_agent.gui.widgets.toggle_switch import ToggleSwitch
from pa_agent.gui.widgets.shimmer_button import ShimmerButton

class SettingsDialog(QDialog):
    """Modal dialog that exposes all Settings fields as editable form widgets."""

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(520)
        self._settings = settings
        self._setup_ui()
        self._load_values()

    @staticmethod
    def _toggle_row(switch: ToggleSwitch, description: str) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(switch)
        layout.addWidget(QLabel(description))
        layout.addStretch()
        return row

    def _setup_ui(self) -> None:
        root_layout = QVBoxLayout(self)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        form_layout = QVBoxLayout(container)
        form_layout.setContentsMargins(8, 8, 8, 8)
        scroll.setWidget(container)
        root_layout.addWidget(scroll)

        provider_group = QGroupBox("AI 提供商")
        provider_form = QFormLayout(provider_group)

        self._model_edit = QLineEdit()
        provider_form.addRow("模型 (model):", self._model_edit)

        self._base_url_edit = QLineEdit()
        provider_form.addRow("Base URL:", self._base_url_edit)

        api_key_row = QHBoxLayout()
        self._api_key_edit = QLineEdit()
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Normal)
        self._api_key_edit.setPlaceholderText("输入 API Key")
        api_key_row.addWidget(self._api_key_edit)
        self._show_key_btn = QPushButton("隐藏")
        self._show_key_btn.setCheckable(True)
        self._show_key_btn.setFixedWidth(52)
        self._show_key_btn.toggled.connect(self._toggle_api_key_visibility)
        api_key_row.addWidget(self._show_key_btn)
        provider_form.addRow("API Key:", api_key_row)

        self._thinking_check = ToggleSwitch()
        provider_form.addRow("Thinking:", self._toggle_row(self._thinking_check, "启用 Thinking"))

        self._reasoning_effort_combo = QComboBox()
        self._reasoning_effort_combo.addItems(["low", "medium", "high", "max"])
        provider_form.addRow("Reasoning Effort:", self._reasoning_effort_combo)

        form_layout.addWidget(provider_group)

        general_group = QGroupBox("通用设置")
        general_form = QFormLayout(general_group)

        self._decision_conf_threshold_spin = QSpinBox()
        self._decision_conf_threshold_spin.setRange(0, 100)
        self._decision_conf_threshold_spin.setToolTip(
            "当阶段二 trade_confidence 低于此阈值时，即使 AI 输出了限价单/突破单/市价单，"
            "也不视为下单机会（不弹窗警报、决策页按「不下单」处理）。"
            "设为 0 可关闭此门槛。"
        )
        general_form.addRow("下单置信度门槛:", self._decision_conf_threshold_spin)

        self._decision_stance_combo = QComboBox()
        self._decision_stance_combo.addItem("保守", "conservative")
        self._decision_stance_combo.addItem("均衡（默认，比保守更愿意下单）", "balanced")
        self._decision_stance_combo.addItem("激进（比均衡更愿意下单）", "aggressive")
        self._decision_stance_combo.addItem(
            "极度激进（强制选方向与进场方式）",
            "extreme_aggressive",
        )
        self._decision_stance_combo.setToolTip(
            "仅影响阶段二交易决策倾向；保守与改版前一致。"
            "均衡、激进逐级提高下单意愿；极度激进在未触犯 §14 硬性禁止时"
            "必须给出具体做多/做空及限价/突破/市价方案。"
        )
        general_form.addRow("交易倾向:", self._decision_stance_combo)

        self._enable_next_bar_check = ToggleSwitch()
        self._enable_next_bar_check.setToolTip(
            "开启后，AI 会在每次分析中额外预测下一根K线方向，结果显示在「未来走势预期」中。\n"
            "关闭后 prompt 中移除该任务，不消耗额外 token。"
        )
        self._enable_next_bar_check.stateChanged.connect(self._on_enable_next_bar_changed)
        general_form.addRow(
            "下根K线预期:",
            self._toggle_row(self._enable_next_bar_check, "在「未来走势预期」中显示下根 K 线预期"),
        )

        self._analysis_bar_count_spin = QSpinBox()
        self._analysis_bar_count_spin.setRange(2, 5_000)
        self._analysis_bar_count_spin.setToolTip(
            "提交 AI 分析时使用的已收盘 K 线根数（不含当前未收盘 K 线）。"
            "图表实时刷新也会按此数量拉取显示。"
        )
        general_form.addRow("用于分析的 K 线数量:", self._analysis_bar_count_spin)

        self._refresh_interval_spin = QSpinBox()
        self._refresh_interval_spin.setRange(100, 10_000)
        self._refresh_interval_spin.setSuffix(" ms")
        general_form.addRow("刷新间隔:", self._refresh_interval_spin)

        self._auto_resume_chart_check = ToggleSwitch()
        self._auto_resume_chart_check.setToolTip(
            "提交分析时图表会暂停刷新并冻结为已收盘 K 线；"
            "勾选后，分析结束（成功或校验失败但流程已跑完）将自动恢复实时刷新，"
            "并重新显示最右侧未收盘空心 K 线。演示模式不受影响。"
        )
        general_form.addRow(
            "图表:",
            self._toggle_row(self._auto_resume_chart_check, "分析完成后自动恢复行情推送"),
        )

        self._keep_analysis_check = ShimmerButton()
        self._keep_analysis_check.setToolTip(
            "勾选后，每当有新的K线收盘时自动触发分析（与主界面「持续跟踪分析」勾选框同步）"
        )
        general_form.addRow(
            "持续跟踪分析:",
            self._toggle_row(self._keep_analysis_check, "有新 K 线收盘时自动开始新一轮分析"),
        )

        self._cancel_keep_on_retry_check = ToggleSwitch()
        self._cancel_keep_on_retry_check.setToolTip(
            "勾选后，当 AI 输出触发校验重试（stage1/stage2），"
            "自动关闭「持续跟踪分析」开关，停止后续自动分析。\n"
            "每次打开程序默认关闭。"
        )
        general_form.addRow(
            "重试行为:",
            self._toggle_row(self._cancel_keep_on_retry_check, "重试后取消持续跟踪分析"),
        )

        self._context_warning_spin = QSpinBox()
        self._context_warning_spin.setRange(1, 100)
        self._context_warning_spin.setSuffix(" %")
        general_form.addRow("上下文警告阈值:", self._context_warning_spin)

        self._stream_font_spin = QSpinBox()
        self._stream_font_spin.setRange(8, 28)
        self._stream_font_spin.setSuffix(" pt")
        self._stream_font_spin.setToolTip(
            "「实时」标签页中思考过程/撰写回答大文本框，以及下方追问输入框的字体大小"
        )
        general_form.addRow("实时窗口字号:", self._stream_font_spin)

        self._chart_seq_font_spin = QSpinBox()
        self._chart_seq_font_spin.setRange(6, 24)
        self._chart_seq_font_spin.setSuffix(" pt")
        self._chart_seq_font_spin.setToolTip("K 线图上 #1、#3… 序号标签的字体大小")
        general_form.addRow("图表K线序号字号:", self._chart_seq_font_spin)

        self._incremental_max_new_bars_spin = QSpinBox()
        self._incremental_max_new_bars_spin.setRange(0, 500)
        self._incremental_max_new_bars_spin.setSuffix(" 根")
        self._incremental_max_new_bars_spin.setToolTip(
            "同品种同周期下，若相对上一条成功记录只新增不超过该数量的已收盘K线，"
            "提交分析时走增量分析；设为 0 可关闭增量分析。"
        )
        general_form.addRow("增量分析最大新增K线:", self._incremental_max_new_bars_spin)

        self._last_symbol_edit = QLineEdit()
        general_form.addRow("上次品种:", self._last_symbol_edit)

        self._last_timeframe_edit = QLineEdit()
        general_form.addRow("上次周期:", self._last_timeframe_edit)

        self._alert_on_order_check = ToggleSwitch()
        self._alert_on_order_check.setToolTip(
            "当阶段二给出限价单、突破单或市价单时：播放系统提示音、弹出摘要对话框，"
            "并切换到右侧「决策」标签页；不再自动进入「决策树可视化」播放演示。"
            "勾选时会播放一次试听。"
        )
        self._alert_on_order_check.stateChanged.connect(self._on_alert_on_order_changed)
        general_form.addRow(
            "下单提醒:",
            self._toggle_row(self._alert_on_order_check, "有下单机会时发出警报音、弹窗并跳转到「决策」页"),
        )

        self._flow_auto_play_check = ToggleSwitch()
        self._flow_auto_play_check.setToolTip(
            "分析完成后自动切换到「决策树可视化」并播放路径动画。"
            "若已开启「下单提醒」且本轮有下单机会，则优先走下单提醒，不播放演示。"
        )
        general_form.addRow(
            "决策树播放:",
            self._toggle_row(self._flow_auto_play_check, "生成后自动播放生效路径"),
        )

        self._flow_play_seconds_spin = QSpinBox()
        self._flow_play_seconds_spin.setRange(3, 120)
        self._flow_play_seconds_spin.setSuffix(" 秒")
        general_form.addRow("播放时长:", self._flow_play_seconds_spin)

        self._flow_default_zoom_spin = QSpinBox()
        self._flow_default_zoom_spin.setRange(10, 9_999_999)
        self._flow_default_zoom_spin.setSuffix(" %")
        self._flow_default_zoom_spin.setToolTip(
            "相对「整图适配」视图：100% 与适配一致，50% 再缩小一半；"
            "可填任意更大百分比以放大（分析完成、播放路径、手动播放均用此比例）"
        )
        general_form.addRow("决策树可视化默认缩放:", self._flow_default_zoom_spin)

        self._flow_play_now_btn = QPushButton("播放决策树可视化")
        self._flow_play_now_btn.setToolTip(
            "使用当前已加载的决策路径重新播放动画（若尚未分析则无可播放内容）"
        )
        self._flow_play_now_btn.clicked.connect(self._on_play_decision_flow_now)
        general_form.addRow("", self._flow_play_now_btn)

        self._decision_flow_play_handler: Callable[[], None] | None = None

        form_layout.addWidget(general_group)

        futu_group = QGroupBox("Futu OpenD")
        futu_form = QFormLayout(futu_group)
        self._futu_host_edit = QLineEdit()
        self._futu_host_edit.setPlaceholderText("127.0.0.1")
        futu_form.addRow("主机:", self._futu_host_edit)
        self._futu_port_spin = QSpinBox()
        self._futu_port_spin.setRange(1, 65535)
        futu_form.addRow("端口:", self._futu_port_spin)
        futu_note = QLabel("富途 OpenD 不使用 API Key；请先启动并登录 OpenD。")
        futu_note.setWordWrap(True)
        futu_form.addRow("", futu_note)
        form_layout.addWidget(futu_group)

        second_order_group = QGroupBox("二阶博弈数据")
        second_order_form = QFormLayout(second_order_group)
        self._second_order_source_combo = QComboBox()
        self._second_order_source_combo.addItem("Futu OpenD（默认）", "futu")
        self._second_order_source_combo.addItem("AkShare（无需 Key）", "akshare")
        second_order_form.addRow("120 分钟行情源:", self._second_order_source_combo)
        tavily_row = QHBoxLayout()
        self._tavily_api_key_edit = QLineEdit()
        self._tavily_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._tavily_api_key_edit.setPlaceholderText("输入 Tavily API Key（可选）")
        tavily_row.addWidget(self._tavily_api_key_edit)
        self._show_tavily_key_btn = QPushButton("显示")
        self._show_tavily_key_btn.setCheckable(True)
        self._show_tavily_key_btn.setFixedWidth(52)
        self._show_tavily_key_btn.toggled.connect(
            lambda visible: self._tavily_api_key_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
            )
        )
        tavily_row.addWidget(self._show_tavily_key_btn)
        second_order_form.addRow("Tavily API Key:", tavily_row)
        second_order_note = QLabel(
            "Tavily 仅用于补充新闻材料；AkShare 通过公开接口调用，不需要 API Key。"
        )
        second_order_note.setWordWrap(True)
        second_order_form.addRow("", second_order_note)
        form_layout.addWidget(second_order_group)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        root_layout.addWidget(buttons)

    def _load_values(self) -> None:
        p = self._settings.provider
        g = self._settings.general

        self._model_edit.setText(p.model)
        self._base_url_edit.setText(p.base_url)
        self._api_key_edit.setText(p.api_key)
        self._thinking_check.setChecked(p.thinking)

        idx = self._reasoning_effort_combo.findText(p.reasoning_effort)
        if idx >= 0:
            self._reasoning_effort_combo.setCurrentIndex(idx)
        self._futu_host_edit.setText(self._settings.futu.opend_host)
        self._futu_port_spin.setValue(self._settings.futu.opend_port)
        self._tavily_api_key_edit.setText(self._settings.second_order.tavily_api_key)
        source_index = self._second_order_source_combo.findData(
            self._settings.second_order.market_data_source
        )
        self._second_order_source_combo.setCurrentIndex(max(source_index, 0))

        self._analysis_bar_count_spin.setValue(g.analysis_bar_count)
        self._refresh_interval_spin.setValue(g.refresh_interval_ms)
        self._auto_resume_chart_check.setChecked(
            bool(getattr(g, "auto_resume_chart_after_analysis", False))
        )
        self._keep_analysis_check.setChecked(
            bool(getattr(g, "keep_analysis", False))
        )
        self._cancel_keep_on_retry_check.setChecked(
            bool(getattr(g, "cancel_keep_analysis_on_retry", False))
        )
        self._context_warning_spin.setValue(int(g.context_warning_threshold_pct))
        self._stream_font_spin.setValue(int(getattr(g, "stream_pane_font_pt", 11)))
        self._chart_seq_font_spin.setValue(int(getattr(g, "chart_seq_label_font_pt", 11)))
        self._incremental_max_new_bars_spin.setValue(
            int(getattr(g, "incremental_max_new_bars", 10))
        )
        stance = getattr(g, "decision_stance", "conservative")
        stance_idx = self._decision_stance_combo.findData(stance)
        if stance_idx >= 0:
            self._decision_stance_combo.setCurrentIndex(stance_idx)
        self._enable_next_bar_check.blockSignals(True)
        self._enable_next_bar_check.setChecked(
            bool(getattr(g, "enable_next_bar_prediction", False))
        )
        self._enable_next_bar_check.blockSignals(False)
        self._decision_conf_threshold_spin.setValue(
            int(getattr(g, "decision_confidence_threshold", 40))
        )
        self._last_symbol_edit.setText(g.last_symbol)
        self._last_timeframe_edit.setText(g.last_timeframe)
        self._alert_on_order_check.blockSignals(True)
        self._alert_on_order_check.setChecked(
            bool(getattr(g, "alert_on_order_opportunity", True))
        )
        self._alert_on_order_check.blockSignals(False)
        self._flow_auto_play_check.setChecked(
            getattr(g, "decision_flow_auto_play", False)
        )
        self._flow_play_seconds_spin.setValue(
            getattr(g, "decision_flow_play_seconds", 50)
        )
        self._flow_default_zoom_spin.setValue(
            int(getattr(g, "decision_flow_default_zoom_pct", 600))
        )

    @staticmethod
    def _validate_provider_fields(model: str, base_url: str) -> str | None:
        """Return user-facing error text, or None if fields look consistent."""
        if is_openclaw_cs_model(model) or should_use_cursor_provider(model, base_url):
            return None
        if is_openclaw_model(model) or should_use_qclaw_provider(model, base_url):
            return None
        if is_openclaw_wb_model(model) or should_use_workbuddy_provider(model, base_url):
            return None
        if model.startswith(("http://", "https://")) and not base_url.startswith(
            ("http://", "https://")
        ):
            return (
                "「模型」与「Base URL」似乎填反了：\n"
                "• 模型应填模型名，如 deepseek-v4-pro 或 claude-sonnet-4-6\n"
                "• 使用 QClaw 时模型填 openclaw（或 openclaw/main）\n"
                "• 使用 Cursor 订阅时模型填 openclaw_cs\n"
                "• 使用 WorkBuddy 时模型填 openclaw_wb\n"
                "• Base URL 应填接口地址，如 https://api.deepseek.com"
            )
        if base_url.startswith(("http://", "https://")):
            return None
        if not base_url:
            if detect_qclaw():
                return (
                    "请填写 Base URL，或使用 QClaw/WorkBuddy：\n"
                    "• 模型填 openclaw → QClaw\n"
                    "• 模型填 openclaw_cs → Cursor 订阅（经 QClaw 网关）\n"
                    "• 模型填 openclaw_wb → WorkBuddy"
                )
            if detect_workbuddy():
                return (
                    "请填写 Base URL，或使用 WorkBuddy：\n"
                    "• 模型填 openclaw_wb（保存时自动配置 WorkBuddy 端点）"
                )
            return "请填写 Base URL（API 接口地址）。"
        return (
            f"Base URL 不是有效网址（当前：{base_url}）。\n"
            "DeepSeek 示例：https://api.deepseek.com\n"
            "PackyAPI 示例：https://www.packyapi.com/v1\n"
            "MiMo 示例：https://api.xiaomimimo.com/v1\n"
            "QClaw：模型填 openclaw 后点保存（自动配置本地网关）\n"
            "Cursor：模型填 openclaw_cs 后点保存（经 QClaw 走 Cursor 订阅）\n"
            "WorkBuddy：模型填 openclaw_wb 后点保存（自动配置 WorkBuddy）"
        )

    def _apply_cursor_provider(self, *, preferred_model: str = "") -> str | None:
        from pa_agent.ai.cursor_connector import apply_cursor_provider_to_settings

        return apply_cursor_provider_to_settings(
            self._settings,
            preferred_model=preferred_model or None,
        )

    def _apply_qclaw_provider(self, *, preferred_model: str = "") -> str | None:
        """Detect QClaw and write provider fields. Returns error text, or None."""
        from pa_agent.ai.qclaw_connector import apply_qclaw_provider_to_settings

        return apply_qclaw_provider_to_settings(
            self._settings,
            preferred_model=preferred_model or None,
        )

    def _apply_workbuddy_provider(self, *, preferred_model: str = "") -> str | None:
        """Detect WorkBuddy and write provider fields. Returns error text, or None."""
        from pa_agent.ai.workbuddy_connector import apply_workbuddy_provider_to_settings

        return apply_workbuddy_provider_to_settings(
            self._settings,
            preferred_model=preferred_model or None,
        )

    def _on_save(self) -> None:
        p = self._settings.provider
        g = self._settings.general

        model = self._model_edit.text().strip()
        base_url = self._base_url_edit.text().strip()
        api_key = self._api_key_edit.text().strip()

        # Explicit model aliases win over stale base_url (openclaw_wb before openclaw).
        if is_openclaw_wb_model(model) or should_use_workbuddy_provider(model, base_url):
            p.api_key = api_key
            wb_err = self._apply_workbuddy_provider(preferred_model=model)
            if wb_err:
                QMessageBox.warning(self, "WorkBuddy 配置异常", wb_err)
                return
        elif is_openclaw_cs_model(model) or should_use_cursor_provider(model, base_url):
            # Cursor route must keep the user-provided Cursor API key (crsr_...).
            p.api_key = api_key
            cs_err = self._apply_cursor_provider(preferred_model=model)
            if cs_err:
                QMessageBox.warning(self, "Cursor 配置异常", cs_err)
                return
        elif is_openclaw_model(model) or should_use_qclaw_provider(model, base_url):
            p.api_key = api_key
            qclaw_err = self._apply_qclaw_provider(preferred_model=model)
            if qclaw_err:
                QMessageBox.warning(self, "QClaw 配置异常", qclaw_err)
                return
        else:
            field_err = self._validate_provider_fields(model, base_url)
            if field_err:
                QMessageBox.warning(self, "AI 提供商配置有误", field_err)
                return

            p.model = model
            p.base_url = base_url
            p.api_key = api_key

        p.thinking = self._thinking_check.isChecked()
        p.reasoning_effort = self._reasoning_effort_combo.currentText()  # type: ignore[assignment]

        g.analysis_bar_count = self._analysis_bar_count_spin.value()
        g.refresh_interval_ms = self._refresh_interval_spin.value()
        g.auto_resume_chart_after_analysis = self._auto_resume_chart_check.isChecked()
        g.keep_analysis = self._keep_analysis_check.isChecked()
        g.cancel_keep_analysis_on_retry = self._cancel_keep_on_retry_check.isChecked()
        g.context_warning_threshold_pct = float(self._context_warning_spin.value())
        g.stream_pane_font_pt = self._stream_font_spin.value()
        g.chart_seq_label_font_pt = self._chart_seq_font_spin.value()
        g.incremental_max_new_bars = self._incremental_max_new_bars_spin.value()
        g.decision_stance = self._decision_stance_combo.currentData()  # type: ignore[assignment]
        g.enable_next_bar_prediction = self._enable_next_bar_check.isChecked()
        g.last_symbol = self._last_symbol_edit.text().strip()
        g.last_timeframe = self._last_timeframe_edit.text().strip()
        g.alert_on_order_opportunity = self._alert_on_order_check.isChecked()
        g.decision_flow_auto_play = self._flow_auto_play_check.isChecked()
        g.decision_flow_play_seconds = self._flow_play_seconds_spin.value()
        g.decision_flow_default_zoom_pct = self._flow_default_zoom_spin.value()
        g.decision_confidence_threshold = self._decision_conf_threshold_spin.value()
        self._settings.futu.opend_host = self._futu_host_edit.text().strip() or "127.0.0.1"
        self._settings.futu.opend_port = self._futu_port_spin.value()
        self._settings.second_order.tavily_api_key = self._tavily_api_key_edit.text().strip()
        self._settings.second_order.market_data_source = (
            self._second_order_source_combo.currentData()
        )

        save_settings(self._settings, SETTINGS_JSON_PATH)
        self.accept()

    def focus_api_key_field(self) -> None:
        """Focus the API Key field (e.g. when prompting on first launch)."""
        self._api_key_edit.setFocus(Qt.FocusReason.OtherFocusReason)
        self._api_key_edit.selectAll()

    def set_decision_flow_play_handler(self, handler: Callable[[], None] | None) -> None:
        """Register callback invoked when user clicks 播放决策树可视化."""
        self._decision_flow_play_handler = handler

    def _on_alert_on_order_changed(self, _state: int) -> None:
        if not self._alert_on_order_check.isChecked():
            return
        from pa_agent.gui.order_opportunity import play_order_alert_sound

        play_order_alert_sound()

    def _on_enable_next_bar_changed(self, state: int) -> None:
        """勾选开启下根K线预期时弹出提示。"""
        from PyQt6.QtCore import Qt as _Qt
        if state == _Qt.CheckState.Checked.value:
            QMessageBox.information(
                self,
                "下根K线预期",
                "下根K线预期难度大，结果仅供参考。\n\n"
                "AI 预测单根K线方向的准确率有限，请勿将其作为交易依据。",
            )

    def _on_play_decision_flow_now(self) -> None:
        # Allow previewing playback without pressing “保存”:
        # sync relevant fields from widgets into the in-memory settings object.
        g = self._settings.general
        g.decision_flow_auto_play = self._flow_auto_play_check.isChecked()
        g.decision_flow_play_seconds = self._flow_play_seconds_spin.value()
        g.decision_flow_default_zoom_pct = self._flow_default_zoom_spin.value()

        if self._decision_flow_play_handler is not None:
            self._decision_flow_play_handler()

    def _toggle_api_key_visibility(self, checked: bool) -> None:
        if checked:
            self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self._show_key_btn.setText("显示")
        else:
            self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Normal)
            self._show_key_btn.setText("隐藏")
