from __future__ import annotations

from PyQt6.QtWidgets import QPushButton

from pa_agent.gui.main_window import MainWindow


class _WindowState:
    """Minimal state required by MainWindow._refresh_incremental_label."""

    def __init__(self, previous_record: object | None) -> None:
        self._analysis_in_progress = False
        self._incremental_available = False
        self._last_frame_ready_bars = [object()]
        self._submit_btn = QPushButton()
        self._previous_record = previous_record
        self._symbol_combo = _TextValue("159732")
        self._tf_combo = _TextValue("15m")

    def _analysis_bar_count(self) -> int:
        return 100

    def _build_chart_frame_from_bars(self, *_args, **_kwargs) -> object:
        return object()

    def _find_incremental_base_record(self, *_args, **_kwargs):
        return self._previous_record, 1, "matched"

    def _set_full_analysis_mode(self) -> None:
        self._incremental_available = False
        self._submit_btn.setText("提交分析")


def test_compatible_history_switches_the_single_button_to_incremental(qtbot) -> None:
    state = _WindowState(previous_record=object())
    qtbot.addWidget(state._submit_btn)

    MainWindow._refresh_incremental_label(state)

    assert state._incremental_available is True
    assert state._submit_btn.text() == "增量分析"


def test_unmatched_history_uses_normal_submit_label(qtbot) -> None:
    state = _WindowState(previous_record=None)
    qtbot.addWidget(state._submit_btn)

    MainWindow._refresh_incremental_label(state)

    assert state._incremental_available is False
    assert state._submit_btn.text() == "提交分析"


def test_disabled_button_uses_normal_submit_label(qtbot) -> None:
    state = _WindowState(previous_record=object())
    qtbot.addWidget(state._submit_btn)
    state._submit_btn.setEnabled(False)

    MainWindow._refresh_incremental_label(state)

    assert state._incremental_available is False
    assert state._submit_btn.text() == "提交分析"


class _TextValue:
    def __init__(self, value: str) -> None:
        self._value = value

    def currentText(self) -> str:  # noqa: N802
        return self._value


def test_submit_click_never_forces_incremental_analysis() -> None:
    class _SubmitState:
        force_incremental: bool | None = None

        def _begin_submit_analysis(self, *, force_incremental: bool) -> None:
            self.force_incremental = force_incremental

    state = _SubmitState()

    MainWindow._on_submit_analysis(state)

    assert state.force_incremental is False

