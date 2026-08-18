from __future__ import annotations

from types import SimpleNamespace

from pa_agent.gui.main_window import MainWindow


class _StatusBar:
    def __init__(self) -> None:
        self.message = ""

    def showMessage(self, message: str) -> None:  # noqa: N802
        self.message = message


class _IncrementalHistoryState:
    def __init__(self) -> None:
        self._auto_incremental_pending = True
        self._incremental_available = False
        self._status_bar = _StatusBar()
        self._ctx = SimpleNamespace(
            settings=SimpleNamespace(
                general=SimpleNamespace(incremental_max_new_bars=10)
            )
        )
        self.label_refreshed = False

    def _refresh_incremental_label(self) -> None:
        self.label_refreshed = True


def test_history_record_changes_button_state_without_auto_start(
    monkeypatch,
) -> None:
    import pa_agent.records.analysis_history as history

    state = _IncrementalHistoryState()
    monkeypatch.setattr(history, "find_latest_successful_record", lambda **_kwargs: object())

    MainWindow._check_auto_incremental(state, "159732", "15m")

    assert state._incremental_available is False
    assert state._auto_incremental_pending is False
    assert state.label_refreshed is True
    assert "已切换为提交分析" in state._status_bar.message
