"""Live refresh must not build chart data while the user is panning."""
from __future__ import annotations

from types import SimpleNamespace

from pa_agent.gui.main_window import MainWindow


def test_dragging_keeps_only_the_latest_refresh_snapshot() -> None:
    first_bars = ["first"]
    latest_bars = ["latest"]
    window = SimpleNamespace(
        _chart_dragging=True,
        _deferred_chart_bars=None,
    )

    MainWindow._on_refresh_frame_ready(window, first_bars)
    MainWindow._on_refresh_frame_ready(window, latest_bars)

    assert window._deferred_chart_bars == latest_bars
    assert window._deferred_chart_bars is not latest_bars


def test_drag_release_processes_one_latest_refresh_snapshot() -> None:
    bars = ["latest"]
    handled: list[list[str]] = []
    window = SimpleNamespace(
        _chart_dragging=True,
        _deferred_chart_bars=None,
        _on_refresh_frame_ready=lambda latest: handled.append(latest),
    )

    MainWindow._on_refresh_frame_ready(window, bars)
    MainWindow._on_chart_dragging_changed(window, False)

    assert window._chart_dragging is False
    assert handled == [bars]
    assert window._deferred_chart_bars is None
