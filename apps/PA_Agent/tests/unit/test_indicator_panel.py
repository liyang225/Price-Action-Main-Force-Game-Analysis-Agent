from __future__ import annotations

import math

import pytest

pytest.importorskip("PyQt6")
pytest.importorskip("pyqtgraph")

from pa_agent.gui.theme import tokens as T


def _frame(n: int = 12):
    from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame

    bars = tuple(
        KlineBar(
            seq=index + 1,
            ts_open=1_700_000_000_000 - index * 60_000,
            open=100.0 + index,
            high=102.0 + index,
            low=99.0 + index,
            close=101.0 + index,
            volume=100_000_000.0 + index * 1_000_000,
            amount=500_000_000.0 + index * 2_000_000,
        )
        for index in range(n)
    )
    return KlineFrame(
        symbol="SH.600519",
        timeframe="15m",
        bars=bars,
        indicators=IndicatorBundle(
            ema20=tuple(100.5 + index for index in range(n)),
            atr14=tuple(1.0 for _ in range(n)),
        ),
        snapshot_ts_local_ms=1_700_000_000_000,
    )


def test_sma_is_aligned_to_newest_first_input() -> None:
    from pa_agent.gui.widgets.indicator_panel import sma_newest_first

    values = sma_newest_first((5.0, 4.0, 3.0, 2.0, 1.0), 3)

    assert values[:3] == pytest.approx((4.0, 3.0, 2.0))
    assert math.isnan(values[3])
    assert math.isnan(values[4])


def test_volume_and_turnover_display_scales_do_not_change_ma_inputs() -> None:
    from pa_agent.gui.widgets.indicator_panel import _compact_turnover, _compact_volume, sma_newest_first

    values = (292_620_000.0, 300_000_000.0, 280_000_000.0, 290_000_000.0, 300_000_000.0)
    assert sma_newest_first(values, 5)[0] == pytest.approx(sum(values) / 5)
    assert _compact_volume(292_620_000) == "292.62万"
    assert _compact_turnover(328_123_456) == "3.281亿"


def test_kdj_and_macd_constant_series_are_neutral() -> None:
    from pa_agent.data.base import KlineBar
    from pa_agent.gui.widgets.indicator_panel import kdj_newest_first, macd_newest_first

    bars = tuple(
        KlineBar(index + 1, float(index), 10.0, 10.0, 10.0, 10.0, 1.0)
        for index in range(20)
    )
    k_values, d_values, j_values = kdj_newest_first(bars)
    dif, dea, histogram = macd_newest_first(tuple(10.0 for _ in bars))

    assert k_values == pytest.approx(tuple(50.0 for _ in bars))
    assert d_values == pytest.approx(tuple(50.0 for _ in bars))
    assert j_values == pytest.approx(tuple(50.0 for _ in bars))
    assert dif == pytest.approx(tuple(0.0 for _ in bars))
    assert dea == pytest.approx(tuple(0.0 for _ in bars))
    assert histogram == pytest.approx(tuple(0.0 for _ in bars))


def test_moving_average_toolbar_drives_chart_overlay(qtbot) -> None:
    from PyQt6.QtCore import Qt

    from pa_agent.gui.chart_widget import ChartWidget
    from pa_agent.gui.widgets.indicator_panel import MovingAverageToolbar

    chart = ChartWidget()
    toolbar = MovingAverageToolbar()
    qtbot.addWidget(chart)
    qtbot.addWidget(toolbar)
    toolbar.overlay_changed.connect(chart.set_moving_average_overlays)
    toolbar.seq_labels_changed.connect(chart.set_seq_labels_enabled)
    toolbar._ma_actions[5].setChecked(True)
    chart.set_frame_now(_frame())

    assert toolbar.selected_periods() == (5,)
    assert toolbar.ma_button.text() == "MA"
    assert toolbar.ma_button.font().pointSize() == 8
    assert toolbar.ma_button.arrowType() == Qt.ArrowType.NoArrow
    assert not hasattr(toolbar, "ema_button")
    assert not hasattr(toolbar, "seq_button")
    from PyQt6.QtGui import QFontMetrics

    assert toolbar.ma_button.width() >= (
        QFontMetrics(toolbar.ma_button.font()).horizontalAdvance("MA") + 22
    )
    assert toolbar.ma_button.height() >= 22
    assert set(chart._moving_average_lines) == {"EMA20", "MA5"}
    assert chart._seq_labels_enabled is True


