"""Workspace window hosting the watchlist, analysis pool, and stock terminals."""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from PyQt6.QtCore import QEvent, QPoint, QRect, QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QBrush, QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QMenu,
    QPushButton,
    QRubberBand,
    QSplitter,
    QTabBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolTip,
    QVBoxLayout,
    QWidget,
    QToolButton,
)

from pa_agent.app_context import AppContext
from pa_agent.gui.futu_symbol_help import FUTU_SYMBOL_FORMAT_HELP
from pa_agent.records.watchlist import WatchlistItem, WatchlistStore

logger = logging.getLogger(__name__)

_QUOTE_REFRESH_SECONDS = 3.0
_QUOTE_RETRY_SECONDS = 20.0

_TIMEFRAMES = ("1m", "5m", "15m", "30m", "1h", "4h", "1d")
_EXCHANGE_LABELS: dict[str, str] = {
    "SSE": "SSE（A股）",
    "SZSE": "SZSE（A股）",
    "HKEX": "HKEX（港股）",
    "NYSE": "NYSE（美股）",
    "NASDAQ": "NASDAQ（美股）",
    "SP": "SP（美股指数）",
    "OANDA": "OANDA（外汇）",
    "PEPPERSTONE": "PEPPERSTONE（外汇）",
    "FOREXCOM": "FOREXCOM（外汇）",
    "FX": "FX（外汇）",
    "TVC": "TVC（商品/指数）",
    "CAPITALCOM": "CAPITALCOM（商品/外汇）",
    "CBOT": "CBOT（期货）",
    "CME_MINI": "CME_MINI（期货）",
    "": "（自动）",
}


class _WatchlistQuoteWorker(QThread):
    """Keep lightweight market-data connections for unopened watchlist items."""

    quote_ready = pyqtSignal(str, object, object)

    def __init__(self, items_provider: Callable[[], tuple[WatchlistItem, ...]]) -> None:
        super().__init__()
        self._items_provider = items_provider
        self._stop_event = threading.Event()
        self._sources: dict[str, tuple[tuple[str, str, str, str], object]] = {}
        self._failed_until: dict[str, float] = {}

    def stop(self) -> None:
        self._stop_event.set()
        for _signature, source in tuple(self._sources.values()):
            close_socket = getattr(source, "_close_tv_socket", None)
            if callable(close_socket):
                try:
                    close_socket()
                except Exception:  # noqa: BLE001
                    pass

    @staticmethod
    def _signature(item: WatchlistItem) -> tuple[str, str, str, str]:
        return (item.data_source, item.exchange, item.symbol, item.timeframe)

    @staticmethod
    def _close_source(source: object) -> None:
        try:
            source.unsubscribe()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
        try:
            source.disconnect()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    def _source_for(self, item: WatchlistItem) -> object:
        signature = self._signature(item)
        existing = self._sources.get(item.id)
        if existing is not None and existing[0] == signature:
            return existing[1]
        if existing is not None:
            self._close_source(existing[1])

        from pa_agent.data.factory import create_data_source

        source = create_data_source(item.data_source)
        try:
            source.connect()
            set_exchange = getattr(source, "set_exchange", None)
            if callable(set_exchange):
                set_exchange(item.exchange)
            source.subscribe(item.symbol, item.timeframe)
        except Exception:
            self._close_source(source)
            raise
        self._sources[item.id] = (signature, source)
        return source

    @staticmethod
    def _read_quote(source: object) -> tuple[float | None, float | None]:
        bars = list(source.latest_snapshot(2))  # type: ignore[attr-defined]
        summary: dict = {}
        getter = getattr(source, "latest_market_summary", None)
        if callable(getter):
            raw = getter()
            if isinstance(raw, dict):
                summary = raw

        newest = bars[0] if bars else None
        price = summary.get("last_price")
        if price is None and newest is not None:
            price = getattr(newest, "close", None)
        change_rate = summary.get("change_rate")
        if change_rate is None and newest is not None:
            change_rate = getattr(newest, "pct_chg", None)
        if change_rate is None and newest is not None:
            open_price = getattr(newest, "open", None)
            try:
                if float(open_price) != 0:
                    change_rate = (float(price) - float(open_price)) / float(open_price) * 100.0
            except (TypeError, ValueError):
                pass
        try:
            price_value = float(price) if price is not None else None
        except (TypeError, ValueError):
            price_value = None
        try:
            change_value = float(change_rate) if change_rate is not None else None
        except (TypeError, ValueError):
            change_value = None
        return price_value, change_value

    def run(self) -> None:
        try:
            while not self._stop_event.is_set():
                items = tuple(self._items_provider())
                active_ids = {item.id for item in items}
                for stale_id in set(self._sources) - active_ids:
                    _signature, source = self._sources.pop(stale_id)
                    self._close_source(source)
                    self._failed_until.pop(stale_id, None)

                now = time.monotonic()
                for item in items:
                    if self._stop_event.is_set():
                        break
                    if now < self._failed_until.get(item.id, 0.0):
                        continue
                    try:
                        source = self._source_for(item)
                        price, change_rate = self._read_quote(source)
                        self.quote_ready.emit(item.id, price, change_rate)
                        self._failed_until.pop(item.id, None)
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("Watchlist quote failed for %s: %s", item.symbol, exc)
                        failed = self._sources.pop(item.id, None)
                        if failed is not None:
                            self._close_source(failed[1])
                        self._failed_until[item.id] = now + _QUOTE_RETRY_SECONDS
                self._stop_event.wait(_QUOTE_REFRESH_SECONDS)
        finally:
            for _signature, source in tuple(self._sources.values()):
                self._close_source(source)
            self._sources.clear()


class _TerminalTabBar(QTabBar):
    """Scrollable analysis tabs with bounded wheel and drag navigation."""

    _DRAG_STEP_PX = 18

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._drag_last_x: int | None = None
        self._drag_remainder = 0
        self._dragging = False
        self._scroll_drag_button: Qt.MouseButton | None = None

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        is_blank_left_drag = (
            event.button() == Qt.MouseButton.LeftButton and self.tabAt(event.pos()) < 0
        )
        if event.button() == Qt.MouseButton.MiddleButton or is_blank_left_drag:
            self._drag_last_x = event.position().toPoint().x()
            self._drag_remainder = 0
            self._dragging = False
            self._scroll_drag_button = event.button()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if (
            self._drag_last_x is None
            or self._scroll_drag_button is None
            or not event.buttons() & self._scroll_drag_button
        ):
            super().mouseMoveEvent(event)
            return
        current_x = event.position().toPoint().x()
        movement = current_x - self._drag_last_x
        self._drag_last_x = current_x
        self._drag_remainder += movement
        if abs(self._drag_remainder) < self._DRAG_STEP_PX:
            return
        self._dragging = True
        direction = -1 if self._drag_remainder > 0 else 1
        steps = abs(self._drag_remainder) // self._DRAG_STEP_PX
        if self._drag_remainder > 0:
            self._drag_remainder -= steps * self._DRAG_STEP_PX
        else:
            self._drag_remainder += steps * self._DRAG_STEP_PX
        for _ in range(steps):
            if not self._scroll(direction):
                self._drag_remainder = 0
                break
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        is_scroll_release = event.button() == self._scroll_drag_button
        self._drag_last_x = None
        self._drag_remainder = 0
        self._dragging = False
        self._scroll_drag_button = None
        if is_scroll_release:
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        delta = event.angleDelta().y()
        if not delta:
            event.ignore()
            return
        self._scroll(-1 if delta > 0 else 1)
        event.accept()

    def _scroll(self, direction: int) -> bool:
        object_name = "ScrollLeftButton" if direction < 0 else "ScrollRightButton"
        button = next(
            (
                child
                for child in self.findChildren(QToolButton)
                if child.objectName() == object_name
            ),
            None,
        )
        if button is None or not button.isEnabled():
            return False
        button.click()
        return True


