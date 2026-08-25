"""ChartWidget — pyqtgraph-based K-line chart with EMA20 and overlay lines.

Tasks 14.2 + 14.5:
  - Renders N candles, EMA20 line, and sequence-number labels.
  - Draws entry/TP/SL horizontal lines when order_type != "不下单".
  - 30 Hz QTimer throttles redraws so the 1 Hz data thread never blocks the UI.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import TYPE_CHECKING, Sequence
from zoneinfo import ZoneInfo

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QEvent, Qt, QTimer, pyqtSignal

from pa_agent.gui.widgets.candle_item import BatchedCandleItem, CandleItem
from pa_agent.gui.widgets.indicator_panel import sma_newest_first
from pa_agent.gui.theme import tokens as T
from pa_agent.gui.widgets.overlay_lines import OverlayLines
from pa_agent.gui.widgets.seq_label_item import SeqLabelItem
from pa_agent.util.trade_metrics import is_long_direction

if TYPE_CHECKING:
    from pa_agent.data.base import KlineFrame

# ── Constants ─────────────────────────────────────────────────────────────────

_TIMER_INTERVAL_MS = 33  # ~30 Hz
_EMA_COLOR = (184, 147, 62)  # muted ochre (tokens.CHART_LINE)
_NO_ORDER_TEXT = "不下单"
_X_MARGIN_BARS = 0.65
_Y_PADDING_RATIO = 0.07
_Y_TOP_EXTRA_RATIO = 0.04
# Technical-analysis K-lines use their full requested vertical scale.
# SecondOrderGame overrides this value independently.
_CANDLE_VERTICAL_SCALE = 0.75
_FIT_VISIBLE_BARS = 26
_AXIS_RESIZE_MIN_WIDTH = 40
_AXIS_RESIZE_EDGE_PX = 8
_DAY_LABEL_MIN_SPACING_PX = 96.0
_HOVER_PRICE_COLOR = T.FG
_HOVER_RISE_COLOR = T.MKT_UP
_HOVER_FALL_COLOR = T.MKT_DOWN
_HOVER_FLAT_COLOR = T.FG_2
_CHART_PANEL_BG = (12, 14, 17)  # tokens.BG as RGB for pg brushes


def _market_datetime(timestamp: float, symbol: str) -> datetime:
    seconds = float(timestamp)
    if seconds >= 1e10:
        seconds /= 1000.0
    market = str(symbol or "").strip().upper().split(".", 1)[0]
    if market == "US":
        return datetime.fromtimestamp(seconds, ZoneInfo("America/New_York"))
    if market in {"SH", "SZ", "BJ", "HK"}:
        return datetime.fromtimestamp(seconds, ZoneInfo("Asia/Shanghai"))
    return datetime.fromtimestamp(seconds)


def _is_intraday_timeframe(timeframe: str) -> bool:
    value = str(timeframe or "").strip()
    if value.endswith("M"):
        return False
    return value.lower().endswith(("m", "h"))


def _timeframe_timestamp_format(timeframe: str) -> str:
    value = str(timeframe or "").strip()
    lowered = value.lower()
    if lowered == "1d":
        return "%m-%d"
    if not value.endswith("M") and lowered in {"30m", "1h", "4h"}:
        return "%m/%d %H:%M"
    if value.endswith("M") or lowered.endswith(("w", "mo", "mon", "month", "y")):
        return "%Y-%m-%d"
    return "%H:%M"


def _show_day_boundary_label(timeframe: str, boundary_index: int) -> bool:
    """Hide date-boundary text when the bottom axis already carries the date."""
    if str(timeframe or "").strip().lower() not in {"30m", "1h", "4h"}:
        return True
    return False


class _KlineTimeAxis(pg.AxisItem):
    """Bottom axis that maps each visible candle position to its open time."""

    def __init__(self) -> None:
        super().__init__(orientation="bottom")
        self._timestamps_by_x: dict[int, float] = {}
        self._labels_by_x: dict[int, str] = {}
        self._timestamp_labels: dict[float, str] = {}
        self._symbol = ""
        self._timeframe = ""

    def set_bars(self, bars, symbol: str, timeframe: str = "") -> None:
        count = len(bars)
        next_symbol = str(symbol or "")
        next_timeframe = str(timeframe or "")
        if (next_symbol, next_timeframe) != (self._symbol, self._timeframe):
            self._timestamp_labels.clear()
        self._symbol = next_symbol
        self._timeframe = next_timeframe
        self._timestamps_by_x = {
            count - 1 - index: float(bar.ts_open)
            for index, bar in enumerate(bars)
        }
        self._labels_by_x = {
            x_pos: self._label_for_timestamp(timestamp)
            for x_pos, timestamp in self._timestamps_by_x.items()
        }
        self.picture = None
        self.update()

    def clear_bars(self) -> None:
        self._timestamps_by_x.clear()
        self._labels_by_x.clear()
        self._timestamp_labels.clear()
        self._symbol = ""
        self._timeframe = ""
        self.picture = None
        self.update()

    def tickStrings(self, values, scale, spacing):  # noqa: N802
        labels: list[str] = []
        for value in values:
            x_pos = int(round(float(value)))
            if x_pos not in self._labels_by_x or not math.isclose(
                float(value), x_pos, abs_tol=1e-6
            ):
                labels.append("")
            else:
                labels.append(self._labels_by_x[x_pos])
        return labels

    def _label_for_timestamp(self, timestamp: float) -> str:
        label = self._timestamp_labels.get(timestamp)
        if label is None:
            label = _market_datetime(timestamp, self._symbol).strftime(
                _timeframe_timestamp_format(self._timeframe)
            )
            self._timestamp_labels[timestamp] = label
        return label

    def datetime_at(self, x_pos: int) -> datetime | None:
        timestamp = self._timestamps_by_x.get(int(x_pos))
        if timestamp is None:
            return None
        return _market_datetime(timestamp, self._symbol)

    def format_timestamp(self, x_pos: int) -> str:
        value = self.datetime_at(x_pos)
        if value is None:
            return "--"
        return value.strftime(_timeframe_timestamp_format(self._timeframe))


class _KlineDateAxis(pg.AxisItem):
    """Top-oriented axis strip for intraday market-day boundary labels."""

    def __init__(self) -> None:
        super().__init__(orientation="top")
        self._labels_by_x: dict[float, str] = {}
        self.setStyle(tickLength=5, tickTextOffset=3)

    def set_boundaries(self, labels_by_x: dict[float, str]) -> None:
        self._labels_by_x = dict(labels_by_x)
        self.picture = None
        self.update()

    def clear_boundaries(self) -> None:
        self.set_boundaries({})

    def tickValues(self, min_val, max_val, size):  # noqa: N802
        values = sorted(
            x_pos
            for x_pos in self._labels_by_x
            if min_val <= x_pos <= max_val
        )
        if len(values) <= 1:
            return [(1.0, values)] if values else []

        try:
            span = float(max_val) - float(min_val)
            pixel_width = float(size)
        except (TypeError, ValueError):
            return [(1.0, values)]
        if (
            not math.isfinite(span)
            or not math.isfinite(pixel_width)
            or span <= 0
            or pixel_width <= 0
        ):
            return [(1.0, values)]

        # Select labels in screen space so zooming out cannot make their text overlap.
        min_x_spacing = span * _DAY_LABEL_MIN_SPACING_PX / pixel_width
        selected = [values[0]]
        for value in values[1:]:
            if value - selected[-1] >= min_x_spacing:
                selected.append(value)
        values = selected
        return [(1.0, values)] if values else []

    def tickStrings(self, values, scale, spacing):  # noqa: N802
        return [self._labels_by_x.get(float(value), "") for value in values]


class ChartWidget(pg.PlotWidget):
    """Interactive K-line chart widget.

    Parameters
    ----------
    parent:
        Optional Qt parent widget.
    """

    frame_rendered = pyqtSignal(object)
    axis_width_changed = pyqtSignal(int)
    hover_x_changed = pyqtSignal(object)
    chart_dragging_changed = pyqtSignal(bool)
    _candle_vertical_scale = _CANDLE_VERTICAL_SCALE
    _fit_visible_bars = _FIT_VISIBLE_BARS

    def __init__(self, parent=None) -> None:
        self._time_axis = _KlineTimeAxis()
        self._date_axis = _KlineDateAxis()
        super().__init__(parent=parent, axisItems={"bottom": self._time_axis})

        plot_item = self.getPlotItem()
        bottom_axis = plot_item.getAxis("bottom")
        plot_item.layout.removeItem(bottom_axis)
        self._date_axis.linkToView(plot_item.vb)
        self._date_axis.setHeight(26)
        plot_item.layout.addItem(self._date_axis, 3, 1)
        plot_item.layout.addItem(bottom_axis, 4, 1)

        # Configure plot appearance — grid stays a whisper under the data
        self.setBackground(T.CHART_BG)
        self.showGrid(x=False, y=True, alpha=0.22)
        self.getPlotItem().setLabel("left", "Price")
        self.getPlotItem().getAxis("left").setWidth(64)

        # Internal state
        self._latest_frame: KlineFrame | None = None
        self._dirty: bool = False
        self._candle_items: list[CandleItem] = []  # kept for compatibility; use _batched_candle_item
        self._batched_candle_item: BatchedCandleItem | None = None
        self._closed_signature: tuple | None = None
        self._closed_count: int = 0
        self._last_forming_signature: tuple | None = None
        self._seq_labels: list[SeqLabelItem] = []
        self._seq_labels_enabled: bool = True
        self._ema_line: pg.PlotDataItem | None = None
        self._moving_average_lines: dict[str, pg.PlotDataItem] = {}
        self._ema_enabled = True
        self._ma_periods: tuple[int, ...] = ()
        self._overlay = OverlayLines()
        self._hover_close_line = pg.InfiniteLine(
            angle=0,
            movable=False,
            pen=pg.mkPen(color=T.CHART_CROSSHAIR, width=1,
                         style=pg.QtCore.Qt.PenStyle.DashLine),
        )
        self._hover_close_line.setZValue(100)
        self._hover_close_line.hide()
        self.addItem(self._hover_close_line)
        self._hover_time_line = pg.InfiniteLine(
            angle=90,
            movable=False,
            pen=pg.mkPen(
                color=T.CHART_CROSSHAIR,
                width=1,
                style=pg.QtCore.Qt.PenStyle.DashLine,
            ),
        )
        self._hover_time_line.setZValue(100)
        self._hover_time_line.hide()
        self.addItem(self._hover_time_line)
        hover_label_fill = pg.mkBrush(*_CHART_PANEL_BG, 235)
        hover_label_border = pg.mkPen(T.CHART_CROSSHAIR)
        self._hover_price_label = pg.TextItem(
            text="",
            color=_HOVER_PRICE_COLOR,
            anchor=(0.0, 0.5),
            fill=hover_label_fill,
            border=hover_label_border,
        )
        self._hover_price_label.setZValue(101)
        self._hover_price_label.hide()
        self.addItem(self._hover_price_label, ignoreBounds=True)
        self._hover_change_label = pg.TextItem(
            text="",
            color=_HOVER_FLAT_COLOR,
            anchor=(1.0, 0.5),
            fill=hover_label_fill,
            border=hover_label_border,
        )
        self._hover_change_label.setZValue(101)
        self._hover_change_label.hide()
        self.addItem(self._hover_change_label, ignoreBounds=True)
        self._hover_time_label = pg.TextItem(
            text="",
            color=_HOVER_PRICE_COLOR,
            anchor=(0.5, 1.0),
            fill=hover_label_fill,
            border=hover_label_border,
        )
        self._hover_time_label.setZValue(101)
        self._hover_time_label.hide()
        self.addItem(self._hover_time_label, ignoreBounds=True)
        self._hover_close_price: float | None = None
        self._hover_x_index: int | None = None
        self._hover_scene_pos = None
        self._day_boundary_lines: list[pg.InfiniteLine] = []
        self._day_boundary_signature: tuple[object, ...] | None = None
        self._sr_items: list[pg.GraphicsItem] = []  # support/resistance level lines
        self._pending_decision: dict | None = None
        self._direction_items: list[pg.GraphicsItem] = []
        self._seq_label_font_pt: int = 11
        self._fit_on_next_render: bool = False
        self._first_frame_fitted: bool = False
        self._chart_dragging: bool = False
        self._last_scene_mouse_pos = None

        # Price-axis resize state
        self._axis_resizing: bool = False
        self._axis_drag_origin_x: float = 0.0
        self._axis_drag_origin_w: float = 0.0

        vb = self.getViewBox()
        vb.enableAutoRange(x=False, y=False)
        vb.sigRangeChanged.connect(self._position_hover_labels)

        # 30 Hz redraw timer (task 14.5)
        self._timer = QTimer(self)
        self._timer.setInterval(_TIMER_INTERVAL_MS)
        self._timer.timeout.connect(self._on_timer)
        self._timer.start()
        self.scene().sigMouseMoved.connect(self._on_scene_mouse_moved)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_seq_label_font_pt(self, point_size: int) -> None:
        """Set K-line sequence label font size and refresh the chart if needed."""
        point_size = max(6, min(24, int(point_size)))
        if point_size == self._seq_label_font_pt:
            return
        self._seq_label_font_pt = point_size
        if self._latest_frame is not None:
            self._dirty = True

    def set_seq_labels_enabled(self, enabled: bool) -> None:
        """Show/hide the #seq label items from the toolbar."""
        enabled = bool(enabled)
        if enabled == self._seq_labels_enabled:
            return
        self._seq_labels_enabled = enabled
        for label in self._seq_labels:
            label.setVisible(enabled)

    def set_moving_average_overlays(
        self,
        ema_enabled: bool,
        ma_periods: Sequence[int],
    ) -> None:
        """Select moving averages rendered over the K-line candles."""
        selected = tuple(sorted({int(period) for period in ma_periods if int(period) > 0}))
        if self._ema_enabled == bool(ema_enabled) and self._ma_periods == selected:
            return
        self._ema_enabled = bool(ema_enabled)
        self._ma_periods = selected
        if self._latest_frame is not None:
            self._dirty = True

    def set_frame(self, frame: "KlineFrame", *, fit_view: bool = False) -> None:
        """Cache the latest KlineFrame; actual redraw happens on the timer."""
        if self._should_skip_redraw(frame):
            self._latest_frame = frame
            if fit_view or not self._first_frame_fitted:
                self._fit_on_next_render = True
            return
        self._latest_frame = frame
        if fit_view or not self._first_frame_fitted:
            self._fit_on_next_render = True
        if not self._chart_dragging:
            self._dirty = True

    def set_frame_now(self, frame: "KlineFrame", *, fit_view: bool = False) -> None:
        """Apply *frame* to the chart immediately (bypass 30 Hz throttle)."""
        if self._should_skip_redraw(frame):
            self._latest_frame = frame
            if fit_view and not self._first_frame_fitted:
                self.fit_view()
            return
        self._latest_frame = frame
        if self._chart_dragging:
            self._dirty = True
            return
        self._dirty = False
        if fit_view:
            self._fit_on_next_render = True
        self._render_frame(frame)

    def _should_skip_redraw(self, frame: "KlineFrame") -> bool:
        """Skip repaint when the screen already shows the exact same chart data."""
        from pa_agent.data.snapshot import frames_equal_for_chart

        current = self._latest_frame
        if current is None or self._batched_candle_item is None:
            return False
        return frames_equal_for_chart(current, frame)

    def request_fit_on_next_render(self) -> None:
        """Zoom/pan to fit the next rendered frame (or now if one is already shown)."""
        self._fit_on_next_render = True
        if self._latest_frame is not None:
            self._dirty = True

    def fit_view(self) -> None:
        """Set view range to show all bars and a comfortable price span."""
        frame = self._latest_frame
        if frame is None or not frame.bars:
            return
        x_range, y_range = self._view_ranges_for_frame(frame)
        self.getViewBox().setRange(
            xRange=x_range,
            yRange=y_range,
            padding=0,
        )
        self._first_frame_fitted = True

    def displayed_frame(self) -> "KlineFrame | None":
        """Return the KlineFrame currently shown on the chart."""
        return self._latest_frame

    def set_decision(self, decision: dict) -> None:
        """Draw or clear entry/TP/SL lines and direction marker from the AI decision."""
        order_type = decision.get("order_type", _NO_ORDER_TEXT)
        overlay_active = bool(decision.get("chart_overlay_active"))

        if order_type == _NO_ORDER_TEXT and not overlay_active:
            self._pending_decision = None
            self._overlay.clear_lines(self)
            self._clear_direction_marker()
            return

        self._pending_decision = decision
        entry = decision.get("entry_price")
        tp = decision.get("take_profit_price")
        tp2 = decision.get("take_profit_price_2")
        sl = decision.get("stop_loss_price")

        if entry is not None and tp is not None and sl is not None:
            try:
                tp2_val = float(tp2) if tp2 is not None else None
                self._overlay.set_lines(
                    self,
                    float(entry),
                    float(tp),
                    float(sl),
                    tp2=tp2_val,
                    continuity=overlay_active,
                )
            except (TypeError, ValueError):
                self._overlay.clear_lines(self)
        else:
            self._overlay.clear_lines(self)

        self._update_direction_marker()

    def clear_decision_overlay(self) -> None:
        """Remove entry/TP/SL lines and direction marker; keep the current K-line frame."""
        self._overlay.clear_lines(self)
        self._clear_direction_marker()
        self._pending_decision = None

    def set_support_resistance(self, levels: list) -> None:
        """Draw horizontal support/resistance lines from StructureLevel objects.

        Parameters
        ----------
        levels:
            List of ``StructureLevel`` objects (from ``pa_agent.gui.support_resistance``).
            Supports are drawn in green, resistances in red/amber.
        """
        plot = self.getPlotItem()
        for item in self._sr_items:
            plot.removeItem(item)
        self._sr_items.clear()

        for level in levels:
            kind = getattr(level, "kind", "support")
            price = getattr(level, "price", None)
            low = getattr(level, "low", price)
            high = getattr(level, "high", price)
            label_text = getattr(level, "label", kind)
            if price is None:
                continue

            if kind == "support":
                color = (0, 192, 135, 180)      # tokens.MKT_DOWN
                text_color = (0, 192, 135)
            else:
                color = (192, 145, 60, 180)    # muted amber (tokens.WARNING)
                text_color = (205, 167, 86)

            # Draw the midline
            line = pg.InfiniteLine(
                pos=price,
                angle=0,
                pen=pg.mkPen(color=color, width=1,
                             style=pg.QtCore.Qt.PenStyle.DashLine),
                movable=False,
            )
            # Keep the actionable level visible over every candlestick.
            line.setZValue(10)
            plot.addItem(line)
            self._sr_items.append(line)

            # Draw a zone fill if it's a range (high != low)
            is_zone = abs((high or price) - (low or price)) > 1e-9
            if is_zone and low is not None and high is not None:
                zone_color = (*color[:3], 28)  # very transparent fill
                fill = pg.LinearRegionItem(
                    values=(low, high),
                    orientation="horizontal",
                    movable=False,
                    brush=pg.mkBrush(color=zone_color),
                    pen=pg.mkPen(None),
                )
                # A price zone is context, so it must never obscure K-lines.
                fill.setZValue(-10)
                plot.addItem(fill)
                self._sr_items.append(fill)

            # Label
            label = pg.TextItem(
                text=f"{label_text}: {price:.5g}",
                color=text_color,
                anchor=(0.0, 0.5),
            )
            label.setZValue(11)
            plot.addItem(label)
            self._sr_items.append(label)
            label._sr_price = float(price)  # type: ignore[attr-defined]

        # Position labels at left edge (use exact price, not rounded display text)
        if self._sr_items:
            try:
                x_min = self.getViewBox().viewRange()[0][0]
                for item in self._sr_items:
                    if isinstance(item, pg.TextItem):
                        p = getattr(item, "_sr_price", None)
                        if p is not None:
                            item.setPos(x_min, float(p))
            except Exception:  # noqa: BLE001
                pass

    def clear_support_resistance(self) -> None:
        """Remove all support/resistance lines from the chart."""
        plot = self.getPlotItem()
        for item in self._sr_items:
            plot.removeItem(item)
        self._sr_items.clear()

    # ── Price-axis resize via viewportEvent ──────────────────────────────────

    def _axis_right_edge_wx(self) -> float:
        """Right edge x of the left price axis in viewport coordinates."""
        axis = self.getPlotItem().getAxis("left")
        geom = axis.geometry()  # layout-managed rect (not sceneBoundingRect!)
        return float(self.mapFromScene(geom.bottomRight()).x())

    def _axis_vertical_range_wy(self) -> tuple[float, float]:
        """Top/bottom y of the left price axis in viewport coordinates."""
        axis = self.getPlotItem().getAxis("left")
        geom = axis.geometry()
        return (
            float(self.mapFromScene(geom.topLeft()).y()),
            float(self.mapFromScene(geom.bottomRight()).y()),
        )

    def _in_axis_resize_zone(self, vx: float, vy: float) -> bool:
        """True when (vx, vy) is within ``_AXIS_RESIZE_EDGE_PX`` of the axis right edge."""
        edge = self._axis_right_edge_wx()
        top, bot = self._axis_vertical_range_wy()
        return abs(vx - edge) < _AXIS_RESIZE_EDGE_PX and top <= vy <= bot

    def viewportEvent(self, ev):  # noqa: N802
        """Intercept viewport mouse events to handle price-axis width resizing.

        This is the canonical entry-point for viewport events in
        ``QAbstractScrollArea`` (parent of ``QGraphicsView``).  We check
        whether the event is inside the price-axis resize zone; if so, we
        handle the drag ourselves and return ``True`` to prevent the event
        from reaching ``QGraphicsView::viewportEvent`` (and thus the scene).
        Otherwise we delegate to the superclass so normal pan/zoom/drag
        on the ViewBox works as usual.
        """
        et = ev.type()

        if et == QEvent.Type.MouseMove:
            pos = ev.position()
            if self._axis_resizing:
                dx = pos.x() - self._axis_drag_origin_x
                new_w = max(
                    _AXIS_RESIZE_MIN_WIDTH,
                    int(self._axis_drag_origin_w + dx),
                )
                self.getPlotItem().getAxis("left").setWidth(new_w)
                self.axis_width_changed.emit(new_w)
                ev.accept()
                return True  # consume event — don't forward to scene
            # Cursor hint (on the viewport, not the QGraphicsView)
            vp = self.viewport()
            if self._in_axis_resize_zone(pos.x(), pos.y()):
                vp.setCursor(Qt.CursorShape.SplitHCursor)
            else:
                vp.unsetCursor()

        elif et == QEvent.Type.MouseButtonPress and ev.button() == Qt.MouseButton.LeftButton:
            pos = ev.position()
            if self._in_axis_resize_zone(pos.x(), pos.y()):
                self._axis_resizing = True
                self._axis_drag_origin_x = pos.x()
                self._axis_drag_origin_w = self.getPlotItem().getAxis("left").width()
                ev.accept()
                return True
            if self.getViewBox().sceneBoundingRect().contains(self.mapToScene(pos.toPoint())):
                self._set_chart_dragging(True)

        elif et == QEvent.Type.MouseButtonRelease:
            if self._axis_resizing:
                self._axis_resizing = False
                ev.accept()
                return True
            if ev.button() == Qt.MouseButton.LeftButton:
                if self._chart_dragging:
                    self._set_chart_dragging(False)

        return super().viewportEvent(ev)

    def reset(self) -> None:
        """Clear all chart items (candles, labels, EMA, overlay lines)."""
        self._hide_hover_close_line()
        self._clear_day_boundaries()
        self.clear_decision_overlay()
        self._clear_candles_and_labels()
        self._clear_moving_average_lines()
        self._latest_frame = None
        self._time_axis.clear_bars()
        self._dirty = False
        self._fit_on_next_render = False
        self._first_frame_fitted = False
        self.frame_rendered.emit(None)

    # ── Timer slot ────────────────────────────────────────────────────────────

    def _on_timer(self) -> None:
        """Called every ~33 ms; redraws only when a new frame is available."""
        if self._chart_dragging or not self._dirty or self._latest_frame is None:
            return
        self._dirty = False
        self._render_frame(self._latest_frame)

    def _set_chart_dragging(self, dragging: bool) -> None:
        """Suspend costly interaction updates while the user pans the chart."""
        if self._chart_dragging == dragging:
            return
        self._chart_dragging = dragging
        if dragging:
            self._hide_hover_close_line()
        self.chart_dragging_changed.emit(dragging)
        if not dragging:
            QTimer.singleShot(0, self._restore_after_chart_drag)

    def _restore_after_chart_drag(self) -> None:
        """Apply the latest deferred frame and restore hover once after panning."""
        if self._chart_dragging:
            return
        if self._dirty and self._latest_frame is not None:
            self._dirty = False
            self._render_frame(self._latest_frame)
        if self._last_scene_mouse_pos is not None:
            self._on_scene_mouse_moved(self._last_scene_mouse_pos)

    # ── Internal rendering ────────────────────────────────────────────────────

    def _render_frame(self, frame: "KlineFrame") -> None:
        """Update chart items in place whenever the frame shape is unchanged."""
        bars = frame.bars
        n = len(bars)
        if n == 0:
            self._clear_candles_and_labels()
            self._clear_moving_average_lines()
            self._time_axis.clear_bars()
            self._date_axis.clear_boundaries()
            self._clear_day_boundaries()
            self.frame_rendered.emit(frame)
            return
        self._time_axis.set_bars(bars, frame.symbol, frame.timeframe)
        self._update_candles_and_labels(bars)

        self._render_moving_averages(frame)

        self._update_direction_marker()

        if self._hover_scene_pos is not None:
            self._on_scene_mouse_moved(self._hover_scene_pos)

        if self._fit_on_next_render:
            self._fit_on_next_render = False
            self.fit_view()
        self._set_day_boundaries(bars, frame.timeframe)
        self.frame_rendered.emit(frame)

    def _update_candles_and_labels(self, bars) -> None:
        """Update candles as one batched scene item and refresh seq labels.

        Closed bars are re-rendered only when the closed history changes; a
        live forming-bar tick regenerates just that bar's small picture.
        """
        n = len(bars)
        if self._batched_candle_item is None:
            self._batched_candle_item = BatchedCandleItem()
            self.addItem(self._batched_candle_item)
            self._candle_items.clear()

        closed_bars = [bar for bar in bars if bar.closed]
        signature = tuple(
            (bar.seq, bar.ts_open, bar.open, bar.high, bar.low, bar.close, bar.volume, bar.amount)
            for bar in closed_bars
        )
        # A pure-closed frame and the next live frame can contain the same
        # closed bars but have different slot counts.  The forming bar's X
        # coordinate depends on the total count, so that transition must
        # rebuild both pictures instead of taking the forming-bar fast path.
        same_bar_shape = len(self._batched_candle_item._bars) == n
        if (
            same_bar_shape
            and self._closed_count == len(closed_bars)
            and self._closed_signature == signature
        ):
            if bars and not bars[0].closed:
                forming_signature = (
                    bars[0].ts_open, bars[0].open, bars[0].high,
                    bars[0].low, bars[0].close, bars[0].volume, bars[0].amount,
                )
                if forming_signature == self._last_forming_signature:
                    return
                self._batched_candle_item.set_forming_bar(bars[0])
                self._last_forming_signature = forming_signature
        else:
            self._batched_candle_item.set_bars(bars)
            self._closed_signature = signature
            self._closed_count = len(closed_bars)
            if bars and not bars[0].closed:
                self._last_forming_signature = (
                    bars[0].ts_open, bars[0].open, bars[0].high,
                    bars[0].low, bars[0].close, bars[0].volume, bars[0].amount,
                )
            else:
                self._last_forming_signature = None

        for label in self._seq_labels:
            self.removeItem(label)
        self._seq_labels.clear()
        for index, bar in enumerate(bars):
            if bar.seq > 0 and bar.seq % 2 == 1:
                x_pos = n - 1 - index
                label = SeqLabelItem(
                    bar.seq,
                    x_pos,
                    bar.high,
                    font_pt=self._seq_label_font_pt,
                    forming=not bar.closed,
                )
                label.setVisible(self._seq_labels_enabled)
                self.addItem(label)
                self._seq_labels.append(label)

    def _clear_moving_average_lines(self) -> None:
        for line in self._moving_average_lines.values():
            self.removeItem(line)
        self._moving_average_lines.clear()
        self._ema_line = None

    def _render_moving_averages(self, frame: "KlineFrame") -> None:
        bars = frame.bars
        series: list[tuple[str, Sequence[float], str | tuple[int, ...]]] = []
        if self._ema_enabled:
            ema_color: str | tuple[int, ...] = _EMA_COLOR
            if bars and not bars[0].closed:
                ema_color = (*_EMA_COLOR, 140)
            series.append(("EMA20", frame.indicators.ema20, ema_color))
        closes = tuple(float(bar.close) for bar in bars)
        ma_colors = {
            5: T.ACCENT_HOVER,
            10: T.CHART_LINE,
            20: T.CHART_LINE_2,
            30: T.CHART_LINE_3,
            60: "#9B7EBD",
        }
        for period in self._ma_periods:
            series.append(
                (f"MA{period}", sma_newest_first(closes, period), ma_colors.get(period, T.FG_2))
            )
        active_names = {name for name, _values, _color in series}
        for name in tuple(self._moving_average_lines):
            if name not in active_names:
                self.removeItem(self._moving_average_lines.pop(name))
        for name, values, color in series:
            points = [
                (len(bars) - 1 - index, float(value))
                for index, value in enumerate(values)
                if math.isfinite(float(value))
            ]
            if not points:
                line = self._moving_average_lines.pop(name, None)
                if line is not None:
                    self.removeItem(line)
                continue
            x_data = np.array([point[0] for point in points], dtype=float)
            y_data = np.array([point[1] for point in points], dtype=float)
            line = self._moving_average_lines.get(name)
            if line is None:
                line = pg.PlotDataItem(pen=pg.mkPen(color=color, width=1.2))
                line.setZValue(5)
                self.addItem(line)
                self._moving_average_lines[name] = line
            line.setData(x=x_data, y=y_data)
        self._ema_line = self._moving_average_lines.get("EMA20")

    def _view_ranges_for_frame(
        self,
        frame: "KlineFrame",
    ) -> tuple[tuple[float, float], tuple[float, float]]:
        """Compute (x_range, y_range) for this chart's visible-bar window."""
        bars = frame.bars
        n = len(bars)
        visible_count = min(self._fit_visible_bars, n)
        visible_bars = bars[:visible_count]
        visible_series: list[Sequence[float]] = []
        if self._ema_enabled:
            visible_series.append(frame.indicators.ema20[:visible_count])
        closes = tuple(float(bar.close) for bar in bars)
        visible_series.extend(
            sma_newest_first(closes, period)[:visible_count] for period in self._ma_periods
        )

        y_min = min(b.low for b in visible_bars)
        y_max = max(b.high for b in visible_bars)

        for values in visible_series:
            for value in values:
                if math.isfinite(float(value)):
                    y_min = min(y_min, float(value))
                    y_max = max(y_max, float(value))

        decision = self._pending_decision
        if decision is not None:
            for key in (
                "entry_price",
                "take_profit_price",
                "take_profit_price_2",
                "stop_loss_price",
            ):
                raw = decision.get(key)
                if raw is None:
                    continue
                try:
                    price = float(raw)
                except (TypeError, ValueError):
                    continue
                y_min = min(y_min, price)
                y_max = max(y_max, price)

        span = y_max - y_min
        if span <= 0:
            mid = y_max if y_max != 0 else 1.0
            span = abs(mid) * 0.01 or 1.0
        y_pad = span * _Y_PADDING_RATIO
        y_top = span * _Y_TOP_EXTRA_RATIO

        # x=0 is oldest; newest bar is at x=n-1 — show only the rightmost window.
        x_left = float(max(0, n - self._fit_visible_bars))
        x_min = x_left - _X_MARGIN_BARS
        x_max = float(n - 1) + _X_MARGIN_BARS
        view_y_min = y_min - y_pad
        view_y_max = y_max + y_pad + y_top
        view_mid = (view_y_min + view_y_max) / 2.0
        scaled_half_span = (view_y_max - view_y_min) / (2.0 * self._candle_vertical_scale)
        return (
            (x_min, x_max),
            (view_mid - scaled_half_span, view_mid + scaled_half_span),
        )

    def _clear_direction_marker(self) -> None:
        for item in self._direction_items:
            self.removeItem(item)
        self._direction_items.clear()

    def _update_direction_marker(self) -> None:
        """Draw ▲/▼ at newest bar × entry price for long/short."""
        self._clear_direction_marker()
        decision = self._pending_decision
        frame = self._latest_frame
        if decision is None or frame is None:
            return
        if (
            decision.get("order_type", _NO_ORDER_TEXT) == _NO_ORDER_TEXT
            and not decision.get("chart_overlay_active")
        ):
            return

        entry = decision.get("entry_price")
        if entry is None:
            return
        try:
            entry_f = float(entry)
        except (TypeError, ValueError):
            return

        n = len(frame.bars)
        if n == 0:
            return

        long = is_long_direction(decision.get("order_direction"))
        if long is True:
            symbol, color = "▲", (63, 185, 80)
            anchor = (0.5, 1.0)
        elif long is False:
            symbol, color = "▼", (248, 81, 73)
            anchor = (0.5, 0.0)
        else:
            return

        x_pos = float(n - 1)
        marker = pg.TextItem(
            text=symbol,
            color=color,
            anchor=anchor,
        )
        from PyQt6.QtGui import QFont

        font = QFont()
        font.setPointSize(14)
        font.setBold(True)
        marker.setFont(font)
        marker.setPos(x_pos, entry_f)
        self.addItem(marker)
        self._direction_items.append(marker)

    def _clear_candles_and_labels(self) -> None:
        """Remove all candle and label items from the plot."""
        for item in self._candle_items:
            self.removeItem(item)
        self._candle_items.clear()
        if self._batched_candle_item is not None:
            self.removeItem(self._batched_candle_item)
            self._batched_candle_item = None
        self._closed_signature = None
        self._closed_count = 0
        self._last_forming_signature = None
        for item in self._seq_labels:
            self.removeItem(item)
        self._seq_labels.clear()

    def _clear_day_boundaries(self) -> None:
        for line in self._day_boundary_lines:
            self.removeItem(line)
        self._day_boundary_lines.clear()
        self._day_boundary_signature = None

    def _set_day_boundaries(self, bars, timeframe: str) -> None:
        signature = (str(timeframe), *(float(bar.ts_open) for bar in bars))
        if signature == self._day_boundary_signature:
            return
        self._clear_day_boundaries()
        self._day_boundary_signature = signature
        if not _is_intraday_timeframe(timeframe):
            self._date_axis.clear_boundaries()
            return
        labels_by_x: dict[float, str] = {}
        boundary_positions: list[float] = []
        chronological = list(reversed(bars))
        for x_pos in range(1, len(chronological)):
            previous_dt = _market_datetime(
                chronological[x_pos - 1].ts_open,
                self._time_axis._symbol,
            )
            current_dt = _market_datetime(
                chronological[x_pos].ts_open,
                self._time_axis._symbol,
            )
            if previous_dt.date() != current_dt.date():
                boundary_x = float(x_pos) - 0.5
                boundary_index = len(boundary_positions)
                boundary_positions.append(boundary_x)
                if _show_day_boundary_label(timeframe, boundary_index):
                    labels_by_x[boundary_x] = f"{previous_dt:%m-%d}/{current_dt:%m-%d}"
        self._date_axis.set_boundaries(labels_by_x)
        for boundary_x in boundary_positions:
            line = pg.InfiniteLine(
                pos=boundary_x,
                angle=90,
                movable=False,
                pen=pg.mkPen(
                    color=(100, 116, 139, 145),
                    width=1,
                    style=pg.QtCore.Qt.PenStyle.DashLine,
                ),
            )
            line.setZValue(20)
            self.addItem(line)
            self._day_boundary_lines.append(line)

    # ── Hover close-price guide ──────────────────────────────────────────────

    def _on_scene_mouse_moved(self, scene_pos) -> None:
        """Show the hovered K-line's close guide, exact price, and change rate."""
        self._last_scene_mouse_pos = scene_pos
        if self._chart_dragging:
            return
        self._hover_scene_pos = scene_pos
        frame = self._latest_frame
        view_box = self.getViewBox()
        if (
            frame is None
            or not frame.bars
            or not view_box.sceneBoundingRect().contains(scene_pos)
        ):
            self._hide_hover_close_line()
            return

        point = view_box.mapSceneToView(scene_pos)
        x_index = int(round(float(point.x())))
        bars = frame.bars
        if not 0 <= x_index < len(bars):
            self._hide_hover_close_line()
            return
        bar = bars[len(bars) - 1 - x_index]
        close_price = float(bar.close)
        self._hover_close_price = close_price
        self._hover_x_index = x_index
        self._hover_close_line.setPos(close_price)
        self._hover_time_line.setPos(float(x_index))
        self._hover_price_label.setText(
            self._format_hover_price(close_price),
            color=_HOVER_PRICE_COLOR,
        )
        change_rate = self._bar_change_rate(float(bar.open), close_price)
        if change_rate is None:
            change_text = "--"
            change_color = _HOVER_FLAT_COLOR
        else:
            change_text = f"{change_rate:+.2f}%"
            change_color = (
                _HOVER_RISE_COLOR
                if change_rate > 0
                else _HOVER_FALL_COLOR
                if change_rate < 0
                else _HOVER_FLAT_COLOR
            )
        self._hover_change_label.setText(change_text, color=change_color)
        self._hover_time_label.setText(
            self._time_axis.format_timestamp(x_index),
            color=_HOVER_PRICE_COLOR,
        )
        self._position_hover_labels()
        self._hover_close_line.show()
        self._hover_time_line.show()
        self._hover_price_label.show()
        self._hover_change_label.show()
        self._hover_time_label.show()
        self.hover_x_changed.emit(x_index)

    @staticmethod
    def _format_hover_price(value: float) -> str:
        return f"{value:.8f}".rstrip("0").rstrip(".")

    @staticmethod
    def _bar_change_rate(open_price: float, close_price: float) -> float | None:
        if not math.isfinite(open_price) or not math.isfinite(close_price) or open_price == 0:
            return None
        return (close_price - open_price) / open_price * 100.0

    def _position_hover_labels(self, *_args) -> None:
        if self._chart_dragging or self._hover_close_price is None:
            return
        view_range = self.getViewBox().viewRange()
        x_min, x_max = view_range[0]
        self._hover_price_label.setPos(float(x_min), self._hover_close_price)
        self._hover_change_label.setPos(float(x_max), self._hover_close_price)
        if self._hover_x_index is not None:
            self._hover_time_label.setPos(float(self._hover_x_index), float(view_range[1][0]))

    def _hide_hover_close_line(self) -> None:
        self._hover_scene_pos = None
        self._hover_close_price = None
        self._hover_x_index = None
        self._hover_close_line.hide()
        self._hover_time_line.hide()
        self._hover_price_label.hide()
        self._hover_change_label.hide()
        self._hover_time_label.hide()
        self.hover_x_changed.emit(None)

    def show_external_vertical_guide(self, x_index: int) -> None:
        """Show only the vertical guide while the lower indicator plot is hovered."""
        frame = self._latest_frame
        if frame is None or not 0 <= int(x_index) < len(frame.bars):
            self.clear_external_vertical_guide()
            return
        self._hover_scene_pos = None
        self._hover_close_price = None
        self._hover_x_index = int(x_index)
        self._hover_time_line.setPos(float(x_index))
        self._hover_close_line.hide()
        self._hover_price_label.hide()
        self._hover_change_label.hide()
        self._hover_time_label.hide()
        self._hover_time_line.show()

    def clear_external_vertical_guide(self) -> None:
        if self._hover_scene_pos is not None:
            return
        self._hover_x_index = None
        self._hover_time_line.hide()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hide_hover_close_line()
        super().leaveEvent(event)
