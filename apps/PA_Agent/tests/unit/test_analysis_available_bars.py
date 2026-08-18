"""Analysis may use all currently available closed K-lines."""
from __future__ import annotations

from types import SimpleNamespace

from pa_agent.data.base import KlineBar
from pa_agent.data.snapshot import build_display_frame
from pa_agent.gui.main_window import MainWindow


def _bar(sequence: int, *, closed: bool = True) -> KlineBar:
    return KlineBar(
        seq=sequence,
        ts_open=float(1_700_000_000_000 - sequence * 15 * 60_000),
        open=10.0,
        high=11.0,
        low=9.0,
        close=10.5,
        volume=100.0,
        closed=closed,
    )


def _window() -> SimpleNamespace:
    window = SimpleNamespace(
        _symbol_combo=SimpleNamespace(currentText=lambda: "SH.600519"),
        _tf_combo=SimpleNamespace(currentText=lambda: "15m"),
        _reference_now_ms=lambda: 1_700_000_100_000,
    )
    window._effective_analysis_bar_count = lambda bars, bar_count: (
        MainWindow._effective_analysis_bar_count(window, bars, bar_count)
    )
    return window


def test_analysis_uses_available_bars_below_configured_target() -> None:
    window = _window()
    bars = [_bar(sequence) for sequence in range(1, 38)]

    available = MainWindow._effective_analysis_bar_count(window, bars, 108)

    assert available == 37
    assert MainWindow._bars_sufficient_for_analysis(window, bars, 108)
    frame = build_display_frame(
        bars, available, "SH.600519", "15m", now_ms=window._reference_now_ms()
    )
    assert frame is not None
    assert len(frame.bars) == 37


def test_analysis_excludes_forming_bar_from_available_sample() -> None:
    window = _window()
    bars = [_bar(0, closed=False), *[_bar(sequence) for sequence in range(1, 38)]]

    assert MainWindow._effective_analysis_bar_count(window, bars, 108) == 37
