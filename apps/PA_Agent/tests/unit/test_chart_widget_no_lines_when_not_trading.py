"""Test that ChartWidget does not draw InfiniteLine items when order_type == '不下单'.

Task 14.7 — pytest-qt test.

Validates: Requirements R9.4, R10.2
"""
from __future__ import annotations

import pytest

# Guard: skip the whole module if PyQt6 / pyqtgraph are not available
pytest.importorskip("PyQt6")
pytest.importorskip("pyqtgraph")


@pytest.fixture
def chart_widget(qtbot):
    """Create a ChartWidget and register it with qtbot."""
    from pa_agent.gui.chart_widget import ChartWidget

    widget = ChartWidget()
    qtbot.addWidget(widget)
    return widget


def _count_infinite_lines(plot_widget) -> int:
    """Count entry/TP/SL lines, excluding hidden hover-guide lines."""
    import pyqtgraph as pg

    hover_guides = {
        plot_widget._hover_close_line,
        plot_widget._hover_time_line,
    }
    return sum(
        1
        for item in plot_widget.getPlotItem().items
        if isinstance(item, pg.InfiniteLine) and item not in hover_guides
    )


class TestNoLinesWhenNotTrading:
    """ChartWidget must not show InfiniteLine items for '不下单' decisions."""

    def test_no_infinite_lines_after_no_order_decision(self, chart_widget):
        """set_decision with order_type='不下单' must leave zero InfiniteLine items."""
        decision = {
            "order_type": "不下单",
            "entry_price": None,
            "take_profit_price": None,
            "stop_loss_price": None,
            "reasoning": "市场结构不明朗，暂不入场。",
        }
        chart_widget.set_decision(decision)

        assert _count_infinite_lines(chart_widget) == 0, (
            "Expected no InfiniteLine items after a '不下单' decision, "
            f"but found {_count_infinite_lines(chart_widget)}."
        )

    def test_lines_cleared_when_switching_to_no_order(self, chart_widget):
        """Lines drawn for a trading decision must be cleared when '不下单' is set."""
        trading_decision = {
            "order_type": "限价单",
            "order_direction": "做多",
            "entry_price": 1900.0,
            "take_profit_price": 1920.0,
            "stop_loss_price": 1880.0,
            "reasoning": "上升趋势明确。",
        }
        chart_widget.set_decision(trading_decision)

        # Sanity check: lines should exist now
        assert _count_infinite_lines(chart_widget) > 0, (
            "Expected InfiniteLine items after a trading decision."
        )

        # Now switch to 不下单
        no_order_decision = {
            "order_type": "不下单",
            "entry_price": None,
            "take_profit_price": None,
            "stop_loss_price": None,
            "reasoning": "风险过高。",
        }
        chart_widget.set_decision(no_order_decision)

        assert _count_infinite_lines(chart_widget) == 0, (
            "Expected no InfiniteLine items after switching to '不下单', "
            f"but found {_count_infinite_lines(chart_widget)}."
        )

    def test_continuity_overlay_keeps_lines_on_wait_no_order(self, chart_widget):
        """不下单 + chart_overlay_active should still draw prior entry/TP/SL."""
        chart_widget.set_decision({
            "order_type": "不下单",
            "order_direction": "做多",
            "entry_price": 2640.0,
            "take_profit_price": 2660.0,
            "stop_loss_price": 2630.0,
            "chart_overlay_active": True,
            "reasoning": "延续等待上一轮限价方案。",
        })
        assert _count_infinite_lines(chart_widget) == 3

    def test_short_decision_shows_down_arrow(self, chart_widget, qtbot):
        """做空 decision draws a ▼ marker at the newest bar."""
        from pa_agent.data.base import KlineBar, KlineFrame, IndicatorBundle

        bars = tuple(
            KlineBar(
                seq=i + 1,
                ts_open=1_700_000_000_000 - i * 3_600_000,
                open=2000.0,
                high=2010.0,
                low=1990.0,
                close=2005.0,
                volume=100.0,
                closed=True,
            )
            for i in range(5)
        )
        frame = KlineFrame(
            symbol="XAUUSD",
            timeframe="1h",
            bars=bars,
            snapshot_ts_local_ms=1_700_000_000_000,
            indicators=IndicatorBundle(ema20=tuple([2000.0] * 5), atr14=tuple([10.0] * 5)),
        )
        chart_widget.set_frame(frame)
        qtbot.wait(100)

        chart_widget.set_decision({
            "order_type": "限价单",
            "order_direction": "做空",
            "entry_price": 2000.0,
            "take_profit_price": 1980.0,
            "stop_loss_price": 2010.0,
        })
        qtbot.wait(100)

        assert len(chart_widget._direction_items) >= 1

    def test_reset_clears_all_lines(self, chart_widget):
        """reset() must remove all InfiniteLine items."""
        trading_decision = {
            "order_type": "市价单",
            "order_direction": "做空",
            "entry_price": 1850.0,
            "take_profit_price": 1830.0,
            "stop_loss_price": 1870.0,
            "reasoning": "下降趋势。",
        }
        chart_widget.set_decision(trading_decision)
        chart_widget.reset()

        assert _count_infinite_lines(chart_widget) == 0, (
            "Expected no InfiniteLine items after reset(), "
            f"but found {_count_infinite_lines(chart_widget)}."
        )

    def test_clear_decision_overlay_keeps_lines_gone(self, chart_widget):
        """clear_decision_overlay() removes trade lines without requiring reset()."""
        chart_widget.set_decision({
            "order_type": "限价单",
            "order_direction": "做多",
            "entry_price": 1900.0,
            "take_profit_price": 1920.0,
            "stop_loss_price": 1880.0,
        })
        chart_widget.clear_decision_overlay()
        assert _count_infinite_lines(chart_widget) == 0
        assert chart_widget._pending_decision is None
