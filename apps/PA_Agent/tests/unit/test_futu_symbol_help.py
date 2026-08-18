from __future__ import annotations

from types import SimpleNamespace


def test_futu_symbol_help_button_shows_the_format_guidance(qtbot, monkeypatch) -> None:
    from PyQt6.QtWidgets import QToolButton

    from pa_agent.gui import main_window

    button = QToolButton()
    qtbot.addWidget(button)
    state = SimpleNamespace(_futu_symbol_help_button=button)
    shown: dict[str, object] = {}

    class Tooltip:
        @staticmethod
        def showText(position, text, widget) -> None:  # noqa: N802
            shown.update(position=position, text=text, widget=widget)

    monkeypatch.setattr(main_window, "QToolTip", Tooltip)

    main_window.MainWindow._show_futu_symbol_format_help(state)

    assert shown["text"] == main_window._FUTU_SYMBOL_FORMAT_HELP
    assert "HK.07709" in shown["text"]
    assert "US.NVDA" in shown["text"]
    assert "HK.GDUmain" in shown["text"]
    assert shown["widget"] is button


def test_watchlist_symbol_help_button_uses_the_shared_guidance(qtbot, monkeypatch) -> None:
    from PyQt6.QtWidgets import QToolButton

    from pa_agent.gui import workspace_window
    from pa_agent.gui.futu_symbol_help import FUTU_SYMBOL_FORMAT_HELP
    from pa_agent.records.watchlist import WatchlistState, WatchlistStore

    store = WatchlistStore()
    store.state = WatchlistState()
    monkeypatch.setattr(store, "_save", lambda: None)
    dashboard = workspace_window.WatchlistDashboard(store, pool_tracking_enabled=False)
    qtbot.addWidget(dashboard)
    button = dashboard._symbol_format_help_button
    assert isinstance(button, QToolButton)
    assert button.toolTip() == FUTU_SYMBOL_FORMAT_HELP

    shown: dict[str, object] = {}

    class Tooltip:
        @staticmethod
        def showText(position, text, widget) -> None:  # noqa: N802
            shown.update(position=position, text=text, widget=widget)

    monkeypatch.setattr(workspace_window, "QToolTip", Tooltip)
    dashboard._show_symbol_format_help()

    assert shown["text"] == FUTU_SYMBOL_FORMAT_HELP
    assert shown["widget"] is button
