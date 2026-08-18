"""Chart skip-redraw when frozen closed frame matches snapshot."""
from __future__ import annotations

import math

from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame
from pa_agent.data.snapshot import frame_is_pure_closed, frames_equal_for_chart
from pa_agent.gui.chart_widget import ChartWidget
from pa_agent.gui.theme import tokens as T


def test_chart_colors_match_the_requested_fill_and_outline_values() -> None:
    assert T.CHART_UP == "#E03F4D"
    assert T.CHART_DOWN == "#00B775"
    assert T.CHART_UP_OUTLINE == "#BF3340"
    assert T.CHART_DOWN_OUTLINE == "#00955F"


def _bar(seq: int, ts: float, *, close: float = 10.0, closed: bool = True) -> KlineBar:
    return KlineBar(
        seq=seq,
        ts_open=ts,
        open=1.0,
        high=2.0,
        low=0.5,
        close=close,
        volume=100.0,
        closed=closed,
    )


def _frame(
    *,
    forming: bool = False,
    close: float = 10.0,
    timeframe: str = "15m",
) -> KlineFrame:
    bars = (
        _bar(1, 300.0, close=close, closed=not forming),
        _bar(2, 200.0, close=9.0),
    )
    n = len(bars)
    return KlineFrame(
        symbol="XAUUSD",
        timeframe=timeframe,
        bars=bars,
        indicators=IndicatorBundle(
            ema20=tuple(1.5 for _ in range(n)),
            atr14=tuple(0.5 for _ in range(n)),
        ),
        snapshot_ts_local_ms=1,
    )


def test_frame_is_pure_closed() -> None:
    assert frame_is_pure_closed(_frame())
    assert not frame_is_pure_closed(_frame(forming=True))


def test_frames_equal_ignores_snapshot_ts() -> None:
    a = _frame()
    b = KlineFrame(
        symbol=a.symbol,
        timeframe=a.timeframe,
        bars=a.bars,
        indicators=a.indicators,
        snapshot_ts_local_ms=999,
    )
    assert frames_equal_for_chart(a, b)


def test_set_frame_now_skips_identical_closed_frame(qtbot) -> None:
    widget = ChartWidget()
    qtbot.addWidget(widget)
    f1 = _frame()
    widget.set_frame_now(f1)
    assert widget._batched_candle_item is not None
    count_after_first = widget._batched_candle_item
    f2 = KlineFrame(
        symbol=f1.symbol,
        timeframe=f1.timeframe,
        bars=f1.bars,
        indicators=f1.indicators,
        snapshot_ts_local_ms=2,
    )
    widget.set_frame_now(f2)
    assert widget._batched_candle_item is count_after_first


def test_set_frame_now_skips_identical_forming_frame(qtbot) -> None:
    widget = ChartWidget()
    qtbot.addWidget(widget)
    rendered: list[KlineFrame] = []
    widget.frame_rendered.connect(rendered.append)
    first = _frame(forming=True)
    second = KlineFrame(
        symbol=first.symbol,
        timeframe=first.timeframe,
        bars=first.bars,
        indicators=first.indicators,
        snapshot_ts_local_ms=999,
    )

    widget.set_frame_now(first)
    widget.set_frame_now(second)

    assert rendered == [first]
    assert widget.displayed_frame() is second


def test_set_frame_now_redraws_when_forming_removed(qtbot) -> None:
    widget = ChartWidget()
    qtbot.addWidget(widget)
    widget.set_frame_now(_frame(forming=True))
    batch_forming = widget._batched_candle_item
    widget.set_frame_now(_frame())
    assert widget._batched_candle_item is batch_forming
    assert widget._batched_candle_item is not None
    assert widget._batched_candle_item._forming_bar is None


def test_same_shape_frame_reuses_chart_items(qtbot) -> None:
    widget = ChartWidget()
    qtbot.addWidget(widget)
    widget.set_frame_now(_frame())
    first_batch = widget._batched_candle_item
    first_ema = widget._moving_average_lines["EMA20"]

    widget.set_frame_now(_frame(close=11.0))

    assert widget._batched_candle_item is first_batch
    assert widget._moving_average_lines["EMA20"] is first_ema


