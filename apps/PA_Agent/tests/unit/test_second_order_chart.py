from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")
pytest.importorskip("pyqtgraph")


def _frame(count: int = 20):
    from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame

    bars = tuple(
        KlineBar(
            seq=index + 1,
            ts_open=1_700_000_000_000 - index * 7_200_000,
            open=100.0 + index / 10,
            high=101.0 + index / 10,
            low=99.0 + index / 10,
            close=100.5 + index / 10,
            volume=1_000.0,
            closed=True,
        )
        for index in range(count)
    )
    return KlineFrame(
        symbol="SH.600519",
        timeframe="2h",
        bars=bars,
        indicators=IndicatorBundle(
            ema20=tuple(100.0 for _ in bars),
            atr14=tuple(2.0 for _ in bars),
        ),
        snapshot_ts_local_ms=1_700_000_000_000,
    )


def _series(count: int = 20) -> list[dict]:
    result = []
    for index in range(count):
        active = index == count - 1
        result.append(
            {
                "bar_time": f"2026-08-{index + 1:02d} 15:00:00",
                "status": "available",
                "reason": None,
                "signal": {
                    "nash": {
                        "center": 100.0 + index / 10,
                        "upper": 103.0 + index / 10,
                        "lower": 97.0 + index / 10,
                    },
                    "features": {
                        "contrarian_buy": active,
                        "contrarian_sell": active,
                    },
                    "liquidity_trap": {
                        "upper": active,
                        "lower": active,
                    },
                    "smart_money": {"positive": active},
                },
            }
        )
    return result


def test_second_order_chart_owns_game_overlays_and_disables_ema(qtbot) -> None:
    import pyqtgraph as pg

    from pa_agent.gui.second_order_chart import SecondOrderGameChart

    chart = SecondOrderGameChart()
    qtbot.addWidget(chart)
    chart.resize(960, 560)
    chart.show()
    chart.set_game_signal_series(_series())
    chart.set_frame_now(_frame(), fit_view=True)

    assert chart._ema_enabled is False
    assert chart._moving_average_lines == {}
    assert set(chart._nash_lines) == {"center", "upper", "lower"}
    assert all(isinstance(line, pg.PlotDataItem) for line in chart._nash_lines.values())
    assert len(chart._game_marker_items) == 5
    from pa_agent.gui.second_order_chart import create_second_order_chart_legend

    legend = create_second_order_chart_legend()
    assert "纳什均衡带（16根 K 线）" in legend.text()
    assert "金橙色中枢线" in legend.text()
    assert "逆势机会" in legend.text()
    assert "流动性陷阱" in legend.text()
    assert "聪明钱活跃" in legend.text()


def test_generic_chart_keeps_its_existing_ema_default(qtbot) -> None:
    from pa_agent.gui.chart_widget import ChartWidget

    chart = ChartWidget()
    qtbot.addWidget(chart)

    assert chart._ema_enabled is True


def test_offscreen_nash_history_does_not_compress_the_visible_y_axis(qtbot) -> None:
    from pa_agent.gui.second_order_chart import SecondOrderGameChart

    chart = SecondOrderGameChart()
    qtbot.addWidget(chart)
    frame = _frame(40)
    series = _series(40)
    series[0]["signal"]["nash"] = {
        "center": 1_000.0,
        "upper": 1_010.0,
        "lower": 990.0,
    }
    chart.resize(960, 560)
    chart.show()
    chart.set_game_signal_series(series)
    chart.set_frame_now(frame, fit_view=True)
    qtbot.wait(10)

    _x_range, y_range = chart.getViewBox().viewRange()

    assert y_range[1] < 200.0


def test_kline_payload_fetches_once_and_returns_all_loaded_chart_points() -> None:
    from pa_agent.data.base import KlineBar
    from pa_agent.gui.second_order_workspace import _kline_chart_payload

    class Source:
        _symbol = "SH.600519"
        _timeframe = "2h"

        def __init__(self) -> None:
            self.calls = 0
            self.bars = [
                KlineBar(
                    seq=index + 1,
                    ts_open=1_700_000_000_000 - index * 7_200_000,
                    open=100.0 + index / 10,
                    high=101.0 + index / 10,
                    low=99.0 + index / 10,
                    close=100.5 + index / 10,
                    volume=1_000.0 + index,
                    closed=True,
                )
                for index in range(201)
            ]

        def latest_snapshot(self, count: int):
            self.calls += 1
            return self.bars[:count]

    source = Source()

    payload = _kline_chart_payload(
        source,
        {"symbol": "600519.SH", "decision_point": "close"},
    )

    assert source.calls == 1
    assert payload["symbol"] == "600519.SH"
    assert len(payload["bars"]) == 201
    assert len(payload["game_signal_series"]) == 201
    assert payload["game_signal_series"][-1]["status"] == "available"
    assert (
        payload["game_signal_series"][-1]["signal"]["bar_time"]
        == payload["bars"][-1]["time"]
    )
