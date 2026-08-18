"""History analysis tab for one terminal."""
from __future__ import annotations

from datetime import datetime
from html import escape

from PyQt6.QtCore import QDate, QDateTime, QPoint, QSignalBlocker, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pa_agent.gui.theme import tokens as T
from pa_agent.records.history_analysis import (
    HistoryAnalysisEntry,
    HistoryTradeEvent,
    list_history_entries,
)
from pa_agent.records.trade_rules import (
    SETTLEMENT_T0,
    SETTLEMENT_T1,
    SETTLEMENT_UNSET,
    InstrumentTradeRuleStore,
    TradeEntryOverrideStore,
)

_HISTORY_TABLE_TEXT = T.FG
_HISTORY_SPIKE = "#FF4757"
_HISTORY_TABLE_UP = "#FF5353"
_HISTORY_TABLE_DOWN = "#00C087"


class _TradeEventLinkLabel(QLabel):
    """Event text with a connector rising from the source record below it."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setContentsMargins(24, 0, 0, 0)
        self.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setStyleSheet("color: #9AA5B1; font-size: 12px;")

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#646E7A"), 1))
        x = 9
        middle = self.height() // 2
        # The source record sits below this event: draw from the lower edge up,
        # then right toward the event text.
        painter.drawLine(x, self.height(), x, middle)
        painter.drawLine(x, middle, x + 10, middle)
        painter.end()


class HistoryPanel(QWidget):
    """Filterable per-symbol analysis history list."""

    record_selected = pyqtSignal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        trade_rule_store: InstrumentTradeRuleStore | None = None,
        entry_override_store: TradeEntryOverrideStore | None = None,
    ) -> None:
        super().__init__(parent)
        self._symbol = ""
        self._entries: list[HistoryAnalysisEntry] = []
        self._trade_rule_store = trade_rule_store or InstrumentTradeRuleStore()
        self._entry_override_store = entry_override_store or TradeEntryOverrideStore()
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        filters = QHBoxLayout()
        filters.addWidget(QLabel("周期:"))
        self._timeframe_combo = QComboBox()
        self._timeframe_combo.addItems(["全部", "1m", "5m", "15m", "1h", "4h", "1d"])
        self._timeframe_combo.currentTextChanged.connect(lambda _text: self.refresh())
        filters.addWidget(self._timeframe_combo)

        filters.addWidget(QLabel("交易规则:"))
        self._settlement_combo = QComboBox()
        self._settlement_combo.setObjectName("historySettlementMode")
        self._settlement_combo.addItem("未设置", SETTLEMENT_UNSET)
        self._settlement_combo.addItem("T+0", SETTLEMENT_T0)
        self._settlement_combo.addItem("T+1", SETTLEMENT_T1)
        self._settlement_combo.setToolTip(
            "按当前品种保存。未设置时不评估历史计划的止盈或止损。"
        )
        self._settlement_combo.setEnabled(False)
        self._settlement_combo.currentIndexChanged.connect(self._on_settlement_mode_changed)
        filters.addWidget(self._settlement_combo)

        self._entry_flexible_check = QCheckBox("灵活入场")
        self._entry_flexible_check.setToolTip(
            "按当前品种保存。开启后，限价单允许在入场价外侧 N 个最小跳动内触发。"
        )
        self._entry_flexible_check.toggled.connect(self._on_entry_flexible_changed)
        filters.addWidget(self._entry_flexible_check)

        self._entry_tolerance_spin = QSpinBox()
        self._entry_tolerance_spin.setRange(1, 100)
        self._entry_tolerance_spin.setValue(1)
        self._entry_tolerance_spin.setSuffix(" 跳")
        self._entry_tolerance_spin.setToolTip("灵活入场允许偏离计划价的最小跳动数量")
        self._entry_tolerance_spin.valueChanged.connect(self._on_entry_tolerance_changed)
        self._entry_flexible_check.setEnabled(False)
        self._entry_tolerance_spin.setEnabled(False)
        filters.addWidget(self._entry_tolerance_spin)

        self._date_filter_check = QCheckBox("时间段")
        self._date_filter_check.toggled.connect(self._on_date_filter_toggled)
        filters.addWidget(self._date_filter_check)

        self._start_date = QDateEdit()
        self._start_date.setObjectName("historyStartDate")
        self._start_date.setCalendarPopup(True)
        self._start_date.setDisplayFormat("yyyy-MM-dd")
        self._start_date.setDate(QDate.currentDate().addMonths(-1))
        self._start_date.setToolTip("选择筛选起始日期")
        self._start_date.dateChanged.connect(self._on_start_date_changed)
        filters.addWidget(self._start_date)

        filters.addWidget(QLabel("至"))
        self._end_date = QDateEdit()
        self._end_date.setObjectName("historyEndDate")
        self._end_date.setCalendarPopup(True)
        self._end_date.setDisplayFormat("yyyy-MM-dd")
        self._end_date.setDate(QDate.currentDate())
        self._end_date.setToolTip("选择筛选结束日期")
        self._end_date.dateChanged.connect(self._on_end_date_changed)
        filters.addWidget(self._end_date)

        reset_btn = QPushButton("重置")
        reset_btn.clicked.connect(self.reset_filters)
        filters.addWidget(reset_btn)
        filters.addStretch()
        layout.addLayout(filters)

        self._table = QTableWidget()
        self._table.setColumnCount(9)
        self._table.setHorizontalHeaderLabels(
            ("日期 / 时间 / 周期", "决策", "收盘价", "EP", "TP", "SL", "支撑", "阻力", "趋势 / 市场周期")
        )
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_history_context_menu)
        self._table.verticalHeader().setVisible(False)
        self._table.itemClicked.connect(self._on_item_clicked)
        header = self._table.horizontalHeader()
        for column in range(9):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(8, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(2, 165)
        layout.addWidget(self._table, 1)

        self._empty_label = QLabel("")
        self._empty_label.setObjectName("mutedLabel")
        layout.addWidget(self._empty_label)
        self._on_date_filter_toggled(False)

    def set_symbol(self, symbol: str) -> None:
        self._symbol = str(symbol or "").strip()
        self._sync_settlement_mode()
        self._sync_entry_tolerance()
        self.refresh()

    def reset_filters(self) -> None:
        self._timeframe_combo.setCurrentText("全部")
        self._date_filter_check.setChecked(False)
        self.refresh()

    def clear_selection(self) -> None:
        """Clear the viewed-record accent before a new live analysis starts."""
        self._table.clearSelection()
        self._table.setCurrentItem(None)

    def _on_date_filter_toggled(self, enabled: bool) -> None:
        self._start_date.setEnabled(enabled)
        self._end_date.setEnabled(enabled)
        self.refresh()

    def _on_start_date_changed(self, date: QDate) -> None:
        if date > self._end_date.date():
            with QSignalBlocker(self._end_date):
                self._end_date.setDate(date)
        self.refresh()

    def _on_end_date_changed(self, date: QDate) -> None:
        if date < self._start_date.date():
            with QSignalBlocker(self._start_date):
                self._start_date.setDate(date)
        self.refresh()

    def _sync_settlement_mode(self) -> None:
        self._settlement_combo.setEnabled(bool(self._symbol))
        mode = self._trade_rule_store.mode_for(self._symbol)
        index = self._settlement_combo.findData(mode)
        with QSignalBlocker(self._settlement_combo):
            self._settlement_combo.setCurrentIndex(max(index, 0))

    def _on_settlement_mode_changed(self, _index: int) -> None:
        if not self._symbol:
            return
        self._trade_rule_store.set_mode(
            self._symbol,
            self._settlement_combo.currentData(),
        )
        self.refresh()

    def _sync_entry_tolerance(self) -> None:
        ticks = self._trade_rule_store.entry_tolerance_ticks_for(self._symbol)
        with QSignalBlocker(self._entry_flexible_check), QSignalBlocker(
            self._entry_tolerance_spin
        ):
            self._entry_flexible_check.setEnabled(bool(self._symbol))
            self._entry_flexible_check.setChecked(ticks > 0)
            self._entry_tolerance_spin.setValue(max(ticks, 1))
            self._entry_tolerance_spin.setEnabled(bool(self._symbol) and ticks > 0)

    def _on_entry_flexible_changed(self, enabled: bool) -> None:
        self._entry_tolerance_spin.setEnabled(enabled and bool(self._symbol))
        if not self._symbol:
            return
        self._trade_rule_store.set_entry_tolerance_ticks(
            self._symbol,
            self._entry_tolerance_spin.value() if enabled else 0,
        )
        self.refresh()

    def _on_entry_tolerance_changed(self, value: int) -> None:
        if self._symbol and self._entry_flexible_check.isChecked():
            self._trade_rule_store.set_entry_tolerance_ticks(self._symbol, value)
            self.refresh()

    def _date_to_start_ms(self, date: QDate) -> int:
        dt = datetime(date.year(), date.month(), date.day(), 0, 0, 0)
        return int(dt.timestamp() * 1000)

    def _date_to_end_ms(self, date: QDate) -> int:
        dt = datetime(date.year(), date.month(), date.day(), 23, 59, 59, 999000)
        return int(dt.timestamp() * 1000)

    def refresh(self) -> None:
        timeframe = self._timeframe_combo.currentText()
        tf_filter = None if timeframe == "全部" else timeframe
        start_ms = end_ms = None
        if self._date_filter_check.isChecked():
            start_ms = self._date_to_start_ms(self._start_date.date())
            end_ms = self._date_to_end_ms(self._end_date.date())

        entries = list_history_entries(
            symbol=self._symbol or None,
            timeframe=tf_filter,
            start_ms=start_ms,
            end_ms=end_ms,
            settlement_mode=str(self._settlement_combo.currentData() or SETTLEMENT_UNSET),
            entry_tolerance_ticks=self._trade_rule_store.entry_tolerance_ticks_for(
                self._symbol
            ),
            entry_overrides=self._entry_override_store.all_overrides(),
        )
        self._entries = entries
        self._table.blockSignals(True)
        try:
            self._table.clearSpans()
            self._table.clearContents()
            self._table.setRowCount(len(entries))
            for row, entry in enumerate(entries):
                if entry.row_kind == "trade_event" and entry.trade_event is not None:
                    self._populate_trade_event_row(row, entry)
                    continue
                time_item = QTableWidgetItem(
                    f"{self._format_timestamp(entry.timestamp_ms)} / {entry.timeframe}"
                )
                time_item.setData(Qt.ItemDataRole.UserRole, str(entry.path))
                self._table.setItem(row, 0, time_item)
                override = self._entry_override_store.override_for(entry.path)
                decision_label = entry.decision_label
                if override is not None:
                    decision_label += "（手动已下单）"
                self._table.setItem(row, 1, QTableWidgetItem(decision_label))
                self._table.setCellWidget(row, 2, self._price_label(entry))
                self._table.setItem(row, 3, QTableWidgetItem(self._format_price(entry.entry_price)))
                self._table.setItem(row, 4, self._target_item(entry, "tp"))
                self._table.setItem(row, 5, self._target_item(entry, "sl"))
                self._table.setItem(row, 6, QTableWidgetItem(" / ".join(entry.support_levels) or "-"))
                self._table.setItem(row, 7, QTableWidgetItem(" / ".join(entry.resistance_levels) or "-"))
                trend_text = escape(f"{entry.trend_label} / {entry.cycle_label}")
                if "尖峰" in str(entry.cycle_label):
                    trend_text = trend_text.replace("尖峰", f'<span style="color:{_HISTORY_SPIKE};">尖峰</span>')
                trend_label = QLabel(trend_text)
                trend_label.setStyleSheet(f"color: {_HISTORY_TABLE_TEXT};")
                self._table.setCellWidget(row, 8, trend_label)
        finally:
            self._table.blockSignals(False)
        for column in (3, 4, 5):
            self._table.resizeColumnToContents(column)
        price_width = max(self._table.columnWidth(column) for column in (3, 4, 5))
        for column in (3, 4, 5):
            self._table.setColumnWidth(column, price_width)
        self._empty_label.setText("" if entries else "暂无符合条件的历史分析记录")

    def _populate_trade_event_row(self, row: int, entry: HistoryAnalysisEntry) -> None:
        event = entry.trade_event
        if event is None:
            return
        source_item = QTableWidgetItem()
        source_item.setData(Qt.ItemDataRole.UserRole, str(entry.path))
        source_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        self._table.setItem(row, 0, source_item)
        link = _TradeEventLinkLabel(
            f"{self._format_timestamp(event.timestamp_ms)} / 盈损"
        )
        self._table.setCellWidget(row, 0, link)

        event_item = QTableWidgetItem()
        event_item.setData(Qt.ItemDataRole.UserRole, str(entry.path))
        event_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        self._table.setItem(row, 1, event_item)
        self._table.setSpan(row, 1, 1, 8)
        event_label = QLabel(self._trade_event_html(event))
        event_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        event_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        event_label.setWordWrap(True)
        event_label.setContentsMargins(6, 2, 6, 2)
        event_label.setStyleSheet(
            "background: #161C22; border-left: 1px solid #38424E; "
            "border-radius: 2px; font-size: 12px;"
        )
        self._table.setCellWidget(row, 1, event_label)
        self._table.setRowHeight(row, 46 if event.blocked_touches else 32)

    def _trade_event_html(self, event: HistoryTradeEvent) -> str:
        blocked = ""
        if event.blocked_touches:
            first_touch = event.blocked_touches[0]
            touch_text = (
                f"{self._format_timestamp(first_touch.timestamp_ms)} 触及"
                f"{'止盈' if first_touch.outcome == 'tp' else '止损'} "
                f"{self._format_price(first_touch.price)}（当日不可平仓）"
            )
            if len(event.blocked_touches) > 1:
                touch_text += "..."
            blocked = (
                f'<span style="color:#C0913C;">{touch_text}</span> '
            )

        if event.outcome == "tp":
            result = (
                f'<span style="color:{_HISTORY_TABLE_UP};">最终止盈 '
                f'{self._format_price(event.exit_price)} '
                f'+{event.return_pct:.2f}%</span>'
            )
        elif event.outcome == "sl":
            result = (
                f'<span style="color:{_HISTORY_TABLE_DOWN};">最终止损 '
                f'{self._format_price(event.exit_price)} '
                f'{event.return_pct:.2f}%</span>'
            )
        else:
            result = '<span style="color:#C0913C;">等待下一交易日确认最终结果</span>'
        return blocked + result

    @staticmethod
    def _format_timestamp(timestamp_ms: int) -> str:
        return datetime.fromtimestamp(timestamp_ms / 1000).strftime("%m-%d / %H:%M:%S")

    @staticmethod
    def _format_price(value: float | None) -> str:
        if value is None:
            return "-"
        return f"{value:.8f}".rstrip("0").rstrip(".")

    def _price_label(self, entry: HistoryAnalysisEntry) -> QLabel:
        label = QLabel()
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        price = self._format_price(getattr(entry, "current_price", None))
        price_html = escape(price)

        change = getattr(entry, "price_change", None)
        change_pct = getattr(entry, "price_change_pct", None)
        if change is None or abs(change) < 1e-12:
            change_html = (
                ' <span style="font-size:small; color:#646E7A;">'
                '0.00%</span>'
            )
        elif change > 0:
            change_html = (
                f' <span style="font-size:small; color:{_HISTORY_TABLE_UP};">'
                f'+{change_pct:.2f}%</span>'
            )
        else:
            change_html = (
                f' <span style="font-size:small; color:{_HISTORY_TABLE_DOWN};">'
                f'-{abs(change_pct):.2f}%</span>'
            )
        label.setText(price_html + change_html)
        return label

    def _target_item(self, entry: HistoryAnalysisEntry, target: str) -> QTableWidgetItem:
        value = entry.take_profit_price if target == "tp" else entry.stop_loss_price
        return QTableWidgetItem(self._format_price(value))

    def _on_item_clicked(self, item: QTableWidgetItem) -> None:
        path_item = self._table.item(item.row(), 0)
        path = path_item.data(Qt.ItemDataRole.UserRole) if path_item is not None else None
        if isinstance(path, str) and path:
            self.record_selected.emit(path)

    def _show_history_context_menu(self, position: QPoint) -> None:
        index = self._table.indexAt(position)
        if not index.isValid() or index.row() >= len(self._entries):
            return
        entry = self._entries[index.row()]
        if entry.row_kind != "analysis" or entry.entry_price is None:
            return

        menu = QMenu(self)
        override = self._entry_override_store.override_for(entry.path)
        if override is None:
            action = menu.addAction("设置为已下单…")
            action.triggered.connect(lambda: self._set_manual_entry_override(entry))
        else:
            action = menu.addAction("取消手动已下单")
            action.triggered.connect(lambda: self._clear_manual_entry_override(entry.path))
        menu.exec(self._table.viewport().mapToGlobal(position))

    def _set_manual_entry_override(self, entry: HistoryAnalysisEntry) -> None:
        override = self._entry_override_store.override_for(entry.path)
        dialog = QDialog(self)
        dialog.setWindowTitle("设置实际成交")
        form = QFormLayout(dialog)

        timestamp_edit = QDateTimeEdit(
            QDateTime.fromMSecsSinceEpoch(
                override.timestamp_ms if override is not None else entry.timestamp_ms
            )
        )
        timestamp_edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        timestamp_edit.setCalendarPopup(True)
        form.addRow("成交时间", timestamp_edit)

        price_spin = QDoubleSpinBox()
        price_spin.setRange(0.00000001, 1000000000.0)
        price_spin.setDecimals(8)
        price_spin.setSingleStep(0.001)
        price_spin.setValue(override.price if override is not None else entry.entry_price)
        form.addRow("成交价", price_spin)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self._entry_override_store.set_override(
            entry.path,
            timestamp_ms=int(timestamp_edit.dateTime().toMSecsSinceEpoch()),
            price=float(price_spin.value()),
        )
        self.refresh()

    def _clear_manual_entry_override(self, path) -> None:
        self._entry_override_store.clear_override(path)
        self.refresh()
