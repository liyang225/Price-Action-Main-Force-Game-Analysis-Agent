"""Chart rendering must not require the configured AI analysis bar count."""
from __future__ import annotations

from types import SimpleNamespace

from pa_agent.data.base import KlineBar
from pa_agent.gui.main_window import MainWindow


def _bar(sequence: int) -> KlineBar:
    return KlineBar(
        seq=sequence,
        ts_open=float(1_700_000_000_000 - sequence * 15 * 60_000),
        open=10.0,
        high=11.0,
        low=9.0,
        close=10.5,
        volume=100.0,
        closed=True,
    )


def test_chart_uses_available_bars_when_fewer_than_analysis_setting() -> None:
    window = SimpleNamespace(
        _analysis_bar_count=lambda: 108,
        _symbol_combo=SimpleNamespace(currentText=lambda: "SH.600519"),
        _tf_combo=SimpleNamespace(currentText=lambda: "15m"),
        _reference_now_ms=lambda: 1_700_000_100_000,
    )
    bars = [_bar(sequence) for sequence in range(1, 38)]

    frame = MainWindow._build_chart_frame_from_bars(
        window,
        bars,
        include_forming=False,
    )

    assert frame is not None
    assert len(frame.bars) == 37
