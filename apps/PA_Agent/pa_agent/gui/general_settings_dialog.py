"""通用设置对话框 — 包含交易决策、图表显示、分析行为等通用字段."""
from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pa_agent.config.settings import Settings, save_settings
from pa_agent.config.paths import SETTINGS_JSON_PATH
from pa_agent.gui.widgets.toggle_switch import ToggleSwitch
from pa_agent.gui.widgets.shimmer_button import ShimmerButton


class GeneralSettingsDialog(QDialog):
    """通用设置对话框 — 交易决策、图表、分析行为等."""

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("通用设置")
        self.setMinimumWidth(640)
        self._settings = settings
        self._decision_flow_play_handler: Callable[[], None] | None = None
        self._setup_ui()
        self._load_values()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        form_layout = QVBoxLayout(container)
        form_layout.setContentsMargins(4, 4, 4, 4)
        form_layout.setSpacing(12)
        scroll.setWidget(container)
        root.addWidget(scroll)

        # ── 交易决策 ──────────────────────────────────────────────────────────
        trade_group = QGroupBox("交易决策")
        trade_form = QFormLayout(trade_group)
        self._configure_form(trade_form)

        self._decision_conf_threshold_spin = QSpinBox()
        self._decision_conf_threshold_spin.setRange(0, 100)
        self._decision_conf_threshold_spin.setToolTip(
            "当阶段二 trade_confidence 低于此阈值时，即使 AI 输出了限价单/突破单/市价单，\n"
            "也不视为下单机会（不弹窗警报、决策页按「不下单」处理）。设为 0 可关闭此门槛。"
        )
        trade_form.addRow("下单置信度门槛:", self._decision_conf_threshold_spin)

        self._decision_stance_combo = QComboBox()
        self._decision_stance_combo.addItem("保守", "conservative")
        self._decision_stance_combo.addItem("均衡（默认，比保守更愿意下单）", "balanced")
        self._decision_stance_combo.addItem("激进（比均衡更愿意下单）", "aggressive")
        self._decision_stance_combo.addItem("极度激进（强制选方向与进场方式）", "extreme_aggressive")
        self._decision_stance_combo.setToolTip(
            "仅影响阶段二交易决策倾向；保守与改版前一致。\n"
            "均衡、激进逐级提高下单意愿；极度激进在未触犯 §14 硬性禁止时\n"
            "必须给出具体做多/做空及限价/突破/市价方案。"
        )
        trade_form.addRow("交易倾向:", self._decision_stance_combo)

        self._alert_on_order_check = ToggleSwitch()
        self._alert_on_order_check.stateChanged.connect(self._on_alert_on_order_changed)
        trade_form.addRow(
            "下单提醒:",
            self._toggle_row(self._alert_on_order_check, "有下单机会时发出警报音与弹窗"),
        )

        self._enable_next_bar_check = ToggleSwitch()
        self._enable_next_bar_check.setToolTip(
            "开启后，AI 会在每次分析中额外预测下一根K线方向。关闭后不消耗额外 token。"
        )
        self._enable_next_bar_check.stateChanged.connect(self._on_enable_next_bar_changed)
        trade_form.addRow(
            "下根K线预期:",
            self._toggle_row(self._enable_next_bar_check, "开启后在「未来走势预期」中显示"),
        )

        form_layout.addWidget(trade_group)

        # ── 分析行为 ──────────────────────────────────────────────────────────
        analysis_group = QGroupBox("分析行为")
        analysis_form = QFormLayout(analysis_group)
        self._configure_form(analysis_form)

        self._analysis_bar_count_spin = QSpinBox()
        self._analysis_bar_count_spin.setRange(2, 5_000)
        self._analysis_bar_count_spin.setToolTip(
            "提交 AI 分析时使用的已收盘 K 线根数（不含当前未收盘 K 线）。"
        )
        analysis_form.addRow("用于分析的 K 线数量:", self._analysis_bar_count_spin)

        self._incremental_max_new_bars_spin = QSpinBox()
        self._incremental_max_new_bars_spin.setRange(0, 500)
        self._incremental_max_new_bars_spin.setSuffix(" 根")
        self._incremental_max_new_bars_spin.setToolTip(
            "同品种同周期下，若相对上一条成功记录只新增不超过该数量的已收盘K线，\n"
            "提交分析时走增量分析；设为 0 可关闭增量分析。"
        )
        analysis_form.addRow("增量分析最大新增K线:", self._incremental_max_new_bars_spin)

        self._keep_analysis_check = ShimmerButton()
        self._keep_analysis_check.setToolTip(
            "勾选后，每当有新的K线收盘时自动触发分析（与主界面「持续跟踪分析」勾选框同步）"
        )
        analysis_form.addRow(
            "持续跟踪分析:",
            self._toggle_row(self._keep_analysis_check, "有新 K 线收盘时自动开始新一轮分析"),
        )

        self._analysis_pool_tracking_on_start_check = ShimmerButton()
        self._analysis_pool_tracking_on_start_check.setToolTip(
            "仅决定分析池工作区的启动默认状态；运行期间仍可用分析池按钮随时开始或停止。"
        )
        analysis_form.addRow(
            "分析池启动默认:",
            self._toggle_row(self._analysis_pool_tracking_on_start_check, "启动时自动开启分析池的持续分析"),
        )

        self._cancel_keep_on_retry_check = ToggleSwitch()
        self._cancel_keep_on_retry_check.setToolTip(
            "勾选后，当 AI 输出触发校验重试时，自动关闭「持续跟踪分析」开关。"
        )
        analysis_form.addRow(
            "重试行为:",
            self._toggle_row(self._cancel_keep_on_retry_check, "重试后取消持续分析"),
        )

        self._last_symbol_edit = QLineEdit()
        analysis_form.addRow("上次品种:", self._last_symbol_edit)

        self._last_timeframe_edit = QLineEdit()
        analysis_form.addRow("上次周期:", self._last_timeframe_edit)

        form_layout.addWidget(analysis_group)

        # ── 图表与界面 ────────────────────────────────────────────────────────
        ui_group = QGroupBox("图表与界面")
        ui_form = QFormLayout(ui_group)
        self._configure_form(ui_form)

        self._refresh_interval_spin = QSpinBox()
        self._refresh_interval_spin.setRange(100, 10_000)
        self._refresh_interval_spin.setSuffix(" ms")
        ui_form.addRow("刷新间隔:", self._refresh_interval_spin)

        self._auto_resume_chart_check = ToggleSwitch()
        ui_form.addRow(
            "图表:",
            self._toggle_row(self._auto_resume_chart_check, "分析完成后自动恢复行情推送"),
        )

        self._context_warning_spin = QSpinBox()
        self._context_warning_spin.setRange(1, 100)
        self._context_warning_spin.setSuffix(" %")
        ui_form.addRow("上下文警告阈值:", self._context_warning_spin)

        self._stream_font_spin = QSpinBox()
        self._stream_font_spin.setRange(8, 28)
        self._stream_font_spin.setSuffix(" pt")
        self._stream_font_spin.setToolTip("「实时」标签页思考/回答框及追问输入框的字体大小")
        ui_form.addRow("实时窗口字号:", self._stream_font_spin)

        self._chart_seq_font_spin = QSpinBox()
        self._chart_seq_font_spin.setRange(6, 24)
        self._chart_seq_font_spin.setSuffix(" pt")
        self._chart_seq_font_spin.setToolTip("K 线图上 #1、#3… 序号标签的字体大小")
        ui_form.addRow("图表K线序号字号:", self._chart_seq_font_spin)

        form_layout.addWidget(ui_group)

        # ── 决策树可视化 ──────────────────────────────────────────────────────
        flow_group = QGroupBox("决策树可视化")
        flow_form = QFormLayout(flow_group)
        self._configure_form(flow_form)

        self._flow_auto_play_check = ToggleSwitch()
        flow_form.addRow(
            "自动播放:",
            self._toggle_row(self._flow_auto_play_check, "生成后自动播放生效路径"),
        )

        self._flow_play_seconds_spin = QSpinBox()
        self._flow_play_seconds_spin.setRange(3, 120)
        self._flow_play_seconds_spin.setSuffix(" 秒")
        flow_form.addRow("播放时长:", self._flow_play_seconds_spin)

        self._flow_default_zoom_spin = QSpinBox()
        self._flow_default_zoom_spin.setRange(10, 9_999_999)
        self._flow_default_zoom_spin.setSuffix(" %")
        self._flow_default_zoom_spin.setToolTip(
            "相对「整图适配」视图：100% 与适配一致，500% 放大 5 倍"
        )
        flow_form.addRow("默认缩放:", self._flow_default_zoom_spin)

        self._flow_play_now_btn = QPushButton("播放决策树可视化")
        self._flow_play_now_btn.clicked.connect(self._on_play_decision_flow_now)
        flow_form.addRow("", self._flow_play_now_btn)

        form_layout.addWidget(flow_group)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
        if save_btn:
            save_btn.setText("保存")
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn:
            cancel_btn.setText("取消")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    @staticmethod
    def _configure_form(form: QFormLayout) -> None:
        form.setContentsMargins(0, 2, 0, 4)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

    @staticmethod
    def _toggle_row(switch: ToggleSwitch, description: str) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(switch)
        label = QLabel(description)
        label.setStyleSheet("color: #9AA5B1; font-size: 12px;")
        layout.addWidget(label)
        layout.addStretch()
        return row

    # ── 加载 / 保存 ────────────────────────────────────────────────────────────

    def _load_values(self) -> None:
        g = self._settings.general
        self._decision_conf_threshold_spin.setValue(
            int(getattr(g, "decision_confidence_threshold", 40))
        )
        stance = getattr(g, "decision_stance", "conservative")
        idx = self._decision_stance_combo.findData(stance)
        if idx >= 0:
            self._decision_stance_combo.setCurrentIndex(idx)
        self._alert_on_order_check.blockSignals(True)
        self._alert_on_order_check.setChecked(
            bool(getattr(g, "alert_on_order_opportunity", True))
        )
        self._alert_on_order_check.blockSignals(False)
        self._enable_next_bar_check.blockSignals(True)
        self._enable_next_bar_check.setChecked(
            bool(getattr(g, "enable_next_bar_prediction", False))
        )
        self._enable_next_bar_check.blockSignals(False)

        self._analysis_bar_count_spin.setValue(g.analysis_bar_count)
        self._incremental_max_new_bars_spin.setValue(
            int(getattr(g, "incremental_max_new_bars", 10))
        )
        self._keep_analysis_check.setChecked(bool(getattr(g, "keep_analysis", False)))
        self._analysis_pool_tracking_on_start_check.setChecked(
            bool(getattr(g, "analysis_pool_tracking_on_start", True))
        )
        self._cancel_keep_on_retry_check.setChecked(
            bool(getattr(g, "cancel_keep_analysis_on_retry", False))
        )
        self._last_symbol_edit.setText(g.last_symbol)
        self._last_timeframe_edit.setText(g.last_timeframe)

        self._refresh_interval_spin.setValue(g.refresh_interval_ms)
        self._auto_resume_chart_check.setChecked(
            bool(getattr(g, "auto_resume_chart_after_analysis", False))
        )
        self._context_warning_spin.setValue(int(g.context_warning_threshold_pct))
        self._stream_font_spin.setValue(int(getattr(g, "stream_pane_font_pt", 11)))
        self._chart_seq_font_spin.setValue(int(getattr(g, "chart_seq_label_font_pt", 11)))

        self._flow_auto_play_check.setChecked(
            getattr(g, "decision_flow_auto_play", False)
        )
        self._flow_play_seconds_spin.setValue(
            getattr(g, "decision_flow_play_seconds", 50)
        )
        self._flow_default_zoom_spin.setValue(
            int(getattr(g, "decision_flow_default_zoom_pct", 600))
        )

    def _on_save(self) -> None:
        g = self._settings.general
        g.decision_confidence_threshold = self._decision_conf_threshold_spin.value()
        g.decision_stance = self._decision_stance_combo.currentData()  # type: ignore[assignment]
        g.alert_on_order_opportunity = self._alert_on_order_check.isChecked()
        g.enable_next_bar_prediction = self._enable_next_bar_check.isChecked()

        g.analysis_bar_count = self._analysis_bar_count_spin.value()
        g.incremental_max_new_bars = self._incremental_max_new_bars_spin.value()
        g.keep_analysis = self._keep_analysis_check.isChecked()
        g.analysis_pool_tracking_on_start = (
            self._analysis_pool_tracking_on_start_check.isChecked()
        )
        g.cancel_keep_analysis_on_retry = self._cancel_keep_on_retry_check.isChecked()
        g.last_symbol = self._last_symbol_edit.text().strip()
        g.last_timeframe = self._last_timeframe_edit.text().strip()

        g.refresh_interval_ms = self._refresh_interval_spin.value()
        g.auto_resume_chart_after_analysis = self._auto_resume_chart_check.isChecked()
        g.context_warning_threshold_pct = float(self._context_warning_spin.value())
        g.stream_pane_font_pt = self._stream_font_spin.value()
        g.chart_seq_label_font_pt = self._chart_seq_font_spin.value()

        g.decision_flow_auto_play = self._flow_auto_play_check.isChecked()
        g.decision_flow_play_seconds = self._flow_play_seconds_spin.value()
        g.decision_flow_default_zoom_pct = self._flow_default_zoom_spin.value()

        save_settings(self._settings, SETTINGS_JSON_PATH)
        self.accept()

    # ── 辅助 ──────────────────────────────────────────────────────────────────

    def set_decision_flow_play_handler(self, handler: Callable[[], None] | None) -> None:
        self._decision_flow_play_handler = handler

    def _on_alert_on_order_changed(self, _state: int) -> None:
        if not self._alert_on_order_check.isChecked():
            return
        from pa_agent.gui.order_opportunity import play_order_alert_sound
        play_order_alert_sound()

    def _on_enable_next_bar_changed(self, state: int) -> None:
        from PyQt6.QtCore import Qt as _Qt
        if state == _Qt.CheckState.Checked.value:
            from PyQt6.QtWidgets import QMessageBox as _MB
            _MB.information(
                self,
                "下根K线预期",
                "下根K线预期难度大，结果仅供参考。\n\nAI 预测单根K线方向的准确率有限，请勿将其作为交易依据。",
            )

    def _on_play_decision_flow_now(self) -> None:
        g = self._settings.general
        g.decision_flow_auto_play = self._flow_auto_play_check.isChecked()
        g.decision_flow_play_seconds = self._flow_play_seconds_spin.value()
        g.decision_flow_default_zoom_pct = self._flow_default_zoom_spin.value()
        if self._decision_flow_play_handler is not None:
            self._decision_flow_play_handler()