class _LegacyWatchlistDashboard(QWidget):
    """The first workspace tab, switching between watchlist and analysis pool."""

    show_terminal_requested = pyqtSignal(str)
    add_to_pool_requested = pyqtSignal(list)
    batch_analysis_requested = pyqtSignal(list)
    remove_from_pool_requested = pyqtSignal(list)
    continuous_tracking_requested = pyqtSignal(bool)

    def __init__(self, store: WatchlistStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self._showing_pool = False
        self._pool_tracking_enabled = False
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        self._title = QLabel("自选股列表")
        self._title.setStyleSheet("font-size: 18px; font-weight: 600;")
        header.addWidget(self._title)
        header.addStretch()
        self._switch_button = QPushButton("分析池")
        self._switch_button.setMinimumWidth(110)
        self._switch_button.clicked.connect(self._toggle_page)
        header.addWidget(self._switch_button)
        layout.addLayout(header)

        self._entry_form = QWidget()
        form = QHBoxLayout(self._entry_form)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("股票名称")
        self._name_edit.setMinimumWidth(130)
        self._symbol_edit = QLineEdit()
        self._symbol_edit.setPlaceholderText("股票代码")
        self._symbol_edit.setMinimumWidth(130)
        self._data_source_combo = QComboBox()
        from pa_agent.data.factory import DATA_SOURCE_CHOICES

        for kind, label in DATA_SOURCE_CHOICES:
            self._data_source_combo.addItem(label, kind)
        tv_index = self._data_source_combo.findData("tradingview")
        self._data_source_combo.setCurrentIndex(max(tv_index, 0))
        self._exchange_combo = QComboBox()
        from pa_agent.data.tradingview import TV_EXCHANGE_PRESETS

        for exchange in TV_EXCHANGE_PRESETS:
            self._exchange_combo.addItem(_EXCHANGE_LABELS.get(exchange, exchange), exchange)
        self._timeframe_combo = QComboBox()
        self._timeframe_combo.addItems(_TIMEFRAMES)
        self._timeframe_combo.setCurrentText("15m")
        self._save_item_button = QPushButton("加入自选股")
        self._save_item_button.setObjectName("primaryButton")
        self._save_item_button.clicked.connect(self._add_watchlist_item)
        for label, widget in (
            ("名称", self._name_edit),
            ("代码", self._symbol_edit),
            ("数据来源", self._data_source_combo),
            ("交易所", self._exchange_combo),
            ("周期", self._timeframe_combo),
        ):
            form.addWidget(QLabel(f"{label}:"))
            form.addWidget(widget)
        form.addWidget(self._save_item_button)
        layout.addWidget(self._entry_form)

        self._table = QTableWidget()
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.cellClicked.connect(self._on_cell_clicked)
        layout.addWidget(self._table, 1)

        self._watchlist_actions = QWidget()
        watch_actions = QHBoxLayout(self._watchlist_actions)
        watch_actions.setContentsMargins(0, 0, 0, 0)
        self._add_pool_button = QPushButton("加入分析池")
        self._add_pool_button.clicked.connect(
            lambda: self.add_to_pool_requested.emit(self._selected_item_ids())
        )
        self._batch_analysis_button = QPushButton("一键批量分析")
        self._batch_analysis_button.setObjectName("primaryButton")
        self._batch_analysis_button.clicked.connect(
            lambda: self.batch_analysis_requested.emit(self._selected_item_ids())
        )
        watch_actions.addWidget(self._add_pool_button)
        watch_actions.addWidget(self._batch_analysis_button)
        watch_actions.addStretch()
        layout.addWidget(self._watchlist_actions)

        self._pool_actions = QWidget()
        pool_actions = QHBoxLayout(self._pool_actions)
        pool_actions.setContentsMargins(0, 0, 0, 0)
        self._remove_pool_button = QPushButton("移出股票")
        self._remove_pool_button.clicked.connect(
            lambda: self.remove_from_pool_requested.emit(self._selected_item_ids())
        )
        self._continuous_button = QPushButton("开始持续跟踪分析")
        self._continuous_button.setStyleSheet(
            "background-color: #00D084; color: white; font-weight: 600;"
        )
        self._continuous_button.clicked.connect(self._toggle_continuous_tracking)
        pool_actions.addWidget(self._remove_pool_button)
        pool_actions.addWidget(self._continuous_button)
        pool_actions.addStretch()
        layout.addWidget(self._pool_actions)

    def _toggle_page(self) -> None:
        self._showing_pool = not self._showing_pool
        self._entry_form.setVisible(not self._showing_pool)
        self._watchlist_actions.setVisible(not self._showing_pool)
        self._pool_actions.setVisible(self._showing_pool)
        self._title.setText("分析池" if self._showing_pool else "自选股列表")
        self._switch_button.setText("自选股列表" if self._showing_pool else "分析池")
        self.refresh()

    def _add_watchlist_item(self) -> None:
        try:
            self._store.add(
                name=self._name_edit.text(),
                symbol=self._symbol_edit.text(),
                data_source=str(self._data_source_combo.currentData() or "tradingview"),
                exchange=str(self._exchange_combo.currentData() or ""),
                timeframe=self._timeframe_combo.currentText(),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "无法加入自选股", str(exc))
            return
        self._name_edit.clear()
        self._symbol_edit.clear()
        self.refresh()

    def _toggle_continuous_tracking(self) -> None:
        self._pool_tracking_enabled = not self._pool_tracking_enabled
        self._update_continuous_button()
        self.continuous_tracking_requested.emit(self._pool_tracking_enabled)

    def set_pool_tracking_enabled(self, enabled: bool) -> None:
        self._pool_tracking_enabled = enabled
        self._update_continuous_button()

    def _update_continuous_button(self) -> None:
        if self._pool_tracking_enabled:
            self._continuous_button.setText("停止持续跟踪分析")
            self._continuous_button.setStyleSheet(
                "background-color: #FF4757; color: white; font-weight: 600;"
            )
        else:
            self._continuous_button.setText("开始持续跟踪分析")
            self._continuous_button.setStyleSheet(
                "background-color: #00D084; color: white; font-weight: 600;"
            )

    def _selected_item_ids(self) -> list[str]:
        rows = {index.row() for index in self._table.selectionModel().selectedRows()}
        ids: list[str] = []
        for row in sorted(rows):
            item = self._table.item(row, 0)
            if item is not None:
                item_id = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(item_id, str):
                    ids.append(item_id)
        return ids

    def _on_cell_clicked(self, row: int, column: int) -> None:
        # Keep all other columns available for multi-selection. Clicking the
        # name or code is the explicit "open this stock" gesture.
        if column not in (0, 1):
            return
        item = self._table.item(row, 0)
        if item is None:
            return
        item_id = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(item_id, str):
            self.show_terminal_requested.emit(item_id)

    def refresh(
        self,
        status_for_item: Callable[[WatchlistItem], tuple[str, bool]] | None = None,
    ) -> None:
        selected_ids = set(self._selected_item_ids())
        items = self._store.analysis_pool_items() if self._showing_pool else self._store.items
        headers = (
            ("名称", "代码", "分析进度", "下单机会")
            if self._showing_pool
            else ("名称", "代码", "数据来源", "交易所", "周期", "分析池", "分析状态", "下单机会")
        )
        self._table.blockSignals(True)
        try:
            self._table.clear()
            self._table.setColumnCount(len(headers))
            self._table.setHorizontalHeaderLabels(headers)
            self._table.setRowCount(len(items))
            for row, watch_item in enumerate(items):
                status, has_opportunity = (
                    status_for_item(watch_item) if status_for_item is not None else ("等待分析", False)
                )
                values = [watch_item.display_name, watch_item.symbol]
                if self._showing_pool:
                    values.extend((status, "有" if has_opportunity else "无"))
                else:
                    values.extend(
                        (
                            self._data_source_label(watch_item.data_source),
                            _EXCHANGE_LABELS.get(watch_item.exchange, watch_item.exchange or "自动"),
                            watch_item.timeframe,
                            "已加入" if self._store.in_analysis_pool(watch_item.id) else "未加入",
                            status,
                            "有" if has_opportunity else "无",
                        )
                    )
                for column, value in enumerate(values):
                    cell = QTableWidgetItem(value)
                    if column == 0:
                        cell.setData(Qt.ItemDataRole.UserRole, watch_item.id)
                    if value == "有":
                        cell.setForeground(QBrush(QColor("#00D084")))
                    elif headers[column] == "分析池":
                        cell.setForeground(
                            QBrush(QColor("#E8ECF1" if value == "已加入" else "#646E7A"))
                        )
                    elif value in ("分析中", "准备分析", "阶段一分析中…", "阶段二分析中…"):
                        cell.setForeground(QBrush(QColor("#C0913C")))
                    self._table.setItem(row, column, cell)
                if watch_item.id in selected_ids:
                    self._table.selectRow(row)
        finally:
            self._table.blockSignals(False)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

    @staticmethod
    def _data_source_label(kind: str) -> str:
        from pa_agent.data.factory import data_source_label

        return data_source_label(kind)


class _PoolTableWidget(QTableWidget):
    """Table with cell-originated rubber-band row selection."""

    cell_activated = pyqtSignal(int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self.verticalHeader().setVisible(False)
        self._selection_origin: QPoint | None = None
        self._selection_additive = False
        self._selection_dragging = False
        self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self.viewport())
        self._rubber_band.setStyleSheet(
            "border: 1px dashed #4A7EBB; background-color: rgba(74,126,187, 35);"
        )
        self.setStyleSheet(
            "QTableWidget::indicator { width: 15px; height: 15px; }"
            "QTableWidget::indicator:unchecked { background: #12151A; border: 1px solid #22272F; border-radius: 3px; }"
            "QTableWidget::indicator:checked { background: #4A7EBB; border: 1px solid #4A7EBB; border-radius: 3px; }"
        )
        self.viewport().installEventFilter(self)

    def selected_item_ids(self) -> list[str]:
        item_ids: list[str] = []
        for row in sorted(self._checked_rows()):
            cell = self.item(row, 0)
            item_id = cell.data(Qt.ItemDataRole.UserRole) if cell is not None else None
            if isinstance(item_id, str):
                item_ids.append(item_id)
        return item_ids

    def eventFilter(self, watched, event):  # type: ignore[override]
        if watched is self.viewport():
            if event.type() == QEvent.Type.MouseButtonPress:
                return self._handle_viewport_press(event)
            if event.type() == QEvent.Type.MouseMove:
                return self._handle_viewport_move(event)
            if event.type() == QEvent.Type.MouseButtonRelease:
                return self._handle_viewport_release(event)
        return super().eventFilter(watched, event)

    def _handle_viewport_press(self, event) -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        if not self.indexAt(event.pos()).isValid():
            return False
        self.setFocus()
        self._selection_origin = event.pos()
        self._selection_additive = bool(
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
        )
        self._selection_dragging = False
        self._rubber_band.setGeometry(QRect(self._selection_origin, self._selection_origin))
        return True

    def _handle_viewport_move(self, event) -> bool:
        if self._selection_origin is None:
            return False
        if (
            not self._selection_dragging
            and (event.pos() - self._selection_origin).manhattanLength()
            >= QApplication.startDragDistance()
        ):
            self._selection_dragging = True
            self._rubber_band.show()
        if self._selection_dragging:
            self._rubber_band.setGeometry(
                QRect(self._selection_origin, event.pos()).normalized()
            )
        return True

    def _handle_viewport_release(self, event) -> bool:
        if self._selection_origin is None or event.button() != Qt.MouseButton.LeftButton:
            return False
        origin = self._selection_origin
        selection_rect = self._rubber_band.geometry()
        self._rubber_band.hide()
        self._selection_origin = None
        if self._selection_dragging:
            selected_rows = {
                row
                for row in range(self.rowCount())
                if self._visual_row_rect(row).intersects(selection_rect)
            }
            self._apply_rubber_selection(selected_rows, self._selection_additive)
        else:
            self._apply_click_selection(origin, self._selection_additive)
        self._selection_dragging = False
        return True

    def _visual_row_rect(self, row: int) -> QRect:
        rect: QRect | None = None
        for column in range(self.columnCount()):
            if self.isColumnHidden(column):
                continue
            cell_rect = self.visualRect(self.model().index(row, column))
            if not cell_rect.isValid():
                continue
            rect = cell_rect if rect is None else rect.united(cell_rect)
        return rect or QRect()
    def _apply_click_selection(self, point: QPoint, additive: bool) -> None:
        index = self.indexAt(point)
        if not index.isValid():
            if not additive:
                self._set_selected_rows(set())
            return
        row = index.row()
        column = index.column()
        if column == 0:
            selected_rows = self._checked_rows()
            if row in selected_rows:
                selected_rows.remove(row)
            else:
                selected_rows.add(row)
        elif additive:
            selected_rows = self._selected_rows()
            if row in selected_rows:
                selected_rows.remove(row)
            else:
                selected_rows.add(row)
        else:
            selected_rows = {row}
        self._set_selected_rows(selected_rows)
        if not additive:
            self.cell_activated.emit(row, column)

    def _apply_rubber_selection(self, hit_rows: set[int], additive: bool) -> None:
        if additive:
            selected_rows = self._selected_rows()
            if hit_rows and hit_rows.issubset(selected_rows):
                selected_rows.difference_update(hit_rows)
            else:
                selected_rows.update(hit_rows)
        else:
            selected_rows = hit_rows
        self._set_selected_rows(selected_rows)

    def _selected_rows(self) -> set[int]:
        return self._checked_rows()

    def _checked_rows(self) -> set[int]:
        rows: set[int] = set()
        for row in range(self.rowCount()):
            item = self.item(row, 0)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                rows.add(row)
        return rows

    def _set_selected_rows(self, rows: set[int]) -> None:
        for row in range(self.rowCount()):
            checked = row in rows
            checkbox = self.item(row, 0)
            if checkbox is not None:
                checkbox.setCheckState(
                    Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                )
        self.clearSelection()


class WatchlistDashboard(QWidget):
    """Side-by-side watchlist and analysis pool with checkbox and DnD controls."""

    show_terminal_requested = pyqtSignal(str)
    add_to_pool_requested = pyqtSignal(list)
    batch_analysis_requested = pyqtSignal(list)
    remove_from_pool_requested = pyqtSignal(list)
    continuous_tracking_requested = pyqtSignal(bool)
    watchlist_item_deleted = pyqtSignal(str)
    watchlist_item_updated = pyqtSignal(str)

    def __init__(
        self,
        store: WatchlistStore,
        *,
        pool_tracking_enabled: bool,
        settings: object | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._settings = settings
        self._pool_tracking_enabled = pool_tracking_enabled
        self._watchlist_name_column_width = 220
        self._pool_name_column_width = 217
        self._build_ui()
        self.set_pool_tracking_enabled(pool_tracking_enabled)
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        self._root_layout = root
        root.setContentsMargins(8, 40, 8, 8)
        root.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("watchlistPoolSplitter")
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_watchlist_panel())
        splitter.addWidget(self._build_pool_panel())
        splitter.setStretchFactor(0, 40)
        splitter.setStretchFactor(1, 60)
        self._watchlist_pool_splitter = splitter
        ratio = self._saved_watchlist_pool_split_ratio()
        if ratio is None:
            splitter.setSizes([400, 600])
        else:
            splitter.setSizes([int(ratio * 1000), int((1.0 - ratio) * 1000)])
        splitter.splitterMoved.connect(self._persist_watchlist_pool_split_ratio)
        root.addWidget(splitter, 1)

    def _saved_watchlist_pool_split_ratio(self) -> float | None:
        general = getattr(self._settings, "general", None)
        ratios = getattr(general, "split_hotzone_ratios", None)
        value = ratios.get("watchlist_pool") if isinstance(ratios, dict) else None
        try:
            ratio = float(value)
        except (TypeError, ValueError):
            return None
        return min(0.95, max(0.05, ratio))

    def _persist_watchlist_pool_split_ratio(self, _position: int, _index: int) -> None:
        sizes = self._watchlist_pool_splitter.sizes()
        total = sum(sizes)
        if total <= 0:
            return
        ratio = min(0.95, max(0.05, sizes[0] / total))
        general = getattr(self._settings, "general", None)
        if general is None:
            return
        ratios = getattr(general, "split_hotzone_ratios", None)
        if not isinstance(ratios, dict):
            ratios = {}
            general.split_hotzone_ratios = ratios
        ratios["watchlist_pool"] = ratio
        try:
            from pa_agent.config.paths import SETTINGS_JSON_PATH
            from pa_agent.config.settings import load_settings, save_settings

            persisted_settings = load_settings(SETTINGS_JSON_PATH)
            persisted_settings.general.split_hotzone_ratios["watchlist_pool"] = ratio
            save_settings(persisted_settings, SETTINGS_JSON_PATH)
        except Exception:
            logger.debug("Unable to persist watchlist/pool split ratio", exc_info=True)

    def set_tab_bar_reserved(self, reserved: bool) -> None:
        """Keep dashboard content clear of the fixed watchlist tab button."""
        top_margin = 8 if reserved else 40
        self._root_layout.setContentsMargins(8, top_margin, 8, 8)

    def _build_watchlist_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("watchlistPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.setSpacing(7)
        heading_row = QHBoxLayout()
        heading_row.setContentsMargins(0, 0, 0, 0)
        self._watchlist_heading = QLabel("自选股列表 (0)")
        self._watchlist_heading.setObjectName("toolbarTitle")
        heading_row.addWidget(self._watchlist_heading)
        heading_row.addStretch()
        self._watchlist_add_toggle = QPushButton("添加自选")
        self._watchlist_add_toggle.setObjectName("ghostButton")
        self._watchlist_add_toggle.clicked.connect(self._toggle_watchlist_add_form)
        heading_row.addWidget(self._watchlist_add_toggle)
        layout.addLayout(heading_row)

        self._watchlist_add_form = QWidget(panel)
        form = QHBoxLayout(self._watchlist_add_form)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("股票名称")
        self._name_edit.setMinimumWidth(70)
        self._symbol_edit = QLineEdit()
        self._symbol_edit.setPlaceholderText("股票代码")
        self._symbol_edit.setMinimumWidth(70)
        self._symbol_format_help_button = QToolButton()
        self._symbol_format_help_button.setObjectName("watchlistSymbolFormatHelp")
        self._symbol_format_help_button.setText("?")
        self._symbol_format_help_button.setFixedSize(20, 20)
        self._symbol_format_help_button.setToolTip(FUTU_SYMBOL_FORMAT_HELP)
        self._symbol_format_help_button.clicked.connect(self._show_symbol_format_help)
        self._data_source_combo = QComboBox()
        from pa_agent.data.factory import DATA_SOURCE_CHOICES

        for kind, label in DATA_SOURCE_CHOICES:
            self._data_source_combo.addItem(label, kind)
        self._data_source_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._data_source_combo.setMinimumContentsLength(7)
        self._data_source_combo.setCurrentIndex(
            max(self._data_source_combo.findData("tradingview"), 0)
        )
        self._exchange_combo = QComboBox()
        from pa_agent.data.tradingview import TV_EXCHANGE_PRESETS

        for exchange in TV_EXCHANGE_PRESETS:
            self._exchange_combo.addItem(_EXCHANGE_LABELS.get(exchange, exchange), exchange)
        self._exchange_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._exchange_combo.setMinimumContentsLength(8)
        self._timeframe_combo = QComboBox()
        self._timeframe_combo.addItems(_TIMEFRAMES)
        self._timeframe_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._timeframe_combo.setMinimumContentsLength(3)
        add_button = QPushButton("确认加入")
        add_button.setObjectName("primaryButton")
        add_button.clicked.connect(self._add_watchlist_item)
        for label, widget in (
            ("名称", self._name_edit),
            ("代码", self._symbol_edit),
            ("来源", self._data_source_combo),
            ("交易所", self._exchange_combo),
            ("周期", self._timeframe_combo),
        ):
            form.addWidget(QLabel(f"{label}:"))
            form.addWidget(widget)
            if widget is self._symbol_edit:
                form.addWidget(self._symbol_format_help_button)
        form.addWidget(add_button)
        self._watchlist_add_form.hide()
        layout.addWidget(self._watchlist_add_form)

        self._watchlist_table = _PoolTableWidget()
        self._watchlist_table.horizontalHeader().sectionResized.connect(
            self._remember_watchlist_name_column_width
        )
        self._watchlist_table.cell_activated.connect(self._on_watchlist_cell_clicked)
        self._watchlist_table.itemChanged.connect(self._update_select_all_state)
        self._watchlist_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._watchlist_table.customContextMenuRequested.connect(
            self._show_watchlist_context_menu
        )
        layout.addWidget(self._watchlist_table, 1)

        actions = QHBoxLayout()
        self._watchlist_select_all = QCheckBox("全选")
        self._watchlist_select_all.setTristate(False)
        self._watchlist_select_all.setStyleSheet(self._selection_checkbox_style())
        self._watchlist_select_all.toggled.connect(self._set_watchlist_all_checked)
        self._add_pool_button = QPushButton("加入分析池")
        self._add_pool_button.clicked.connect(
            lambda: self.add_to_pool_requested.emit(self._checked_or_selected_ids(self._watchlist_table))
        )
        self._batch_analysis_button = QPushButton("单次分析")
        self._batch_analysis_button.setObjectName("primaryButton")
        self._batch_analysis_button.clicked.connect(
            lambda: self.batch_analysis_requested.emit(self._checked_or_selected_ids(self._watchlist_table))
        )
        actions.addWidget(self._watchlist_select_all)
        actions.addWidget(self._add_pool_button)
        actions.addWidget(self._batch_analysis_button)
        actions.addStretch()
        hint = QLabel("按住 CTRL 键复选\n顶部标签可滚轮滚动浏览")
        hint.setObjectName("mutedLabel")
        hint.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        hint.setStyleSheet("font-size: 11px;")
        actions.addWidget(hint)
        layout.addLayout(actions)
        return panel

    def _toggle_watchlist_add_form(self) -> None:
        visible = not self._watchlist_add_form.isVisible()
        self._watchlist_add_form.setVisible(visible)
        self._watchlist_add_toggle.setText("收起" if visible else "添加自选")
        if visible:
            self._name_edit.setFocus()

    def _show_symbol_format_help(self) -> None:
        button = self._symbol_format_help_button
        QToolTip.showText(
            button.mapToGlobal(button.rect().bottomLeft()),
            FUTU_SYMBOL_FORMAT_HELP,
            button,
        )

    def _remember_watchlist_name_column_width(
        self,
        logical_index: int,
        _old_size: int,
        new_size: int,
    ) -> None:
        if logical_index == 1 and new_size > 0:
            self._watchlist_name_column_width = new_size

    def _build_pool_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("analysisPoolPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 0, 0, 0)
        layout.setSpacing(7)
        heading_row = QHBoxLayout()
        heading_row.setContentsMargins(0, 0, 0, 0)
        self._pool_heading = QLabel("分析池 (0)")
        self._pool_heading.setObjectName("toolbarTitle")
        heading_row.addWidget(self._pool_heading)
        heading_row.addStretch()
        layout.addLayout(heading_row)

        self._pool_table = _PoolTableWidget()
        self._pool_table.cell_activated.connect(self._on_pool_cell_clicked)
        self._pool_table.itemChanged.connect(self._update_pool_select_all_state)
        layout.addWidget(self._pool_table, 1)

        actions = QHBoxLayout()
        self._pool_select_all = QCheckBox("全选")
        self._pool_select_all.setTristate(False)
        self._pool_select_all.setStyleSheet(self._selection_checkbox_style())
        self._pool_select_all.toggled.connect(self._set_pool_all_checked)
        remove_button = QPushButton("移出分析池")
        remove_button.clicked.connect(
            lambda: self.remove_from_pool_requested.emit(self._checked_or_selected_ids(self._pool_table))
        )
        from pa_agent.gui.widgets.shimmer_button import ShimmerButton

        self._continuous_button = ShimmerButton()
        self._continuous_button.clicked.connect(self._toggle_continuous_tracking)
        actions.addWidget(self._pool_select_all)
        actions.addWidget(remove_button)
        actions.addWidget(self._continuous_button)
        actions.addStretch()
        layout.addLayout(actions)
        return panel

    def _add_watchlist_item(self) -> None:
        try:
            self._store.add(
                name=self._name_edit.text(),
                symbol=self._symbol_edit.text(),
                data_source=str(self._data_source_combo.currentData() or "tradingview"),
                exchange=str(self._exchange_combo.currentData() or ""),
                timeframe=self._timeframe_combo.currentText(),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "无法加入自选股", str(exc))
            return
        self._name_edit.clear()
        self._symbol_edit.clear()
        self._watchlist_add_form.hide()
        self._watchlist_add_toggle.setText("添加自选")
        self.refresh()

    def _on_watchlist_cell_clicked(self, row: int, column: int) -> None:
        if column not in (1, 3):
            return
        self._request_terminal_for_row(self._watchlist_table, row)

    def _show_watchlist_context_menu(self, position: QPoint) -> None:
        """Show stock editing actions when the name cell is right-clicked."""
        index = self._watchlist_table.indexAt(position)
        if not index.isValid() or index.column() != 1:
            return
        checkbox = self._watchlist_table.item(index.row(), 0)
        item_id = checkbox.data(Qt.ItemDataRole.UserRole) if checkbox is not None else None
        if not isinstance(item_id, str):
            return
        item = self._store.get(item_id)
        if item is None:
            return

        menu = QMenu(self)
        edit_name = menu.addAction("编辑股票名称...")
        edit_name.triggered.connect(lambda: self._edit_stock_name(item_id))

        source_menu = menu.addMenu("编辑数据来源")
        from pa_agent.data.factory import DATA_SOURCE_CHOICES

        for kind, label in DATA_SOURCE_CHOICES:
            action = source_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(kind == item.data_source)
            action.triggered.connect(
                lambda _checked=False, source_kind=kind: self._update_watchlist_item(
                    item_id, data_source=source_kind
                )
            )

        exchange_menu = menu.addMenu("编辑交易所")
        from pa_agent.data.tradingview import TV_EXCHANGE_PRESETS

        for exchange in TV_EXCHANGE_PRESETS:
            action = exchange_menu.addAction(_EXCHANGE_LABELS.get(exchange, exchange))
            action.setCheckable(True)
            action.setChecked(exchange == item.exchange)
            action.triggered.connect(
                lambda _checked=False, value=exchange: self._update_watchlist_item(
                    item_id, exchange=value
                )
            )

        timeframe_menu = menu.addMenu("编辑周期")
        for timeframe in _TIMEFRAMES:
            action = timeframe_menu.addAction(timeframe)
            action.setCheckable(True)
            action.setChecked(timeframe == item.timeframe)
            action.triggered.connect(
                lambda _checked=False, value=timeframe: self._update_watchlist_item(
                    item_id, timeframe=value
                )
            )

        menu.addSeparator()
        delete_action = menu.addAction("删除")
        delete_action.triggered.connect(lambda: self._delete_watchlist_item(item_id))

        menu.exec(self._watchlist_table.viewport().mapToGlobal(position))

    def _edit_stock_name(self, item_id: str) -> None:
        item = self._store.get(item_id)
        if item is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("编辑股票信息")
        form = QFormLayout(dialog)
        name_edit = QLineEdit(item.name)
        symbol_edit = QLineEdit(item.symbol)
        symbol_edit.setEnabled(False)
        symbol_edit.setStyleSheet("color: #9AA5B1;")
        form.addRow("股票名称", name_edit)
        form.addRow("股票代码", symbol_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._update_watchlist_item(item_id, name=name_edit.text())

    def _update_watchlist_item(self, item_id: str, **changes: str) -> None:
        if self._store.update(item_id, **changes) is not None:
            if {"data_source", "exchange", "timeframe"}.intersection(changes):
                self.watchlist_item_updated.emit(item_id)
            self.refresh()

    def _delete_watchlist_item(self, item_id: str) -> None:
        item = self._store.get(item_id)
        if item is None:
            return
        answer = QMessageBox.question(
            self,
            "删除自选股",
            f"确定删除“{item.display_name}（{item.symbol}）”？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self._store.remove(item_id) is not None:
            self.watchlist_item_deleted.emit(item_id)
            self.refresh()

    def _on_pool_cell_clicked(self, row: int, column: int) -> None:
        if column not in (1, 2):
            return
        self._request_terminal_for_row(self._pool_table, row)

    def _request_terminal_for_row(self, table: _PoolTableWidget, row: int) -> None:
        item = table.item(row, 0)
        item_id = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if isinstance(item_id, str):
            self.show_terminal_requested.emit(item_id)

    @staticmethod
    def _checked_or_selected_ids(table: _PoolTableWidget) -> list[str]:
        checked_ids: list[str] = []
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item is None or item.checkState() != Qt.CheckState.Checked:
                continue
            item_id = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(item_id, str):
                checked_ids.append(item_id)
        return checked_ids or table.selected_item_ids()

    def _set_watchlist_all_checked(self, checked: bool) -> None:
        self._set_table_all_checked(self._watchlist_table, checked)

    def _set_pool_all_checked(self, checked: bool) -> None:
        self._set_table_all_checked(self._pool_table, checked)

    @staticmethod
    def _set_table_all_checked(table: _PoolTableWidget, checked: bool) -> None:
        table.blockSignals(True)
        try:
            table._set_selected_rows(set(range(table.rowCount())) if checked else set())
        finally:
            table.blockSignals(False)

    @staticmethod
    def _selection_checkbox_style() -> str:
        """Keep select-all controls visually consistent with row checkboxes."""
        return (
            "QCheckBox::indicator { width: 15px; height: 15px; }"
            "QCheckBox::indicator:unchecked { background: #12151A; border: 1px solid #22272F; border-radius: 3px; }"
            "QCheckBox::indicator:checked { background: #4A7EBB; border: 1px solid #4A7EBB; border-radius: 3px; }"
        )

    def _update_select_all_state(self, _item: QTableWidgetItem) -> None:
        self._update_table_select_all_state(self._watchlist_table, self._watchlist_select_all)

    def _update_pool_select_all_state(self, _item: QTableWidgetItem) -> None:
        self._update_table_select_all_state(self._pool_table, self._pool_select_all)

    @staticmethod
    def _update_table_select_all_state(
        table: _PoolTableWidget,
        control: QCheckBox,
    ) -> None:
        if table.signalsBlocked():
            return
        total = table.rowCount()
        checked = sum(
            1
            for row in range(total)
            if table.item(row, 0) is not None
            and table.item(row, 0).checkState() == Qt.CheckState.Checked
        )
        control.blockSignals(True)
        control.setChecked(bool(total and checked == total))
        control.blockSignals(False)

    def _toggle_continuous_tracking(self) -> None:
        self._pool_tracking_enabled = self._continuous_button.isChecked()
        self._update_continuous_button()
        self.continuous_tracking_requested.emit(self._pool_tracking_enabled)

    def set_pool_tracking_enabled(self, enabled: bool) -> None:
        self._pool_tracking_enabled = enabled
        self._update_continuous_button()

    def _update_continuous_button(self) -> None:
        self._continuous_button.setChecked(self._pool_tracking_enabled)
        self._continuous_button.set_tracking_labels(
            active="持续分析",
            inactive="持续分析",
        )

    def refresh(
        self,
        status_for_item: Callable[[WatchlistItem], tuple[str, bool]] | None = None,
        quote_for_item: Callable[[WatchlistItem], tuple[float | None, float | None]] | None = None,
        tracking_for_item: Callable[[WatchlistItem], bool] | None = None,
    ) -> None:
        self._populate_table(
            self._watchlist_table,
            self._store.items,
            ("选择", "名称", "分析池", "代码", "实时价格", "涨跌幅", "下单机会"),
            status_for_item,
            quote_for_item,
            pool_only=False,
            tracking_for_item=tracking_for_item,
        )
        self._populate_table(
            self._pool_table,
            self._store.analysis_pool_items(),
            ("选择", "名称", "代码", "周期", "分析状态", "下单机会", "持续分析"),
            status_for_item,
            quote_for_item,
            pool_only=True,
            tracking_for_item=tracking_for_item,
        )
        self._watchlist_heading.setText(f"自选股列表 ({len(self._store.items)})")
        self._pool_heading.setText(f"分析池 ({len(self._store.analysis_pool_items())})")

    def _populate_table(
        self,
        table: _PoolTableWidget,
        items: tuple[WatchlistItem, ...],
        headers: tuple[str, ...],
        status_for_item: Callable[[WatchlistItem], tuple[str, bool]] | None,
        quote_for_item: Callable[[WatchlistItem], tuple[float | None, float | None]] | None,
        *,
        pool_only: bool,
        tracking_for_item: Callable[[WatchlistItem], bool] | None = None,
    ) -> None:
        header = table.horizontalHeader()
        restore_watchlist_name_width = table is self._watchlist_table
        if restore_watchlist_name_width:
            header.blockSignals(True)
        checked_ids = {
            item.data(Qt.ItemDataRole.UserRole)
            for row in range(table.rowCount())
            if (item := table.item(row, 0)) is not None
            and item.checkState() == Qt.CheckState.Checked
        }
        table.blockSignals(True)
        try:
            table.clear()
            table.setColumnCount(len(headers))
            table.setHorizontalHeaderLabels(headers)
            table.setRowCount(len(items))
            for row, watch_item in enumerate(items):
                status, has_opportunity = (
                    status_for_item(watch_item) if status_for_item else ("等待分析", False)
                )
                price, change_rate = (
                    quote_for_item(watch_item) if quote_for_item else (None, None)
                )
                tracking_enabled = (
                    tracking_for_item(watch_item)
                    if tracking_for_item is not None
                    else self._pool_tracking_enabled
                )
                values = (
                    [
                        watch_item.display_name,
                        watch_item.symbol,
                        watch_item.timeframe,
                        status,
                        "有" if has_opportunity else "无",
                        "开启" if tracking_enabled else "暂停",
                    ]
                    if pool_only
                    else [
                        watch_item.display_name,
                        "已加入" if self._store.in_analysis_pool(watch_item.id) else "未加入",
                        watch_item.symbol,
                        self._format_quote_price(price),
                        self._format_quote_change(change_rate),
                        "有" if has_opportunity else "无",
                    ]
                )
                checkbox = QTableWidgetItem()
                checkbox.setData(Qt.ItemDataRole.UserRole, watch_item.id)
                checkbox.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsUserCheckable
                )
                checkbox.setCheckState(
                    Qt.CheckState.Checked if watch_item.id in checked_ids else Qt.CheckState.Unchecked
                )
                table.setItem(row, 0, checkbox)
                for column, value in enumerate(values, start=1):
                    cell = QTableWidgetItem(value)
                    header_text = headers[column]
                    if header_text == "实时价格":
                        cell.setForeground(QBrush(QColor("#E8ECF1")))
                    elif header_text == "涨跌幅" and change_rate is not None:
                        if change_rate > 0:
                            cell.setForeground(QBrush(QColor("#FF5353")))
                        elif change_rate < 0:
                            cell.setForeground(QBrush(QColor("#28C086")))
                    elif value == "有":
                        cell.setForeground(QBrush(QColor("#00D084")))
                    elif header_text == "分析池":
                        is_in_pool = value == "已加入"
                        cell.setForeground(QBrush(QColor("#E8ECF1" if is_in_pool else "#646E7A")))
                    elif header_text == "持续分析":
                        cell.setForeground(QBrush(QColor("#00D084" if value == "开启" else "#9AA5B1")))
                    elif "分析" in value or "准备" in value or "获取K线" in value:
                        cell.setForeground(QBrush(QColor("#C0913C")))
                    table.setItem(row, column, cell)
        finally:
            table.blockSignals(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(0, 42)
        if table is self._watchlist_table:
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
            table.setColumnWidth(1, self._watchlist_name_column_width)
        else:
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
            table.setColumnWidth(1, self._pool_name_column_width)
        for column in range(2, len(headers) - 1):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        # Keep the final column joined to the table's right border.  This
        # removes the visually detached blank strip that appears when a table
        # has only a few narrow, content-sized columns.
        header.setSectionResizeMode(len(headers) - 1, QHeaderView.ResizeMode.Stretch)
        if table is self._watchlist_table:
            header.blockSignals(False)
            self._update_select_all_state(QTableWidgetItem())
        elif table is self._pool_table:
            self._update_pool_select_all_state(QTableWidgetItem())

    @staticmethod
    def _format_quote_price(value: float | None) -> str:
        if value is None:
            return "--"
        return f"{value:.8f}".rstrip("0").rstrip(".")

    @staticmethod
    def _format_quote_change(value: float | None) -> str:
        if value is None:
            return "--"
        return f"{value:+.2f}%"

    @staticmethod
    def _data_source_label(kind: str) -> str:
        from pa_agent.data.factory import data_source_label

        return data_source_label(kind)


class WorkspaceWindow(QMainWindow):
    """Top-level workspace that keeps analysis terminals in front-end tabs."""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("PA Agent — 自选股与分析池")
        self.setMinimumSize(960, 620)
        from PyQt6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        self.resize(
            min(1440, int(available.width() * 0.90)) if available else 1280,
            min(900, int(available.height() * 0.85)) if available else 780,
        )
        self._ctx = ctx
        self._store = WatchlistStore()
        self._terminals: dict[str, QWidget] = {}
        self._quote_cache: dict[str, tuple[float | None, float | None]] = {}
        self._pool_tracking_item_ids: set[str] = set()
        self._pool_tracking_syncing = False
        settings = getattr(self._ctx, "settings", None)
        self._pool_tracking_enabled = bool(
            getattr(
                getattr(settings, "general", None),
                "analysis_pool_tracking_on_start",
                True,
            )
        )
        if self._pool_tracking_enabled:
            self._pool_tracking_item_ids.update(self._store.analysis_pool_ids)
        self._build_ui()
        self._build_menu()
        self._quote_worker = _WatchlistQuoteWorker(lambda: self._store.items)
        self._quote_worker.quote_ready.connect(self._on_watchlist_quote_ready)
        self._quote_worker.start()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(500)
        self._refresh_timer.timeout.connect(self._refresh_dashboard)
        self._refresh_timer.start()
        if self._pool_tracking_enabled:
            QTimer.singleShot(0, lambda: self._set_pool_tracking(True, announce=False))

    def _build_ui(self) -> None:
        self._tabs = QTabWidget()
        self._tabs.setObjectName("workspaceTabs")
        self._tabs.setTabBar(_TerminalTabBar())
        self._tabs.setMovable(True)
        self._tabs.setTabsClosable(True)
        self._tabs.tabBar().setUsesScrollButtons(True)
        self._tabs.tabBar().tabMoved.connect(self._keep_watchlist_tab_fixed)
        self._tabs.tabCloseRequested.connect(self._close_tab)
        self.setCentralWidget(self._tabs)

        self._dashboard = WatchlistDashboard(
            self._store,
            pool_tracking_enabled=self._pool_tracking_enabled,
            settings=getattr(self._ctx, "settings", None),
        )
        self._dashboard.show_terminal_requested.connect(self._show_terminal)
        self._dashboard.add_to_pool_requested.connect(self._add_to_pool)
        self._dashboard.batch_analysis_requested.connect(self._batch_analyze)
        self._dashboard.remove_from_pool_requested.connect(self._remove_from_pool)
        self._dashboard.continuous_tracking_requested.connect(self._set_pool_tracking)
        self._dashboard.watchlist_item_deleted.connect(self._close_deleted_watchlist_terminal)
        self._dashboard.watchlist_item_updated.connect(self._close_deleted_watchlist_terminal)
        self._tabs.addTab(self._dashboard, "自选股")
        tab_bar = self._tabs.tabBar()
        tab_bar.setTabVisible(0, False)
        self._update_tab_widget_layout()
        self._watchlist_tab_button = QToolButton(self._tabs)
        self._watchlist_tab_button.setObjectName("watchlistTabButton")
        self._watchlist_tab_button.setText("自选股")
        self._watchlist_tab_button.setCheckable(True)
        self._watchlist_tab_button.setChecked(True)
        self._watchlist_tab_button.setFixedSize(96, 28)
        self._watchlist_tab_button.setStyleSheet(
            "QToolButton { border: 1px solid #22272F; background: #22272F; color: #E8ECF1; }"
            "QToolButton:checked { background: #4A7EBB; border-color: #4A7EBB; color: white; }"
        )
        self._watchlist_tab_button.clicked.connect(
            lambda: self._tabs.setCurrentWidget(self._dashboard)
        )
        self._tabs.currentChanged.connect(self._sync_watchlist_tab_button)
        self._position_watchlist_tab_button()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if hasattr(self, "_watchlist_tab_button"):
            self._position_watchlist_tab_button()

    def _position_watchlist_tab_button(self) -> None:
        self._watchlist_tab_button.move(4, 2)
        self._watchlist_tab_button.raise_()

    def _update_tab_widget_layout(self) -> None:
        """Keep dashboard content clear when the terminal tab strip is absent."""
        self._tabs.setStyleSheet("QTabWidget::tab-bar { left: 104px; }")
        self._dashboard.set_tab_bar_reserved(self._tabs.count() > 1)

    def _sync_watchlist_tab_button(self, index: int) -> None:
        self._watchlist_tab_button.setChecked(self._tabs.widget(index) is self._dashboard)

    def _keep_watchlist_tab_fixed(self, from_index: int, to_index: int) -> None:
        """Reject a drag that would move the hidden dashboard back into the tab strip."""
        if not hasattr(self, "_restoring_watchlist_tab"):
            self._restoring_watchlist_tab = False
        if self._restoring_watchlist_tab or (from_index != 0 and to_index != 0):
            return
        self._restoring_watchlist_tab = True
        try:
            self._tabs.tabBar().moveTab(to_index, from_index)
        finally:
            self._restoring_watchlist_tab = False

    def _build_menu(self) -> None:
        menu = self.menuBar()
        ai_action = QAction("AI 模型设置", self)
        ai_action.triggered.connect(self._open_ai_model_settings)
        menu.addAction(ai_action)
        feishu_action = QAction("飞书发送通知设置", self)
        feishu_action.triggered.connect(self._open_feishu_settings)
        menu.addAction(feishu_action)
        general_action = QAction("其他通用设置", self)
        general_action.triggered.connect(self._open_general_settings)
        menu.addAction(general_action)

    def _show_terminal(self, item_id: str) -> None:
        terminal = self._ensure_terminal(item_id)
        if terminal is None:
            return
        # Suppress painting while QTabWidget assigns the final page geometry;
        # otherwise Windows may expose the terminal at its tiny construction
        # size for one frame, including an unnecessary vertical scrollbar.
        terminal.setUpdatesEnabled(False)
        try:
            self._tabs.setCurrentWidget(terminal)
            terminal.ensurePolished()
            tabs_layout = self._tabs.layout()
            if tabs_layout is not None:
                tabs_layout.activate()
            terminal_layout = terminal.layout()
            if terminal_layout is not None:
                terminal_layout.activate()
            QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
        finally:
            terminal.setUpdatesEnabled(True)
            terminal.update()

    def _add_to_pool(self, item_ids: list[str]) -> None:
        if not item_ids:
            return
        added_ids = self._store.add_to_analysis_pool(item_ids)
        if self._pool_tracking_enabled:
            self._pool_tracking_syncing = True
            try:
                # A terminal added after the pool switch was enabled must
                # inherit the current tracking state like existing members.
                for item_id in added_ids:
                    terminal = self._ensure_terminal(item_id)
                    if terminal is not None:
                        terminal.set_workspace_continuous_tracking(True)  # type: ignore[attr-defined]
                        self._pool_tracking_item_ids.add(item_id)
            finally:
                self._pool_tracking_syncing = False
        self._refresh_dashboard()

    def _batch_analyze(self, item_ids: list[str]) -> None:
        if not item_ids:
            return
        started = 0
        for item_id in item_ids:
            terminal = self._ensure_terminal(item_id)
            if terminal is None:
                continue
            if terminal.start_workspace_analysis():  # type: ignore[attr-defined]
                started += 1
        self.statusBar().showMessage(f"已提交 {started} 只股票的分析任务")
        self._refresh_dashboard()

    def _remove_from_pool(self, item_ids: list[str]) -> None:
        if not item_ids:
            return
        removed_ids = self._store.remove_from_analysis_pool(item_ids)
        self._pool_tracking_syncing = True
        try:
            for item_id in removed_ids:
                self._pool_tracking_item_ids.discard(item_id)
                terminal = self._terminals.get(item_id)
                if terminal is not None:
                    terminal.set_workspace_continuous_tracking(False)  # type: ignore[attr-defined]
        finally:
            self._pool_tracking_syncing = False
        self._sync_pool_tracking_switch()
        self._refresh_dashboard()

    def _close_deleted_watchlist_terminal(self, item_id: str) -> None:
        terminal = self._terminals.pop(item_id, None)
        self._pool_tracking_item_ids.discard(item_id)
        if terminal is not None:
            self._pool_tracking_syncing = True
            try:
                terminal.set_workspace_continuous_tracking(False)  # type: ignore[attr-defined]
            finally:
                self._pool_tracking_syncing = False
            index = self._tabs.indexOf(terminal)
            if index >= 0:
                self._tabs.removeTab(index)
                self._update_tab_widget_layout()
            terminal.close()
            terminal.deleteLater()
        self._sync_pool_tracking_switch()
        self._refresh_dashboard()

    def _set_pool_tracking(self, enabled: bool, *, announce: bool = True) -> None:
        enabled = bool(enabled)
        self._pool_tracking_enabled = enabled
        pool_ids = set(self._store.analysis_pool_ids)
        self._pool_tracking_item_ids = pool_ids if enabled else set()
        self._pool_tracking_syncing = True
        try:
            for item in self._store.analysis_pool_items():
                terminal = self._ensure_terminal(item.id)
                if terminal is not None:
                    terminal.set_workspace_continuous_tracking(enabled)  # type: ignore[attr-defined]
        finally:
            self._pool_tracking_syncing = False
        self._sync_pool_tracking_switch()
        if announce:
            self.statusBar().showMessage(
                "分析池持续分析已开启" if enabled else "分析池持续分析已停止"
            )
        self._refresh_dashboard()

    def _sync_pool_tracking_switch(self) -> None:
        """Reflect whether any current analysis-pool stock is still active."""
        pool_ids = self._store.analysis_pool_ids
        self._pool_tracking_item_ids.intersection_update(pool_ids)
        self._pool_tracking_enabled = bool(self._pool_tracking_item_ids)
        self._dashboard.set_pool_tracking_enabled(self._pool_tracking_enabled)

    def _tracking_for_item(self, item: WatchlistItem) -> bool:
        return item.id in self._pool_tracking_item_ids

    def _on_terminal_tracking_changed(self, item_id: str, enabled: bool) -> None:
        """Keep per-stock terminal state and the analysis pool in sync."""
        if self._pool_tracking_syncing:
            return
        item = self._store.get(item_id)
        if item is None:
            return
        if enabled:
            self._store.add_to_analysis_pool([item_id])
            self._pool_tracking_item_ids.add(item_id)
        else:
            self._pool_tracking_item_ids.discard(item_id)
        self._sync_pool_tracking_switch()
        self._refresh_dashboard()

    def _ensure_terminal(self, item_id: str):
        existing = self._terminals.get(item_id)
        if existing is not None:
            return existing
        item = self._store.get(item_id)
        if item is None:
            return None
        try:
            terminal = self._create_terminal(item)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unable to create terminal for %s", item.symbol)
            QMessageBox.warning(self, "无法创建分析窗口", f"{item.symbol}: {exc}")
            return None
        self._terminals[item_id] = terminal
        terminal.setUpdatesEnabled(False)
        index_before_add = self._tabs.currentIndex()
        try:
            self._tabs.addTab(terminal, self._terminal_tab_label(item))
            self._update_tab_widget_layout()
            self._tabs.setCurrentIndex(index_before_add)
        finally:
            terminal.setUpdatesEnabled(True)
        terminal.order_opportunity_detected.connect(  # type: ignore[attr-defined]
            lambda _decision, selected_id=item_id: self._show_terminal(selected_id)
        )
        terminal.workspace_continuous_tracking_changed.connect(  # type: ignore[attr-defined]
            lambda enabled, selected_id=item_id: self._on_terminal_tracking_changed(
                selected_id, bool(enabled)
            )
        )
        terminal.analysis_state_changed.connect(lambda _active: self._refresh_dashboard())  # type: ignore[attr-defined]
        terminal.order_opportunity_changed.connect(lambda _has: self._refresh_dashboard())  # type: ignore[attr-defined]
        return terminal

    def _create_terminal(self, item: WatchlistItem):
        """Create an isolated context so each pool member owns its data feed."""
        from pa_agent.ai.client_factory import create_ai_client
        from pa_agent.ai.json_validator import JsonValidator
        from pa_agent.ai.prompt_assembler import PromptAssembler
        from pa_agent.ai.router import route_strategy_files
        from pa_agent.ai.session_ledger import SessionTokenLedger
        from pa_agent.config.paths import EXPERIENCE_DIR, PROMPT_DIR, RECORDS_PENDING_DIR
        from pa_agent.config.settings import Settings
        from pa_agent.data.factory import create_data_source, normalize_data_source_kind
        from pa_agent.gui.main_window import MainWindow
        from pa_agent.records.experience_reader import ExperienceReader
        from pa_agent.records.pending_writer import PendingWriter
        from pa_agent.util.event_bus import EventBus

        base_settings = self._ctx.settings
        settings = (
            base_settings.model_copy(deep=True)
            if base_settings is not None
            else Settings()
        )
        kind = normalize_data_source_kind(item.data_source)
        settings.general.last_data_source = kind
        settings.general.last_symbol = item.symbol
        settings.general.last_timeframe = item.timeframe
        settings.general.last_tradingview_exchange = item.exchange
        settings.general.keep_analysis = False

        data_source = create_data_source(kind)
        try:
            data_source.connect()
            setter = getattr(data_source, "set_exchange", None)
            if callable(setter):
                setter(item.exchange)
            data_source.subscribe(item.symbol, item.timeframe)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Initial subscription failed for %s: %s", item.symbol, exc)

        event_bus = EventBus()
        exp_reader = ExperienceReader(experience_dir=EXPERIENCE_DIR, logger=self._ctx.logger)
        terminal_ctx = AppContext(
            settings=settings,
            logger=self._ctx.logger,
            event_bus=event_bus,
            data_source=data_source,
            client=create_ai_client(settings.provider, logger_=self._ctx.logger),
            assembler=PromptAssembler(
                prompt_dir=PROMPT_DIR,
                experience_reader=exp_reader,
                prompt_settings=settings.prompt,
            ),
            router=route_strategy_files,
            validator=JsonValidator(settings),
            pending_writer=PendingWriter(
                pending_dir=RECORDS_PENDING_DIR,
                event_bus=event_bus,
                api_key=settings.provider.api_key,
            ),
            exp_reader=exp_reader,
            ledger=SessionTokenLedger(
                context_window=settings.provider.context_window,
                warn_pct=settings.general.context_warning_threshold_pct,
            ),
        )
        return MainWindow(
            terminal_ctx,
            parent=self._tabs,
            embedded=True,
            target_name=self._analysis_target_label(item),
        )

    @staticmethod
    def _terminal_tab_label(item: WatchlistItem) -> str:
        return WorkspaceWindow._analysis_target_label(item)

    @staticmethod
    def _analysis_target_label(item: WatchlistItem) -> str:
        """Use one stable target label across analysis tabs and workbenches."""
        symbol = item.symbol.strip().upper().replace(":", ".")
        exchange = item.exchange.strip().upper()
        prefix_aliases = {
            "SSE": "SH",
            "SHSE": "SH",
            "SH": "SH",
            "SZSE": "SZ",
            "XSHE": "SZ",
            "SZ": "SZ",
            "NASDAQ": "US",
            "NYSE": "US",
            "AMEX": "US",
            "US": "US",
            "HKEX": "HK",
            "HK": "HK",
            "BJSE": "BJ",
            "BJ": "BJ",
        }
        suffix_prefixes = {"SH", "SZ", "US", "HK", "BJ"}
        if "." in symbol:
            first, second = symbol.split(".", 1)
            if second in suffix_prefixes:
                market_symbol = f"{second}.{first}"
            else:
                market_symbol = f"{prefix_aliases.get(first, first)}.{second}"
        elif exchange:
            market_symbol = f"{prefix_aliases.get(exchange, exchange)}.{symbol}"
        else:
            market_symbol = symbol
        return f"{item.display_name}（{market_symbol}）"

    def _status_for_item(self, item: WatchlistItem) -> tuple[str, bool]:
        terminal = self._terminals.get(item.id)
        if terminal is None:
            return "等待分析", False
        if terminal.workspace_analysis_in_progress():  # type: ignore[attr-defined]
            progress = terminal.workspace_progress_text()  # type: ignore[attr-defined]
            return progress or "分析中", terminal.workspace_order_opportunity()  # type: ignore[attr-defined]
        return terminal.workspace_progress_text(), terminal.workspace_order_opportunity()  # type: ignore[attr-defined]

    def _quote_for_item(self, item: WatchlistItem) -> tuple[float | None, float | None]:
        return self._quote_cache.get(item.id, (None, None))

    def _on_watchlist_quote_ready(
        self,
        item_id: str,
        price: object,
        change_rate: object,
    ) -> None:
        self._quote_cache[item_id] = (
            float(price) if price is not None else None,
            float(change_rate) if change_rate is not None else None,
        )

    def _refresh_dashboard(self) -> None:
        self._dashboard.refresh(
            self._status_for_item,
            self._quote_for_item,
            self._tracking_for_item,
        )

    def _close_tab(self, index: int) -> None:
        if index <= 0:
            return
        terminal = self._tabs.widget(index)
        if terminal is None:
            return
        item_id = next(
            (key for key, value in self._terminals.items() if value is terminal), None
        )
        if item_id is not None:
            self._pool_tracking_item_ids.discard(item_id)
            self._pool_tracking_syncing = True
            try:
                terminal.set_workspace_continuous_tracking(False)  # type: ignore[attr-defined]
            finally:
                self._pool_tracking_syncing = False
            self._sync_pool_tracking_switch()
        self._tabs.removeTab(index)
        self._update_tab_widget_layout()
        if item_id is not None:
            self._terminals.pop(item_id, None)
        terminal.close()
        terminal.deleteLater()
        self._refresh_dashboard()

    def _open_ai_model_settings(self) -> None:
        from pa_agent.gui.ai_model_settings_dialog import AIModelSettingsDialog

        dialog = AIModelSettingsDialog(self._ctx.settings, parent=self)
        if dialog.exec():
            terminal = self._tabs.currentWidget()
            if terminal is not None and all(
                callable(getattr(terminal, name, None))
                for name in ("sync_ai_model_settings", "resubmit_after_ai_settings_saved")
            ):
                terminal.sync_ai_model_settings(self._ctx.settings)
                terminal.resubmit_after_ai_settings_saved()
                self.statusBar().showMessage("AI 模型设置已保存，当前分析窗口已重新提交分析")
            else:
                self.statusBar().showMessage("AI 模型设置已保存；当前未选择分析窗口")

    def _open_feishu_settings(self) -> None:
        from pa_agent.gui.feishu_settings_dialog import FeishuSettingsDialog

        FeishuSettingsDialog(settings=self._ctx.settings, parent=self).exec()
        for terminal in self._terminals.values():
            terminal.sync_workspace_notification_settings(self._ctx.settings)  # type: ignore[attr-defined]

    def _open_general_settings(self) -> None:
        from pa_agent.gui.general_settings_dialog import GeneralSettingsDialog

        dialog = GeneralSettingsDialog(self._ctx.settings, parent=self)
        if dialog.exec():
            for terminal in self._terminals.values():
                terminal.sync_workspace_notification_settings(self._ctx.settings)  # type: ignore[attr-defined]
            self.statusBar().showMessage("通用设置已保存；新建分析窗口将使用新配置")

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._refresh_timer.stop()
        self._quote_worker.stop()
        self._quote_worker.wait(10_000)
        for terminal in tuple(self._terminals.values()):
            terminal.close()
        data_source = getattr(self._ctx, "data_source", None)
        if data_source is not None:
            try:
                data_source.unsubscribe()
            except Exception:  # noqa: BLE001
                pass
            try:
                data_source.disconnect()
            except Exception:  # noqa: BLE001
                pass
        super().closeEvent(event)