def test_volume_ma_settings_use_editable_slots_and_allow_removal(qtbot) -> None:
    from pa_agent.gui.widgets.indicator_panel import IndicatorSettingsDialog

    dialog = IndicatorSettingsDialog(
        {
            "kdj_period": 9,
            "k_smoothing": 3,
            "d_smoothing": 3,
            "macd_fast": 12,
            "macd_slow": 26,
            "macd_signal": 9,
            "volume_ma_periods": (5, 10, 13),
        }
    )
    qtbot.addWidget(dialog)
    assert [edit.text() for edit in dialog.volume_ma_period_edits] == ["5", "10", "13", "", ""]
    dialog.volume_ma_period_edits[0].setText("3")
    dialog.volume_ma_period_edits[1].clear()
    dialog.volume_ma_period_edits[2].setText("135")

    assert dialog.values()["volume_ma_periods"] == (3, 135)


def test_indicator_panel_defaults_to_volume_and_shares_x_axis(qtbot, qapp) -> None:
    from pa_agent.gui.chart_widget import ChartWidget
    from pa_agent.gui.widgets.indicator_panel import IndicatorPanel

    chart = ChartWidget()
    panel = IndicatorPanel(chart)
    qtbot.addWidget(chart)
    qtbot.addWidget(panel)
    chart.frame_rendered.connect(panel.set_frame)
    chart.resize(900, 500)
    panel.resize(900, 194)
    chart.show()
    panel.show()
    chart.set_frame_now(_frame(), fit_view=True)
    chart.getViewBox().setXRange(2.0, 8.0, padding=0)
    qapp.processEvents()

    assert panel.height() == 194
    assert panel.plot.height() == 160
    assert panel.settings_button.parentWidget() is panel.indicator_selector_card
    assert panel.indicator_combo.currentText() == "成交量"
    stats_text = panel.stats_label.text()
    assert stats_text.startswith("成交量 100万 · ")
    assert '<span style="color:#5B8CC9;">MA5</span> 102万' in stats_text
    assert '<span style="color:#B8933E;">MA10</span> 104.5万' in stats_text
    assert panel.plot.getViewBox().viewRange()[0] == pytest.approx((2.0, 8.0))
    assert panel.plot.getPlotItem().getAxis("left").width() == chart.getPlotItem().getAxis(
        "left"
    ).width()
    assert not panel.plot.getPlotItem().getAxis("right").isVisible()
    assert not chart.getPlotItem().getAxis("right").isVisible()
    bottom_axis = panel.plot.getPlotItem().getAxis("bottom")
    assert bottom_axis.tickStrings([2.0, 3.0], 1.0, 1.0) == ["", ""]

    panel.indicator_combo.setCurrentText("KDJ")
    stats_text = panel.stats_label.text()
    assert f"color:{T.ACCENT}" in stats_text
    assert f"color:{T.CHART_LINE}" in stats_text
    assert f"color:{T.CHART_LINE_3}" in stats_text
    panel._show_linked_hover(3)
    hover_stats_text = panel.stats_label.text()
    assert f"color:{T.ACCENT}" in hover_stats_text
    assert f"color:{T.CHART_LINE}" in hover_stats_text
    assert f"color:{T.CHART_LINE_3}" in hover_stats_text


