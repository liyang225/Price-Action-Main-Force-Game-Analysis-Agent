import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QAbstractItemView, QApplication, QMessageBox, QTableWidgetItem

from pa_agent.gui.workspace_window import (
    WorkspaceWindow,
    WatchlistDashboard,
    _PoolTableWidget,
    _WatchlistQuoteWorker,
)
from pa_agent.records.watchlist import WatchlistItem, WatchlistState, WatchlistStore


def _mouse_event(event_type, pos: QPoint) -> QMouseEvent:
    return QMouseEvent(
        event_type,
        QPointF(pos),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _click_cell(table: _PoolTableWidget, row: int, column: int) -> None:
    pos = table.visualItemRect(table.item(row, column)).center()
    QApplication.sendEvent(table.viewport(), _mouse_event(QMouseEvent.Type.MouseButtonPress, pos))
    QApplication.sendEvent(table.viewport(), _mouse_event(QMouseEvent.Type.MouseButtonRelease, pos))


def _checked_ids(table: _PoolTableWidget) -> list[str]:
    ids: list[str] = []
    for row in range(table.rowCount()):
        item = table.item(row, 0)
        if item is not None and item.checkState() == Qt.CheckState.Checked:
            ids.append(item.data(Qt.ItemDataRole.UserRole))
    return ids


def test_checkbox_clicks_accumulate_beyond_two_rows() -> None:
    app = QApplication.instance() or QApplication([])

    table = _PoolTableWidget()
    table.setColumnCount(2)
    table.setRowCount(3)
    for row, item_id in enumerate(("stock-1", "stock-2", "stock-3")):
        checkbox = QTableWidgetItem()
        checkbox.setData(Qt.ItemDataRole.UserRole, item_id)
        checkbox.setFlags(
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsUserCheckable
        )
        checkbox.setCheckState(Qt.CheckState.Unchecked)
        table.setItem(row, 0, checkbox)
        table.setItem(row, 1, QTableWidgetItem(item_id))

    table.resize(320, 180)
    table.show()
    app.processEvents()

    _click_cell(table, 0, 0)
    _click_cell(table, 1, 0)
    _click_cell(table, 2, 0)

    assert _checked_ids(table) == ["stock-1", "stock-2", "stock-3"]
    assert table.selected_item_ids() == ["stock-1", "stock-2", "stock-3"]
    assert not table.selectedIndexes()

    _click_cell(table, 1, 0)

    assert _checked_ids(table) == ["stock-1", "stock-3"]
    assert table.selected_item_ids() == ["stock-1", "stock-3"]


def test_pool_table_selection_has_no_row_highlight() -> None:
    app = QApplication.instance() or QApplication([])
    table = _PoolTableWidget()

    assert app is not None
    assert table.selectionMode() == QAbstractItemView.SelectionMode.NoSelection


def test_row_and_select_all_checkboxes_share_rounded_square_shape() -> None:
    table = _PoolTableWidget()

    assert "border-radius: 3px" in table.styleSheet()
    assert "border-radius: 3px" in WatchlistDashboard._selection_checkbox_style()


def test_watchlist_uses_live_quote_columns_and_chinese_market_colors(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    store = WatchlistStore()
    store.state = WatchlistState()
    monkeypatch.setattr(store, "_save", lambda: None)
    item = store.add(name="Test stock", symbol="600519")
    dashboard = WatchlistDashboard(store, pool_tracking_enabled=False)

    dashboard.refresh(
        lambda _item: ("等待分析", False),
        lambda _item: (12.3456, 2.5),
    )

    table = dashboard._watchlist_table
    headers = [table.horizontalHeaderItem(column).text() for column in range(table.columnCount())]
    assert app is not None
    assert item.id
    assert "分析状态" not in headers
    assert "周期" not in headers
    assert headers[1:6] == ["名称", "分析池", "代码", "实时价格", "涨跌幅"]
    price_column = headers.index("实时价格")
    change_column = headers.index("涨跌幅")
    assert table.item(0, price_column).text() == "12.3456"
    assert table.item(0, price_column).foreground().color().name() == "#e8ecf1"
    assert table.item(0, change_column).text() == "+2.50%"
    assert table.item(0, change_column).foreground().color().name() == "#ff5353"

    dashboard.refresh(
        lambda _item: ("等待分析", False),
        lambda _item: (12.0, -1.5),
    )
    assert table.item(0, change_column).foreground().color().name() == "#28c086"

    pool_headers = [
        dashboard._pool_table.horizontalHeaderItem(column).text()
        for column in range(dashboard._pool_table.columnCount())
    ]
    assert pool_headers[2:4] == ["代码", "周期"]


def test_analysis_pool_status_uses_white_or_muted_gray(qtbot, monkeypatch) -> None:
    store = WatchlistStore()
    store.state = WatchlistState()
    monkeypatch.setattr(store, "_save", lambda: None)
    item = store.add(name="Test stock", symbol="600519")
    dashboard = WatchlistDashboard(store, pool_tracking_enabled=False)
    qtbot.addWidget(dashboard)

    dashboard.refresh()
    pool_column = next(
        column
        for column in range(dashboard._watchlist_table.columnCount())
        if dashboard._watchlist_table.horizontalHeaderItem(column).text() == "分析池"
    )
    assert dashboard._watchlist_table.item(0, pool_column).text() == "未加入"
    assert dashboard._watchlist_table.item(0, pool_column).foreground().color().name() == "#646e7a"

    store.add_to_analysis_pool([item.id])
    dashboard.refresh()
    assert dashboard._watchlist_table.item(0, pool_column).text() == "已加入"
    assert dashboard._watchlist_table.item(0, pool_column).foreground().color().name() == "#e8ecf1"


def test_adding_to_analysis_pool_does_not_create_terminal(monkeypatch) -> None:
    store = WatchlistStore()
    store.state = WatchlistState()
    monkeypatch.setattr(store, "_save", lambda: None)
    item = store.add(name="Test stock", symbol="600519")
    window = WorkspaceWindow.__new__(WorkspaceWindow)
    window._store = store
    window._pool_tracking_enabled = False
    window._refresh_dashboard = lambda: None
    created: list[str] = []
    window._ensure_terminal = lambda item_id: created.append(item_id)

    window._add_to_pool([item.id])

    assert store.in_analysis_pool(item.id)
    assert created == []


def test_adding_to_analysis_pool_inherits_enabled_continuous_tracking(monkeypatch) -> None:
    store = WatchlistStore()
    store.state = WatchlistState()
    monkeypatch.setattr(store, "_save", lambda: None)
    item = store.add(name="Test stock", symbol="600519")
    window = WorkspaceWindow.__new__(WorkspaceWindow)
    window._store = store
    window._pool_tracking_enabled = True
    window._pool_tracking_item_ids = set()
    window._refresh_dashboard = lambda: None

    class _Terminal:
        def __init__(self) -> None:
            self.tracking_states: list[bool] = []

        def set_workspace_continuous_tracking(self, enabled: bool) -> None:
            self.tracking_states.append(enabled)

    terminal = _Terminal()
    created: list[str] = []

    def _ensure_terminal(item_id: str):
        created.append(item_id)
        return terminal

    window._ensure_terminal = _ensure_terminal

    window._add_to_pool([item.id])

    assert store.in_analysis_pool(item.id)
    assert created == [item.id]
    assert terminal.tracking_states == [True]


def test_terminal_tracking_changes_update_pool_switch_and_per_item_state(monkeypatch) -> None:
    store = WatchlistStore()
    store.state = WatchlistState()
    monkeypatch.setattr(store, "_save", lambda: None)
    first = store.add(name="First", symbol="600519")
    second = store.add(name="Second", symbol="000001")
    store.add_to_analysis_pool([first.id, second.id])

    class _Dashboard:
        def __init__(self) -> None:
            self.switch_states: list[bool] = []

        def set_pool_tracking_enabled(self, enabled: bool) -> None:
            self.switch_states.append(enabled)

    window = WorkspaceWindow.__new__(WorkspaceWindow)
    window._store = store
    window._dashboard = _Dashboard()
    window._pool_tracking_enabled = True
    window._pool_tracking_item_ids = {first.id, second.id}
    window._pool_tracking_syncing = False
    window._refresh_dashboard = lambda: None

    window._on_terminal_tracking_changed(first.id, False)

    assert first.id not in window._pool_tracking_item_ids
    assert second.id in window._pool_tracking_item_ids
    assert window._pool_tracking_enabled is True
    assert window._dashboard.switch_states[-1] is True

    window._on_terminal_tracking_changed(second.id, False)

    assert window._pool_tracking_item_ids == set()
    assert window._pool_tracking_enabled is False
    assert window._dashboard.switch_states[-1] is False


def test_manually_enabling_terminal_adds_stock_to_analysis_pool(monkeypatch) -> None:
    store = WatchlistStore()
    store.state = WatchlistState()
    monkeypatch.setattr(store, "_save", lambda: None)
    item = store.add(name="Test stock", symbol="600519")

    class _Dashboard:
        def __init__(self) -> None:
            self.switch_states: list[bool] = []

        def set_pool_tracking_enabled(self, enabled: bool) -> None:
            self.switch_states.append(enabled)

    window = WorkspaceWindow.__new__(WorkspaceWindow)
    window._store = store
    window._dashboard = _Dashboard()
    window._pool_tracking_enabled = False
    window._pool_tracking_item_ids = set()
    window._pool_tracking_syncing = False
    window._refresh_dashboard = lambda: None

    window._on_terminal_tracking_changed(item.id, True)

    assert store.in_analysis_pool(item.id)
    assert window._pool_tracking_item_ids == {item.id}
    assert window._pool_tracking_enabled is True
    assert window._dashboard.switch_states[-1] is True


def test_closing_terminal_pauses_its_pool_tracking(monkeypatch) -> None:
    store = WatchlistStore()
    store.state = WatchlistState()
    monkeypatch.setattr(store, "_save", lambda: None)
    item = store.add(name="Test stock", symbol="600519")
    store.add_to_analysis_pool([item.id])

    class _Dashboard:
        def __init__(self) -> None:
            self.switch_states: list[bool] = []

        def set_pool_tracking_enabled(self, enabled: bool) -> None:
            self.switch_states.append(enabled)

    class _Tabs:
        def __init__(self, terminal) -> None:
            self.terminal = terminal
            self.removed: list[int] = []

        def widget(self, _index: int):
            return self.terminal

        def removeTab(self, index: int) -> None:
            self.removed.append(index)

    class _Terminal:
        def __init__(self) -> None:
            self.tracking_states: list[bool] = []
            self.closed = False
            self.deleted = False

        def set_workspace_continuous_tracking(self, enabled: bool) -> None:
            self.tracking_states.append(enabled)

        def close(self) -> None:
            self.closed = True

        def deleteLater(self) -> None:
            self.deleted = True

    terminal = _Terminal()
    window = WorkspaceWindow.__new__(WorkspaceWindow)
    window._store = store
    window._dashboard = _Dashboard()
    window._tabs = _Tabs(terminal)
    window._terminals = {item.id: terminal}
    window._pool_tracking_enabled = True
    window._pool_tracking_item_ids = {item.id}
    window._pool_tracking_syncing = False
    window._update_tab_widget_layout = lambda: None
    window._refresh_dashboard = lambda: None

    window._close_tab(1)

    assert terminal.tracking_states == [False]
    assert terminal.closed is True
    assert terminal.deleted is True
    assert window._pool_tracking_item_ids == set()
    assert window._pool_tracking_enabled is False
    assert window._dashboard.switch_states[-1] is False


def test_dashboard_uses_40_60_split_and_requested_name_column_widths(monkeypatch) -> None:
    from PyQt6.QtWidgets import QSplitter

    app = QApplication.instance() or QApplication([])
    store = WatchlistStore()
    store.state = WatchlistState()
    monkeypatch.setattr(store, "_save", lambda: None)
    store.add(name="Test stock", symbol="600519")
    dashboard = WatchlistDashboard(store, pool_tracking_enabled=False)
    dashboard.resize(1440, 700)
    dashboard.show()
    app.processEvents()

    splitter = dashboard.findChild(QSplitter)
    assert splitter is not None
    left_width, right_width = splitter.sizes()
    assert left_width / (left_width + right_width) == pytest.approx(0.4, abs=0.02)
    assert dashboard._watchlist_table.columnWidth(1) == 220
    assert dashboard._pool_table.columnWidth(1) == 217


def test_dashboard_restores_saved_watchlist_pool_split_ratio(monkeypatch) -> None:
    from pa_agent.config.settings import Settings

    app = QApplication.instance() or QApplication([])
    store = WatchlistStore()
    store.state = WatchlistState()
    monkeypatch.setattr(store, "_save", lambda: None)
    settings = Settings()
    settings.general.split_hotzone_ratios["watchlist_pool"] = 0.57
    dashboard = WatchlistDashboard(
        store,
        pool_tracking_enabled=False,
        settings=settings,
    )
    dashboard.resize(1440, 700)
    dashboard.show()
    app.processEvents()

    sizes = dashboard._watchlist_pool_splitter.sizes()
    assert sum(sizes) > 0
    assert 0.54 <= sizes[0] / sum(sizes) <= 0.60

    dashboard._watchlist_table.setColumnWidth(1, 180)
    dashboard.refresh()
    assert dashboard._watchlist_table.columnWidth(1) == 180


def test_dashboard_reserves_tab_button_height_only_without_terminal_tabs(monkeypatch) -> None:
    from PyQt6.QtWidgets import QVBoxLayout

    store = WatchlistStore()
    store.state = WatchlistState()
    monkeypatch.setattr(store, "_save", lambda: None)
    dashboard = WatchlistDashboard(store, pool_tracking_enabled=False)

    layout = dashboard.layout()
    assert isinstance(layout, QVBoxLayout)
    assert layout.contentsMargins().top() == 40
    assert dashboard._continuous_button.text() == "持续分析"
    assert dashboard._pool_table.horizontalHeaderItem(
        dashboard._pool_table.columnCount() - 1
    ).text() == "持续分析"

    dashboard.set_tab_bar_reserved(True)
    assert layout.contentsMargins().top() == 8

    dashboard.set_tab_bar_reserved(False)
    assert layout.contentsMargins().top() == 40


def test_watchlist_quote_worker_prefers_api_market_summary() -> None:
    class _Source:
        def latest_snapshot(self, _count: int):
            return [type("Bar", (), {"close": 10.0, "open": 9.8, "pct_chg": None})()]

        def latest_market_summary(self):
            return {"last_price": 10.5, "change_rate": 2.5}

    assert _WatchlistQuoteWorker._read_quote(_Source()) == (10.5, 2.5)


def test_delete_watchlist_item_removes_confirmed_item(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    store = WatchlistStore()
    store.state = WatchlistState()
    monkeypatch.setattr(store, "_save", lambda: None)
    item = store.add(name="Test stock", symbol="TEST")
    dashboard = WatchlistDashboard(store, pool_tracking_enabled=False)
    deleted_ids: list[str] = []
    dashboard.watchlist_item_deleted.connect(deleted_ids.append)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args: QMessageBox.StandardButton.Yes,
    )

    dashboard._delete_watchlist_item(item.id)

    assert store.get(item.id) is None
    assert deleted_ids == [item.id]


def test_exchange_update_invalidates_existing_terminal(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    store = WatchlistStore()
    store.state = WatchlistState()
    monkeypatch.setattr(store, "_save", lambda: None)
    item = store.add(name="Test stock", symbol="600519", exchange="SSE")
    dashboard = WatchlistDashboard(store, pool_tracking_enabled=False)
    updated_ids: list[str] = []
    dashboard.watchlist_item_updated.connect(updated_ids.append)

    dashboard._update_watchlist_item(item.id, exchange="SZSE")

    assert app is not None
    assert store.get(item.id).exchange == "SZSE"
    assert updated_ids == [item.id]


def test_terminal_exchange_comes_from_its_isolated_context() -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.config.settings import Settings
    from pa_agent.gui.main_window import _tradingview_exchange_from_context

    settings = Settings()
    settings.general.last_tradingview_exchange = "SSE"

    assert _tradingview_exchange_from_context(AppContext(settings=settings)) == "SSE"


def test_analysis_window_target_uses_name_and_exchange_prefixed_code() -> None:
    item = WatchlistItem(
        id="consumer-etf",
        name="消费电子ETF",
        symbol="159732",
        exchange="SZ",
    )

    assert WorkspaceWindow._terminal_tab_label(item) == "消费电子ETF（SZ.159732）"


def test_analysis_window_target_normalizes_market_prefix_aliases() -> None:
    item = WatchlistItem(
        id="us-stock",
        name="Apple",
        symbol="AAPL",
        exchange="NASDAQ",
    )
    suffix_item = WatchlistItem(
        id="sh-stock",
        name="贵州茅台",
        symbol="600519.SH",
    )

    assert WorkspaceWindow._terminal_tab_label(item) == "Apple（US.AAPL）"
    assert WorkspaceWindow._terminal_tab_label(suffix_item) == "贵州茅台（SH.600519）"


def _drag_between_cells(table: _PoolTableWidget, start_row: int, start_column: int, end_row: int, end_column: int) -> None:
    start = table.visualItemRect(table.item(start_row, start_column)).center()
    end = table.visualItemRect(table.item(end_row, end_column)).center()
    QApplication.sendEvent(table.viewport(), _mouse_event(QMouseEvent.Type.MouseButtonPress, start))
    QApplication.sendEvent(table.viewport(), _mouse_event(QMouseEvent.Type.MouseMove, end))
    QApplication.sendEvent(table.viewport(), _mouse_event(QMouseEvent.Type.MouseButtonRelease, end))


def test_rubber_band_from_name_column_checks_rows() -> None:
    app = QApplication.instance() or QApplication([])

    table = _PoolTableWidget()
    table.setColumnCount(3)
    table.setHorizontalHeaderLabels(("选择", "自选股名称", "代码"))
    table.setRowCount(3)
    for row, item_id in enumerate(("stock-1", "stock-2", "stock-3")):
        checkbox = QTableWidgetItem()
        checkbox.setData(Qt.ItemDataRole.UserRole, item_id)
        checkbox.setFlags(
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsUserCheckable
        )
        checkbox.setCheckState(Qt.CheckState.Unchecked)
        table.setItem(row, 0, checkbox)
        table.setItem(row, 1, QTableWidgetItem(f"Name {row}"))
        table.setItem(row, 2, QTableWidgetItem(item_id))

    table.resize(420, 180)
    table.show()
    app.processEvents()

    _drag_between_cells(table, 0, 1, 2, 1)

    assert _checked_ids(table) == ["stock-1", "stock-2", "stock-3"]
