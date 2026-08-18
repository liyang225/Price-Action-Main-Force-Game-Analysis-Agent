"""Overlay horizontal lines for entry / TP / SL on a pyqtgraph PlotWidget."""
from __future__ import annotations

from typing import TYPE_CHECKING

import pyqtgraph as pg
from PyQt6.QtGui import QColor

from pa_agent.gui.theme import tokens as T

if TYPE_CHECKING:
    from pyqtgraph import PlotItem

# Line colors keep their learned role mapping (Entry blue / TP green / SL red)
# at the palette's restraint level — values come from the design tokens.
_COLOR_ENTRY = QColor(T.LINE_ENTRY)
_COLOR_TP = QColor(T.LINE_TP)
_COLOR_TP2 = QColor(T.LINE_TP2)
_COLOR_SL = QColor(T.LINE_SL)


class OverlayLines:
    """Manages entry / TP / SL horizontal lines on a PlotWidget.

    Each line is an ``pg.InfiniteLine`` (angle=0, i.e. horizontal) paired
    with a ``pg.TextItem`` label positioned at the left edge of the view.

    Usage::

        overlay = OverlayLines()
        overlay.set_lines(plot_item, entry=1900.0, tp=1920.0, sl=1880.0)
        # … later …
        overlay.clear_lines(plot_item)
    """

    def __init__(self) -> None:
        self._items: list[pg.GraphicsItem] = []
        # (TextItem, exact line price) — never re-parse Y from formatted label text.
        self._labels: list[tuple[pg.TextItem, float]] = []
        self._plot: "PlotItem | None" = None
        self._range_conn = None

    # ── Public API ────────────────────────────────────────────────────────────

    def set_lines(
        self,
        plot: "PlotItem",
        entry: float,
        tp: float,
        sl: float,
        *,
        tp2: float | None = None,
        continuity: bool = False,
    ) -> None:
        """Draw (or redraw) horizontal price lines.

        Clears any previously drawn lines first. Labels are anchored to the
        left edge of the current view and stay there on pan/zoom.
        """
        self.clear_lines(plot)
        self._plot = plot

        wait_suffix = " (延续)" if continuity else ""
        specs: list[tuple[float, QColor, str]] = [
            (entry, _COLOR_ENTRY, f"Entry{wait_suffix}"),
            (tp, _COLOR_TP, f"TP1{wait_suffix}"),
            (sl, _COLOR_SL, f"SL{wait_suffix}"),
        ]
        if tp2 is not None:
            specs.insert(2, (tp2, _COLOR_TP2, f"TP2{wait_suffix}"))

        for price, color, label_text in specs:
            line = pg.InfiniteLine(
                pos=price,
                angle=0,
                pen=pg.mkPen(color=color, width=1, style=pg.QtCore.Qt.PenStyle.DashLine),
                movable=False,
            )
            label = pg.TextItem(
                text=f"{label_text}: {price:.5g}",
                color=color,
                anchor=(0.0, 0.5),
            )

            plot.addItem(line)
            plot.addItem(label)
            self._items.extend([line, label])
            self._labels.append((label, float(price)))

        # Position labels at the left edge of the current view
        self._update_label_positions()

        # Keep labels anchored to the left edge when the view changes
        vb = plot.getViewBox()
        self._range_conn = vb.sigRangeChanged.connect(self._update_label_positions)

    def clear_lines(self, plot: "PlotItem") -> None:
        """Remove all managed lines and labels from the plot."""
        if self._range_conn is not None:
            try:
                plot.getViewBox().sigRangeChanged.disconnect(self._range_conn)
            except Exception:  # noqa: BLE001
                pass
            self._range_conn = None

        for item in self._items:
            plot.removeItem(item)
        self._items.clear()
        self._labels.clear()
        self._plot = None

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _update_label_positions(self) -> None:
        """Move all labels to the left edge of the current X view range."""
        if self._plot is None or not self._labels:
            return
        try:
            x_min = self._plot.getViewBox().viewRange()[0][0]
        except Exception:  # noqa: BLE001
            return
        for label, price in self._labels:
            label.setPos(x_min, price)