def test_hover_guide_snaps_to_the_hovered_bar_close(qtbot) -> None:
    from PyQt6.QtCore import QPointF

    widget = ChartWidget()
    qtbot.addWidget(widget)
    widget.resize(640, 400)
    widget.show()
    widget.set_frame_now(_frame(), fit_view=True)
    qtbot.wait(20)

    # The guide snaps by X position; any Y inside the plot area is valid.
    scene_pos = widget.getViewBox().mapViewToScene(QPointF(1.0, 1.0))
    widget._on_scene_mouse_moved(scene_pos)

    assert widget._hover_close_line.isVisible()
    assert widget._hover_close_line.value() == 10.0
    assert widget._hover_time_line.isVisible()
    assert widget._hover_time_line.value() == 1.0
    assert widget._hover_time_label.isVisible()
    assert widget._hover_time_label.toPlainText() == widget._time_axis.format_timestamp(1)
    assert widget._hover_price_label.isVisible()
    assert widget._hover_price_label.toPlainText() == "10"
    assert widget._hover_price_label.pos().y() == 10.0
    assert widget._hover_change_label.isVisible()
    assert widget._hover_change_label.toPlainText() == "+900.00%"
    assert widget._hover_change_label.textItem.defaultTextColor().name() == "#ff4757"
    x_min, x_max = widget.getViewBox().viewRange()[0]
    assert widget._hover_price_label.pos().x() == x_min
    assert widget._hover_change_label.pos().x() == x_max

    widget._hide_hover_close_line()
    assert not widget._hover_close_line.isVisible()
    assert not widget._hover_time_line.isVisible()
    assert not widget._hover_time_label.isVisible()
    assert not widget._hover_price_label.isVisible()
    assert not widget._hover_change_label.isVisible()


def test_hover_change_label_uses_green_for_a_falling_bar(qtbot) -> None:
    from PyQt6.QtCore import QPointF

    widget = ChartWidget()
    qtbot.addWidget(widget)
    widget.resize(640, 400)
    widget.show()
    widget.set_frame_now(_frame(close=0.5), fit_view=True)
    qtbot.wait(20)

    scene_pos = widget.getViewBox().mapViewToScene(QPointF(1.0, 1.0))
    widget._on_scene_mouse_moved(scene_pos)

    assert widget._hover_change_label.toPlainText() == "-50.00%"
    assert widget._hover_change_label.textItem.defaultTextColor().name() == "#00d084"


def test_forming_bar_tick_updates_only_forming_picture(qtbot) -> None:
    from dataclasses import replace

    widget = ChartWidget()
    qtbot.addWidget(widget)
    first = _frame(forming=True, close=10.0)
    widget.set_frame_now(first)
    batch = widget._batched_candle_item
    closed_picture = batch._closed_picture
    forming_picture = batch._forming_picture
    assert batch._forming_bar is not None

    live = replace(
        first,
        bars=(
            replace(first.bars[0], high=2.5, close=11.0),
            *first.bars[1:],
        ),
    )
    widget.set_frame_now(live)

    assert widget._batched_candle_item is batch
    assert batch._closed_picture is closed_picture
    assert batch._forming_picture is not forming_picture
    assert batch._forming_bar.close == 11.0


def test_closed_frame_to_live_frame_moves_forming_bar_to_the_right(qtbot) -> None:
    widget = ChartWidget()
    qtbot.addWidget(widget)

    closed = _frame()
    live_bars = (
        _bar(0, 400.0, close=10.5, closed=False),
        *closed.bars,
    )
    live = KlineFrame(
        symbol=closed.symbol,
        timeframe=closed.timeframe,
        bars=live_bars,
        indicators=IndicatorBundle(
            ema20=(1.5,) * len(live_bars),
            atr14=(0.5,) * len(live_bars),
        ),
        snapshot_ts_local_ms=2,
    )

    widget.set_frame_now(closed)
    batch = widget._batched_candle_item
    assert batch is not None
    closed_picture = batch._closed_picture

    widget.set_frame_now(live)

    assert batch._bars == list(live.bars)
    assert batch._n() == len(live.bars)
    assert batch._closed_picture is not closed_picture
    assert batch._forming_bar is live.bars[0]


def test_drag_keeps_sequence_labels_visible(qtbot) -> None:
    widget = ChartWidget()
    qtbot.addWidget(widget)
    widget.resize(640, 400)
    widget.show()
    widget.set_frame_now(_frame())
    assert widget._seq_labels
    assert all(label.isVisible() for label in widget._seq_labels)

    widget._set_chart_dragging(True)

    assert all(label.isVisible() for label in widget._seq_labels)

    widget._set_chart_dragging(False)
    qtbot.waitUntil(lambda: all(label.isVisible() for label in widget._seq_labels))


def test_seq_label_toggle_shows_and_hides_label_items(qtbot) -> None:
    widget = ChartWidget()
    qtbot.addWidget(widget)
    widget.resize(640, 400)
    widget.show()
    widget.set_frame_now(_frame())
    assert widget._seq_labels_enabled is True
    assert widget._seq_labels
    assert all(label.isVisible() for label in widget._seq_labels)

    widget.set_seq_labels_enabled(False)

    assert widget._seq_labels_enabled is False
    assert all(not label.isVisible() for label in widget._seq_labels)

    widget.set_seq_labels_enabled(True)

    assert all(label.isVisible() for label in widget._seq_labels)



