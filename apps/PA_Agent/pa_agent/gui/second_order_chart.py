"""Chart overlays owned exclusively by the embedded SecondOrderGame page."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QLabel, QSizePolicy

from pa_agent.gui.chart_widget import ChartWidget


_NASH_UPPER = "#79B9F2"
_NASH_LOWER = "#71D49A"
_NASH_CENTER = "#D3A967"
_CONTRARIAN = "#F0A33A"
_LIQUIDITY = "#F06464"
_SMART_MONEY = "#4B9FE8"
_TEXT = "#F4F7FA"


class SecondOrderGameChart(ChartWidget):
    """K-line chart with chart-only game-signal history and no EMA overlay."""

    _candle_vertical_scale = 0.50
    _fit_visible_bars = 35

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("secondOrderGameChart")
        self.set_moving_average_overlays(ema_enabled=False, ma_periods=())
        self._game_signal_series: tuple[Mapping[str, Any], ...] = ()
        self._nash_lines: dict[str, pg.PlotDataItem] = {}
        self._game_marker_items: list[Any] = []

    def set_game_signal_series(self, points: Sequence[Mapping[str, Any]]) -> None:
        """Set the oldest-first chart series returned by SecondOrderGame."""
        self._game_signal_series = tuple(
            point for point in points if isinstance(point, Mapping)
        )
        self._render_game_overlays()

    def game_signal_series(self) -> tuple[Mapping[str, Any], ...]:
        return self._game_signal_series

    def reset(self) -> None:
        self._game_signal_series = ()
        self._clear_game_overlays()
        super().reset()

    def _render_frame(self, frame) -> None:
        super()._render_frame(frame)
        self._render_game_overlays()

    def _view_ranges_for_frame(self, frame):
        x_range, y_range = super()._view_ranges_for_frame(frame)
        bounds = self._game_overlay_price_bounds(frame, x_range=x_range)
        if bounds is None:
            return x_range, y_range
        lower, upper = bounds
        span = max(upper - lower, abs(upper) * 0.01, 1e-8)
        return x_range, (
            min(y_range[0], lower - span * 0.06),
            max(y_range[1], upper + span * 0.06),
        )

    def _render_game_overlays(self) -> None:
        self._clear_game_overlays()
        frame = self.displayed_frame()
        if frame is None or not frame.bars or not self._game_signal_series:
            return

        aligned = self._aligned_points(len(frame.bars))
        if not aligned:
            return
        x_values: list[float] = []
        center_values: list[float] = []
        upper_values: list[float] = []
        lower_values: list[float] = []
        buy_points: list[tuple[float, float]] = []
        sell_points: list[tuple[float, float]] = []
        trap_points: list[tuple[float, float]] = []
        smart_points: list[tuple[float, float]] = []

        for x_pos, point in aligned:
            signal = point.get("signal")
            if point.get("status") != "available" or not isinstance(signal, Mapping):
                x_values.append(float(x_pos))
                center_values.append(math.nan)
                upper_values.append(math.nan)
                lower_values.append(math.nan)
                continue
            nash = signal.get("nash")
            nash = nash if isinstance(nash, Mapping) else {}
            x_values.append(float(x_pos))
            center_values.append(_finite_or_nan(nash.get("center")))
            upper_values.append(_finite_or_nan(nash.get("upper")))
            lower_values.append(_finite_or_nan(nash.get("lower")))

            bar = frame.bars[len(frame.bars) - 1 - x_pos]
            gap = max(float(bar.high) - float(bar.low), abs(float(bar.close)) * 0.003, 1e-8)
            above_level = float(bar.close) + gap * 0.60
            below_level = float(bar.close) - gap * 0.60
            above_stack = 0
            below_stack = 0

            features = signal.get("features")
            features = features if isinstance(features, Mapping) else {}
            if bool(features.get("contrarian_buy")):
                buy_points.append((float(x_pos), below_level))
                below_stack += 1
            if bool(features.get("contrarian_sell")):
                sell_points.append((float(x_pos), above_level))
                above_stack += 1

            liquidity = signal.get("liquidity_trap")
            liquidity = liquidity if isinstance(liquidity, Mapping) else {}
            if bool(liquidity.get("lower")):
                trap_points.append(
                    (float(x_pos), below_level - below_stack * gap * 0.45)
                )
                below_stack += 1
            if bool(liquidity.get("upper")):
                trap_points.append(
                    (float(x_pos), above_level + above_stack * gap * 0.45)
                )
                above_stack += 1

            smart_money = signal.get("smart_money")
            smart_money = smart_money if isinstance(smart_money, Mapping) else {}
            if bool(smart_money.get("positive")):
                smart_points.append(
                    (float(x_pos), below_level - below_stack * gap * 0.50)
                )

        self._add_nash_line("center", x_values, center_values, _NASH_CENTER)
        self._add_nash_line("upper", x_values, upper_values, _NASH_UPPER)
        self._add_nash_line("lower", x_values, lower_values, _NASH_LOWER)
        self._add_scatter(buy_points, symbol="t", color=_CONTRARIAN, size=13)
        self._add_scatter(sell_points, symbol="t1", color=_CONTRARIAN, size=13)
        self._add_scatter(trap_points, symbol="x", color=_LIQUIDITY, size=11)
        self._add_smart_money_markers(smart_points)

    def _aligned_points(
        self, bar_count: int
    ) -> list[tuple[int, Mapping[str, Any]]]:
        points = self._game_signal_series[-bar_count:]
        start_x = bar_count - len(points)
        return [(start_x + index, point) for index, point in enumerate(points)]

    def _add_nash_line(
        self,
        name: str,
        x_values: Sequence[float],
        y_values: Sequence[float],
        color: str,
    ) -> None:
        if not any(math.isfinite(value) for value in y_values):
            return
        pen = pg.mkPen(color=color, width=1.25, style=Qt.PenStyle.DashLine)
        line = pg.PlotDataItem(
            x=np.asarray(x_values, dtype=float),
            y=np.asarray(y_values, dtype=float),
            pen=pen,
            connect="finite",
        )
        line.setZValue(4)
        self.addItem(line)
        self._nash_lines[name] = line

    def _add_scatter(
        self,
        points: Sequence[tuple[float, float]],
        *,
        symbol: str,
        color: str,
        size: int,
    ) -> None:
        if not points:
            return
        item = pg.ScatterPlotItem(
            x=[point[0] for point in points],
            y=[point[1] for point in points],
            symbol=symbol,
            size=size,
            pen=pg.mkPen(color=color, width=1.8),
            brush=pg.mkBrush(0, 0, 0, 0),
            pxMode=True,
        )
        item.setZValue(12)
        self.addItem(item)
        self._game_marker_items.append(item)

    def _add_smart_money_markers(
        self, points: Sequence[tuple[float, float]]
    ) -> None:
        if not points:
            return
        circles = pg.ScatterPlotItem(
            x=[point[0] for point in points],
            y=[point[1] for point in points],
            symbol="o",
            size=18,
            pen=pg.mkPen(color=_SMART_MONEY, width=1.8),
            brush=pg.mkBrush(9, 16, 24, 230),
            pxMode=True,
        )
        circles.setZValue(12)
        self.addItem(circles)
        self._game_marker_items.append(circles)
        for x_pos, y_pos in points:
            label = pg.TextItem(
                text="聪",
                color=_TEXT,
                anchor=(0.5, 0.5),
                border=None,
                fill=None,
            )
            label.setFont(QFont("Microsoft YaHei UI", 7, QFont.Weight.DemiBold))
            label.setPos(x_pos, y_pos)
            label.setZValue(13)
            self.addItem(label)
            self._game_marker_items.append(label)

    def _clear_game_overlays(self) -> None:
        for line in self._nash_lines.values():
            self.removeItem(line)
        self._nash_lines.clear()
        for item in self._game_marker_items:
            self.removeItem(item)
        self._game_marker_items.clear()

    def _game_overlay_price_bounds(
        self,
        frame,
        *,
        x_range: tuple[float, float] | None = None,
    ) -> tuple[float, float] | None:
        values: list[float] = []
        for x_pos, point in self._aligned_points(len(frame.bars)):
            if x_range is not None and not x_range[0] <= x_pos <= x_range[1]:
                continue
            signal = point.get("signal")
            if point.get("status") != "available" or not isinstance(signal, Mapping):
                continue
            nash = signal.get("nash")
            if not isinstance(nash, Mapping):
                continue
            for key in ("upper", "lower"):
                value = _finite_or_nan(nash.get(key))
                if math.isfinite(value):
                    values.append(value)
        return (min(values), max(values)) if values else None


def _finite_or_nan(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return math.nan
    return number if math.isfinite(number) else math.nan


def create_second_order_chart_legend(parent=None) -> QLabel:
    """Build the chart key outside the plotting surface."""
    legend = QLabel(parent)
    legend.setObjectName("secondOrderChartLegend")
    legend.setTextFormat(Qt.TextFormat.RichText)
    legend.setWordWrap(True)
    legend.setText(
        '<span style="color:#AEB8C5">纳什均衡带（16根 K 线）：</span>'
        '<span style="color:#79B9F2">蓝虚线上界</span>'
        '<span style="color:#778391"> · </span>'
        '<span style="color:#71D49A">绿虚线下界</span>'
        '<span style="color:#778391"> · </span>'
        '<span style="color:#D3A967">金橙色中枢线</span>'
        '<span style="color:#778391">　|　</span>'
        '<span style="color:#AEB8C5">标记：</span>'
        '<span style="color:#F0A33A">△ 逆势机会</span>'
        '<span style="color:#778391"> · </span>'
        '<span style="color:#F06464">× 流动性陷阱</span>'
        '<span style="color:#778391"> · </span>'
        '<span style="color:#4B9FE8">○</span>'
        '<span style="color:#F4F7FA">聪</span>'
        '<span style="color:#AEB8C5"> 聪明钱活跃</span>'
    )
    legend.setStyleSheet(
        "QLabel#secondOrderChartLegend {"
        "color: #AEB8C5;"
        "background: #0C1016;"
        "border: 1px solid #4C5765;"
        "padding: 5px 8px;"
        "font-family: 'Microsoft YaHei UI';"
        "font-size: 11px;"
        "}"
    )
    legend.setFont(QFont("Microsoft YaHei UI", 8))
    legend.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    legend.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    return legend


__all__ = ["SecondOrderGameChart", "create_second_order_chart_legend"]
