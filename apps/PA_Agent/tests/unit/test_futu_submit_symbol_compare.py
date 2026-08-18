from __future__ import annotations

from types import SimpleNamespace

from pa_agent.gui.main_window import MainWindow


class _TextValue:
    def __init__(self, value: str) -> None:
        self._value = value

    def currentText(self) -> str:  # noqa: N802
        return self._value


class _Unchecked:
    def isChecked(self) -> bool:  # noqa: N802
        return False


class _FutuSubmitState:
    def __init__(self) -> None:
        self._auto_incremental_pending = True
        self._ctx = SimpleNamespace(
            data_source=SimpleNamespace(_symbol="SZ.159732", _timeframe="15m")
        )
        self._symbol_combo = _TextValue("159732")
        self._tf_combo = _TextValue("15m")
        self._wait_close_checkbox = _Unchecked()
        self.switch_calls = 0
        self.started: tuple | None = None

    def _can_submit(self) -> bool:
        return True

    def _clear_history_overlay(self) -> None:
        pass

    def _current_data_source_kind(self) -> str:
        return "futu"

    def _tv_exchange_text(self) -> str:
        return "SZSE"

    def _on_symbol_or_tf_changed(self, *_args) -> None:
        self.switch_calls += 1

    def _cancel_analysis_worker(self) -> None:
        pass

    def _analysis_bar_count(self) -> int:
        return 120

    def _start_analysis(self, *args, **kwargs) -> None:
        self.started = (args, kwargs)


def test_futu_bare_symbol_does_not_retrigger_subscription_on_submit() -> None:
    state = _FutuSubmitState()

    MainWindow._begin_submit_analysis(state, force_incremental=False)

    assert state.switch_calls == 0
    assert state.started is not None
    assert state.started[0] == ("159732", "15m", 120)
