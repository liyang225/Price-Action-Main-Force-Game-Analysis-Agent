"""Live market summary row displayed above the K-line chart."""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from pa_agent.gui.theme import tokens as T


def _price(value: Any) -> str:
    try:
        text = f"{float(value):.4f}".rstrip("0").rstrip(".")
        return text or "0"
    except (TypeError, ValueError):
        return "--"


def _percentage(value: Any) -> str:
    try:
        return f"{float(value):+.2f}%"
    except (TypeError, ValueError):
        return "--"


class MarketSummaryStrip(QWidget):
    """One-line display of the latest market quote."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("marketSummaryStrip")
        self.setFixedHeight(44)
        self.setStyleSheet(
            f"#marketSummaryStrip {{ background: {T.BG};"
            f" border-top: 1px solid {T.BORDER_SOFT}; }}"
            "QLabel { border: none; background: transparent; }"
        )
        layout = QHBoxLayout(self)
        # Align the quote block to the chart plot's left edge, rather than the
        # outer widget edge.  The 40 px increase restores the quote gutter.
        layout.setContentsMargins(52, 6, 12, 6)
        layout.setSpacing(0)

        self._price_value = QLabel("--")
        self._price_value.setStyleSheet(self._price_css())
        self._price_value.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self._price_value.setContentsMargins(0, 0, 10, 0)
        self._price_value.setMinimumWidth(0)
        layout.addWidget(self._price_value)

        self._values: dict[str, QLabel] = {}
        for index, (key, caption_text, minimum_width) in enumerate((
            ("change", "涨跌", 110),
            ("open", "今开", 94),
            ("high_low", "最高 / 最低", 160),
        )):
            if index:
                divider = QFrame()
                divider.setFrameShape(QFrame.Shape.VLine)
                divider.setStyleSheet(f"color: {T.SURFACE_3};")
                layout.addWidget(divider)
            cell = QWidget()
            cell.setMinimumWidth(minimum_width)
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(10, 0, 10, 0)
            cell_layout.setSpacing(5)
            label = QLabel(caption_text)
            label.setStyleSheet(f"font-size: 12px; color: {T.FG_2};")
            value = QLabel("--")
            value.setStyleSheet(
                f"font-size: 13px; font-weight: {T.WEIGHT_MEDIUM}; color: {T.FG};"
            )
            value.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            cell_layout.addWidget(label)
            cell_layout.addWidget(value)
            layout.addWidget(cell)
            self._values[key] = value

        layout.addStretch(1)

    @staticmethod
    def _price_css() -> str:
        """The latest price is the data protagonist: weight, not size, leads."""
        return (
            f"font-size: {T.SIZE_QUOTE}px; font-weight: {T.WEIGHT_SEMIBOLD};"
            f"color: {T.FG}; font-family: {T.FONT_MONO};"
        )

    def clear(self) -> None:
        self._price_value.setText("--")
        self._price_value.setStyleSheet(self._price_css())
        for label in self._values.values():
            label.setText("--")
            label.setStyleSheet(
                f"font-size: 13px; font-weight: {T.WEIGHT_MEDIUM}; color: {T.FG};"
            )

    def set_summary(
        self,
        *,
        latest_price: Any = None,
        open_price: Any = None,
        change_rate: Any = None,
        high_price: Any = None,
        low_price: Any = None,
    ) -> None:
        direction = 0
        try:
            change_value = float(change_rate)
            direction = 1 if change_value > 0 else -1 if change_value < 0 else 0
        except (TypeError, ValueError):
            pass
        # Chinese market convention: rising prices are red, falling prices green.
        color = T.MKT_UP if direction > 0 else T.MKT_DOWN if direction < 0 else T.FG
        self._price_value.setText(_price(latest_price))
        self._price_value.setStyleSheet(self._price_css())
        self._values["open"].setText(_price(open_price))
        self._values["change"].setText(_percentage(change_rate))
        self._values["change"].setStyleSheet(
            f"font-size: 13px; font-weight: {T.WEIGHT_MEDIUM}; color: {color};"
            f"font-family: {T.FONT_MONO};"
        )
        self._values["high_low"].setText(f"{_price(high_price)} / {_price(low_price)}")
