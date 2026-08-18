"""Sequence number label item for pyqtgraph."""
from __future__ import annotations

import pyqtgraph as pg
from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QColor, QFont


class SeqLabelItem(pg.TextItem):
    """A small text label showing a candle's sequence number.

    The label is positioned above the candle's high price.

    Parameters
    ----------
    seq:
        Sequence number (1 = newest bar).
    x_pos:
        Integer x-axis position matching the corresponding CandleItem.
    y_pos:
        Y-axis position (typically the bar's high price).
    """

    _COLOR = QColor(180, 180, 180)  # light grey — unobtrusive

    def __init__(
        self,
        seq: int,
        x_pos: int,
        y_pos: float,
        *,
        font_pt: int = 7,
        forming: bool = False,
    ) -> None:
        label = f"#{seq}" if not forming else f"#{seq}"
        color = QColor(120, 200, 220, 200) if forming else self._COLOR
        super().__init__(
            text=label,
            color=color,
            anchor=(0.5, 1.0),  # horizontally centred, bottom of text at y_pos
        )
        self.setFont(QFont("Arial", font_pt))
        self.setPos(QPointF(float(x_pos), y_pos))

    def set_label(
        self,
        seq: int,
        x_pos: int,
        y_pos: float,
        *,
        font_pt: int = 7,
        forming: bool = False,
    ) -> None:
        """Update the existing label instead of recreating a scene item."""
        color = QColor(120, 200, 220, 200) if forming else self._COLOR
        self.setText(f"#{seq}", color=color)
        font = self.textItem.font()
        if font.pointSize() != font_pt:
            font.setPointSize(font_pt)
            self.setFont(font)
        self.setPos(QPointF(float(x_pos), y_pos))
        self.show()