def test_chart_drag_hides_hover_and_defers_live_frame_render(qtbot) -> None:
    from PyQt6.QtCore import QPointF

    widget = ChartWidget()
    qtbot.addWidget(widget)
    widget.resize(640, 400)
    widget.show()
    first = _frame()
    second = _frame(close=11.0)
    rendered: list[KlineFrame] = []
    widget.frame_rendered.connect(rendered.append)
    widget.set_frame_now(first, fit_view=True)
    qtbot.wait(20)
    scene_pos = widget.getViewBox().mapViewToScene(QPointF(1.0, 1.0))
    widget._on_scene_mouse_moved(scene_pos)

    widget._set_chart_dragging(True)
    widget.set_frame_now(second)
    widget._on_scene_mouse_moved(scene_pos)

    assert not widget._hover_close_line.isVisible()
    assert not widget._hover_time_line.isVisible()
    assert rendered == [first]
    assert widget._dirty is True

    widget._set_chart_dragging(False)

    qtbot.waitUntil(lambda: rendered == [first, second])
    assert widget._hover_close_line.isVisible()
    assert widget._hover_time_line.isVisible()


def test_bottom_axis_uses_each_kline_open_time(qtbot) -> None:
    widget = ChartWidget()
    qtbot.addWidget(widget)
    widget.set_frame_now(_frame(), fit_view=True)

    widget._time_axis.set_bars(_frame().bars, "SH.600519", "15m")
    labels = widget._time_axis.tickStrings([0.0, 1.0], 1.0, 1.0)

    assert labels == ["08:03", "08:05"]


def test_hover_timestamp_format_follows_timeframe(qtbot) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    timestamp = datetime(
        2026, 7, 27, 10, 15, tzinfo=ZoneInfo("Asia/Shanghai")
    ).timestamp() * 1000
    bars = (_bar(1, timestamp),)
    widget = ChartWidget()
    qtbot.addWidget(widget)

    widget._time_axis.set_bars(bars, "SH.600519", "15m")
    assert widget._time_axis.format_timestamp(0) == "10:15"
    widget._time_axis.set_bars(bars, "SH.600519", "1d")
    assert widget._time_axis.format_timestamp(0) == "07-27"
    widget._time_axis.set_bars(bars, "SH.600519", "1w")
    assert widget._time_axis.format_timestamp(0) == "2026-07-27"
    widget._time_axis.set_bars(bars, "SH.600519", "1M")
    assert widget._time_axis.format_timestamp(0) == "2026-07-27"

    for timeframe in ("30m", "1h", "4h"):
        widget._time_axis.set_bars(bars, "SH.600519", timeframe)
        assert widget._time_axis.format_timestamp(0) == "07/27 10:15"


def test_chart_marks_market_day_boundary(qtbot) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    def timestamp_ms(day: int, hour: int, minute: int) -> float:
        value = datetime(2026, 7, day, hour, minute, tzinfo=ZoneInfo("Asia/Shanghai"))
        return value.timestamp() * 1000

    bars = (
        _bar(1, timestamp_ms(27, 10, 0), close=10.2),
        _bar(2, timestamp_ms(27, 9, 45), close=10.1),
        _bar(3, timestamp_ms(26, 15, 0), close=10.0),
    )
    frame = KlineFrame(
        symbol="SH.600519",
        timeframe="15m",
        bars=bars,
        indicators=IndicatorBundle(
            ema20=(1.5, 1.5, 1.5),
            atr14=(0.5, 0.5, 0.5),
        ),
        snapshot_ts_local_ms=int(timestamp_ms(27, 10, 1)),
    )
    widget = ChartWidget()
    qtbot.addWidget(widget)

    widget.set_frame_now(frame, fit_view=True)

    assert len(widget._day_boundary_lines) == 1
    assert widget._day_boundary_lines[0].value() == 0.5
    assert widget._date_axis._labels_by_x == {0.5: "07-26/07-27"}
    assert any(
        0.5 in values
        for _spacing, values in widget._date_axis.tickValues(-1.0, 3.0, 640.0)
    )
    assert widget._date_axis.tickStrings([0.5], 1.0, 1.0) == ["07-26/07-27"]


