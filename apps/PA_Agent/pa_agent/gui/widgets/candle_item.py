"""Self-drawn candlestick item for pyqtgraph."""
from __future__ import annotations

from typing import TYPE_CHECKING

import pyqtgraph as pg
from PyQt6.QtCore import QLineF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen, QPicture

from pa_agent.gui.theme import tokens as T

if TYPE_CHECKING:
    from pa_agent.data.base import KlineBar

# Candle colors follow the requested CN market convention:
# close >= open -> price went UP -> red
# close <  open -> price went DOWN -> green
_COLOR_UP = QColor(T.CHART_UP)
_COLOR_DOWN = QColor(T.CHART_DOWN)
_OUTLINE_UP = QColor(T.CHART_UP_OUTLINE)
_OUTLINE_DOWN = QColor(T.CHART_DOWN_OUTLINE)

# Candle body width as a fraction of the x-spacing (0..1)
_BODY_WIDTH = 0.68
_FORMING_BODY_WIDTH = 0.58


class CandleItem(pg.GraphicsObject):
    """A single OHLCV candlestick drawn via QPainter.

    Parameters
    ----------
    bar:
        The KlineBar data for this candle.
    x_pos:
        Integer x-axis position (0 = leftmost / oldest visible candle).
    forming:
        When True, draw the unclosed bar as a hollow ghost candle (live chart only).
    """

    def __init__(self, bar: "KlineBar", x_pos: int, *, forming: bool = False) -> None:
        super().__init__()
        self._bar = bar
        self._x = x_pos
        self._forming = forming
        self._generate_picture()

    # ── pyqtgraph interface ───────────────────────────────────────────────────

    def boundingRect(self) -> QRectF:
        half = (_FORMING_BODY_WIDTH if self._forming else _BODY_WIDTH) / 2.0
        top = self._bar.high
        bottom = self._bar.low
        span = top - bottom
        margin = span * 0.05 + 1e-8
        return QRectF(
            self._x - half,
            bottom - margin,
            _FORMING_BODY_WIDTH if self._forming else _BODY_WIDTH,
            span + 2 * margin,
        )

    def paint(
        self,
        painter: QPainter,
        option: object,  # QStyleOptionGraphicsItem
        widget: object = None,
    ) -> None:
        painter.drawPicture(0, 0, self._picture)

    def set_bar(self, bar: "KlineBar", x_pos: int, *, forming: bool = False) -> None:
        """Reuse this scene item for updated market data."""
        self.prepareGeometryChange()
        self._bar = bar
        self._x = x_pos
        self._forming = forming
        self._generate_picture()
        self.update()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _generate_picture(self) -> None:
        """Pre-render the candle into a QPicture for fast repaints."""
        self._picture = QPicture()
        p = QPainter(self._picture)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        bar = self._bar
        x = float(self._x)
        if self._forming:
            self._paint_forming(p, bar, x)
        else:
            self._paint_closed(p, bar, x)

        p.end()

    @staticmethod
    def _paint_closed(p: QPainter, bar: "KlineBar", x: float) -> None:
        color = _COLOR_UP if bar.close >= bar.open else _COLOR_DOWN
        outline = _OUTLINE_UP if bar.close >= bar.open else _OUTLINE_DOWN
        p.setPen(QPen(outline, 0))
        p.setBrush(color)
        half = _BODY_WIDTH / 2.0
        body_top, body_bottom = CandleItem._body_bounds(bar)
        body_rect = QRectF(x - half, body_bottom, _BODY_WIDTH, body_top - body_bottom)
        p.drawRect(body_rect)
        CandleItem._paint_wicks(p, bar, x, body_top, body_bottom, QPen(outline, 0))

    @staticmethod
    def _paint_forming(p: QPainter, bar: "KlineBar", x: float) -> None:
        base = _COLOR_UP if bar.close >= bar.open else _COLOR_DOWN
        outline = _OUTLINE_UP if bar.close >= bar.open else _OUTLINE_DOWN
        fill = QColor(base.red(), base.green(), base.blue(), 70)
        wick_pen = QPen(outline, 1)
        wick_pen.setCosmetic(True)
        # Thicker dashed border for forming bar
        border_pen = QPen(outline, 2)
        border_pen.setCosmetic(True)
        border_pen.setStyle(Qt.PenStyle.DashLine)

        half = _FORMING_BODY_WIDTH / 2.0
        body_top, body_bottom = CandleItem._body_bounds(bar)
        span = bar.high - bar.low
        min_body = max(span * 0.06, 1e-6) if span > 0 else 1e-6
        if body_top - body_bottom < min_body:
            mid = (body_top + body_bottom) / 2.0
            body_top = mid + min_body / 2.0
            body_bottom = mid - min_body / 2.0
        body_rect = QRectF(x - half, body_bottom, _FORMING_BODY_WIDTH, body_top - body_bottom)

        CandleItem._paint_wicks(p, bar, x, body_top, body_bottom, wick_pen)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawRect(body_rect)

        p.setPen(border_pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(body_rect)

    @staticmethod
    def _body_bounds(bar: "KlineBar") -> tuple[float, float]:
        body_top = max(bar.open, bar.close)
        body_bottom = min(bar.open, bar.close)
        body_height = body_top - body_bottom
        if body_height < 1e-8:
            mid = (bar.open + bar.close) / 2.0
            body_top = mid + 1e-8
            body_bottom = mid - 1e-8
        return body_top, body_bottom

    @staticmethod
    def _paint_wicks(
        p: QPainter,
        bar: "KlineBar",
        x: float,
        body_top: float,
        body_bottom: float,
        pen: QPen,
    ) -> None:
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if bar.high > body_top:
            p.drawLine(QLineF(x, body_top, x, bar.high))
        if bar.low < body_bottom:
            p.drawLine(QLineF(x, body_bottom, x, bar.low))

class BatchedCandleItem(pg.GraphicsObject):
    """All candles drawn by a single scene item.

    Closed bars are pre-rendered into one ``QPicture``; the forming (live) bar
    lives in a second tiny picture so a price tick does not regenerate the
    whole history.  Panning therefore costs one item lookup + picture replay
    instead of one scene lookup + picture replay per candle.
    """

    def __init__(self) -> None:
        super().__init__()
        self._bars: list["KlineBar"] = []
        self._closed_bars: list["KlineBar"] = []
        self._forming_bar: "KlineBar | None" = None
        self._closed_picture = QPicture()
        self._forming_picture: QPicture | None = None
        self._bounds = QRectF()

    # ── pyqtgraph interface ───────────────────────────────────────────────────

    def boundingRect(self) -> QRectF:
        return self._bounds

    def paint(
        self,
        painter: QPainter,
        option: object,  # QStyleOptionGraphicsItem
        widget: object = None,
    ) -> None:
        painter.drawPicture(0, 0, self._closed_picture)
        if self._forming_picture is not None:
            painter.drawPicture(0, 0, self._forming_picture)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_bars(self, bars: list["KlineBar"]) -> None:
        """Rebuild cached pictures from a full *newest-first* frame."""
        self._bars = list(bars)
        self._forming_bar = self._bars[0] if self._bars and not self._bars[0].closed else None
        self._closed_bars = [bar for bar in self._bars if bar.closed]

        self.prepareGeometryChange()
        self._closed_picture = self._render_closed()
        self._forming_picture = (
            self._render_forming() if self._forming_bar is not None else None
        )
        self._bounds = self._compute_bounds()
        self.update()

    def set_forming_bar(self, bar: "KlineBar") -> None:
        """Update only the live forming bar; closed history is untouched."""
        if not self._bars:
            self._bars = [bar]
            self._forming_bar = bar
        else:
            self._bars[0] = bar
            self._forming_bar = bar
        self.prepareGeometryChange()
        self._forming_picture = self._render_forming()
        self._bounds = self._compute_bounds()
        self.update()

    def clear(self) -> None:
        """Remove every cached bar and picture."""
        self.prepareGeometryChange()
        self._bars.clear()
        self._closed_bars.clear()
        self._forming_bar = None
        self._closed_picture = QPicture()
        self._forming_picture = None
        self._bounds = QRectF()
        self.update()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _n(self) -> int:
        return len(self._bars)

    def _compute_bounds(self) -> QRectF:
        bars = self._bars
        if not bars:
            return QRectF()
        y_min = min(bar.low for bar in bars)
        y_max = max(bar.high for bar in bars)
        margin = (y_max - y_min) * 0.05 + 1e-8
        return QRectF(
            -_BODY_WIDTH,
            y_min - margin,
            len(bars) - 1 + _BODY_WIDTH * 2,
            y_max - y_min + 2 * margin,
        )

    def _render_closed(self) -> QPicture:
        picture = QPicture()
        painter = QPainter(picture)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        n = self._n()
        for index, bar in enumerate(self._closed_bars):
            if self._forming_bar is not None:
                x = float(n - 2 - index)
            else:
                x = float(n - 1 - index)
            CandleItem._paint_closed(painter, bar, x)
        painter.end()
        return picture

    def _render_forming(self) -> QPicture:
        picture = QPicture()
        painter = QPainter(picture)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        if self._forming_bar is not None:
            x = float(self._n() - 1)
            CandleItem._paint_forming(painter, self._forming_bar, x)
        painter.end()
        return picture