def test_indicator_panel_syncs_x_axis_while_main_chart_drags(qtbot, qapp) -> None:
    from pa_agent.gui.chart_widget import ChartWidget
    from pa_agent.gui.widgets.indicator_panel import IndicatorPanel

    chart = ChartWidget()
    panel = IndicatorPanel(chart)
    qtbot.addWidget(chart)
    qtbot.addWidget(panel)
    chart.set_frame_now(_frame(), fit_view=True)
    chart.getViewBox().setXRange(0.0, 1.0, padding=0)
    qapp.processEvents()

    chart._set_chart_dragging(True)
    chart.getViewBox().setXRange(3.0, 4.0, padding=0)
    qapp.processEvents()

    assert panel.plot.getViewBox().viewRange()[0] == pytest.approx((3.0, 4.0))

    chart._set_chart_dragging(False)
    qapp.processEvents()

    assert panel.plot.getViewBox().viewRange()[0] == pytest.approx((3.0, 4.0))


def test_indicator_panel_reuses_items_for_live_frame_updates(qtbot) -> None:
    from dataclasses import replace

    from pa_agent.gui.chart_widget import ChartWidget
    from pa_agent.gui.widgets.indicator_panel import IndicatorPanel

    chart = ChartWidget()
    panel = IndicatorPanel(chart)
    qtbot.addWidget(chart)
    qtbot.addWidget(panel)
    first = _frame()
    panel.set_frame(first)
    first_up_bars = panel._data_items["up_bars"]
    first_hover_line = panel._hover_line
    second = replace(
        first,
        bars=(replace(first.bars[0], close=102.0), *first.bars[1:]),
    )

    panel.set_frame(second)

    assert panel._data_items["up_bars"] is first_up_bars
    assert panel._hover_line is first_hover_line


def test_main_and_indicator_hover_share_only_the_vertical_guide(qtbot, qapp) -> None:
    from PyQt6.QtCore import QPointF

    from pa_agent.gui.chart_widget import ChartWidget
    from pa_agent.gui.widgets.indicator_panel import IndicatorPanel

    chart = ChartWidget()
    panel = IndicatorPanel(chart)
    qtbot.addWidget(chart)
    qtbot.addWidget(panel)
    chart.resize(900, 500)
    panel.resize(900, 230)
    chart.show()
    panel.show()
    frame = _frame()
    chart.set_frame_now(frame, fit_view=True)
    panel.set_frame(frame)
    qapp.processEvents()

    main_scene_pos = chart.getViewBox().mapViewToScene(QPointF(3.0, 101.0))
    chart._on_scene_mouse_moved(main_scene_pos)

    assert chart._hover_close_line.isVisible()
    assert chart._hover_time_line.isVisible()
    assert panel._hover_line is not None and panel._hover_line.isVisible()
    assert panel._hover_line.value() == 3.0
    assert not hasattr(panel, "_hover_label")

    panel._show_hover(4, propagate=True)

    assert chart._hover_time_line.isVisible()
    assert chart._hover_time_line.value() == 4.0
    assert not chart._hover_close_line.isVisible()
    assert not chart._hover_price_label.isVisible()
    assert panel._hover_line is not None and panel._hover_line.isVisible()
    assert panel._hover_x_index == 4

    panel.set_frame(frame)

    assert panel._hover_line is not None and panel._hover_line.isVisible()
    assert panel._hover_line.value() == 4.0
    assert chart._hover_time_line.isVisible()
    assert chart._hover_time_line.value() == 4.0


def test_zero_volume_is_reported_as_unavailable(qtbot) -> None:
    from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame
    from pa_agent.gui.chart_widget import ChartWidget
    from pa_agent.gui.widgets.indicator_panel import IndicatorPanel

    bars = tuple(
        KlineBar(
            seq=index + 1,
            ts_open=1_700_000_000_000 - index * 60_000,
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=0.0,
        )
        for index in range(6)
    )
    frame = KlineFrame(
        symbol="TVC.GOLD",
        timeframe="15m",
        bars=bars,
        indicators=IndicatorBundle(
            ema20=tuple(100.0 for _ in bars),
            atr14=tuple(1.0 for _ in bars),
        ),
        snapshot_ts_local_ms=1_700_000_000_000,
    )
    chart = ChartWidget()
    panel = IndicatorPanel(chart)
    qtbot.addWidget(chart)
    qtbot.addWidget(panel)

    panel.set_frame(frame)

    assert panel.stats_label.text() == "成交量：数据源未提供"
    assert panel._hover_details[0][0] == "成交量：数据源未提供"