def test_day_boundary_label_is_owned_by_x_axis_when_view_range_changes(qtbot) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    def timestamp_ms(day: int, hour: int, minute: int) -> float:
        value = datetime(2026, 7, day, hour, minute, tzinfo=ZoneInfo("Asia/Shanghai"))
        return value.timestamp() * 1000

    bars = (
        _bar(1, timestamp_ms(27, 10, 0)),
        _bar(2, timestamp_ms(26, 15, 0)),
    )
    frame = KlineFrame(
        symbol="SH.600519",
        timeframe="15m",
        bars=bars,
        indicators=IndicatorBundle(ema20=(1.5, 1.5), atr14=(0.5, 0.5)),
        snapshot_ts_local_ms=int(timestamp_ms(27, 10, 1)),
    )
    widget = ChartWidget()
    qtbot.addWidget(widget)
    widget.set_frame_now(frame, fit_view=True)
    boundaries = widget._date_axis._labels_by_x.copy()

    widget.getViewBox().setRange(yRange=(0.0, 1.0), padding=0)

    assert widget._date_axis._labels_by_x == boundaries
    assert widget._date_axis.tickStrings([0.5], 1.0, 1.0) == ["07-26/07-27"]


def test_larger_intraday_charts_hide_day_boundary_labels(qtbot) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    def timestamp_ms(day: int) -> float:
        value = datetime(2026, 7, day, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        return value.timestamp() * 1000

    bars = tuple(_bar(index + 1, timestamp_ms(27 - index)) for index in range(5))
    for timeframe in ("30m", "1h", "4h"):
        frame = KlineFrame(
            symbol="SH.600519",
            timeframe=timeframe,
            bars=bars,
            indicators=IndicatorBundle(ema20=(1.5,) * 5, atr14=(0.5,) * 5),
            snapshot_ts_local_ms=int(timestamp_ms(27)),
        )
        widget = ChartWidget()
        qtbot.addWidget(widget)
        widget.set_frame_now(frame, fit_view=True)

        assert len(widget._day_boundary_lines) == 4
        assert widget._date_axis._labels_by_x == {}


def test_shorter_intraday_charts_keep_all_day_boundary_labels(qtbot) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    def timestamp_ms(day: int) -> float:
        value = datetime(2026, 7, day, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        return value.timestamp() * 1000

    bars = tuple(_bar(index + 1, timestamp_ms(27 - index)) for index in range(5))
    frame = KlineFrame(
        symbol="SH.600519",
        timeframe="15m",
        bars=bars,
        indicators=IndicatorBundle(ema20=(1.5,) * 5, atr14=(0.5,) * 5),
        snapshot_ts_local_ms=int(timestamp_ms(27)),
    )
    widget = ChartWidget()
    qtbot.addWidget(widget)
    widget.set_frame_now(frame, fit_view=True)

    assert len(widget._day_boundary_lines) == 4
    assert set(widget._date_axis._labels_by_x) == {0.5, 1.5, 2.5, 3.5}


def test_day_boundary_labels_are_thinned_again_when_zoomed_out(qtbot) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    def timestamp_ms(day: int) -> float:
        value = datetime(2026, 7, day, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        return value.timestamp() * 1000

    bars = tuple(_bar(index + 1, timestamp_ms(27 - index)) for index in range(10))
    frame = KlineFrame(
        symbol="SH.600519",
        timeframe="15m",
        bars=bars,
        indicators=IndicatorBundle(ema20=(1.5,) * 10, atr14=(0.5,) * 10),
        snapshot_ts_local_ms=int(timestamp_ms(27)),
    )
    widget = ChartWidget()
    qtbot.addWidget(widget)
    widget.set_frame_now(frame, fit_view=True)

    all_values = widget._date_axis.tickValues(0.0, 9.0, 900.0)[0][1]
    zoomed_out_values = widget._date_axis.tickValues(0.0, 9.0, 300.0)[0][1]

    assert len(zoomed_out_values) < len(all_values)
    assert all(
        (right - left) / 9.0 * 300.0 >= 96.0
        for left, right in zip(zoomed_out_values, zoomed_out_values[1:])
    )


def test_daily_and_weekly_charts_hide_day_boundary_markers(qtbot) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    def timestamp_ms(day: int) -> float:
        value = datetime(2026, 7, day, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        return value.timestamp() * 1000

    bars = (
        _bar(1, timestamp_ms(27), close=10.2),
        _bar(2, timestamp_ms(26), close=10.1),
    )
    widget = ChartWidget()
    qtbot.addWidget(widget)
    for timeframe in ("1d", "1w"):
        frame = KlineFrame(
            symbol="SH.600519",
            timeframe=timeframe,
            bars=bars,
            indicators=IndicatorBundle(ema20=(1.5, 1.5), atr14=(0.5, 0.5)),
            snapshot_ts_local_ms=int(timestamp_ms(27)),
        )
        widget.set_frame_now(frame)
        assert widget._day_boundary_lines == []
        assert widget._date_axis._labels_by_x == {}
