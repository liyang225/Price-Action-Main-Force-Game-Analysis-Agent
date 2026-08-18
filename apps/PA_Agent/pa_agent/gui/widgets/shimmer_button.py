"""Continuous-tracking switch sharing the static reference control's visual language."""
from __future__ import annotations

from PyQt6.QtCore import QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter

from pa_agent.gui.widgets.toggle_switch import ToggleSwitch
from pa_agent.gui.theme import tokens as T


class ShimmerButton(ToggleSwitch):
    """A 48×24 tracking switch with its dynamic label and active status dot."""

    stateChanged = pyqtSignal(int)

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(parent)
        self._active_text = "持续分析"
        self._inactive_text = text or "持续分析"
        self._label_gap = 10
        self.setToolTip("切换持续分析")
        self._sync_label()

    def set_tracking_labels(self, *, active: str, inactive: str) -> None:
        """Set the dynamic visual and accessible labels shown beside the switch."""
        self._active_text = active
        self._inactive_text = inactive
        self._sync_label()

    def setChecked(self, checked: bool) -> None:  # noqa: N802
        """Keep accessible state text synchronized with internal state changes."""
        super().setChecked(checked)
        self._sync_label()
        self.updateGeometry()

    def _on_toggled(self, checked: bool) -> None:
        # ToggleSwitch emits its public signal before beginning the paired
        # 400ms track/thumb transition.  This subclass keeps that behavior.
        super()._on_toggled(checked)
        self._sync_label()
        self.updateGeometry()

    def sizeHint(self) -> QSize:  # type: ignore[override]
        text_width = self.fontMetrics().horizontalAdvance(
            self._active_text if self.isChecked() else self._inactive_text
        )
        dot_width = 10 if self.isChecked() else 0
        return QSize(self._WIDTH + self._label_gap + dot_width + text_width, self._HEIGHT + 2)

    def minimumSizeHint(self) -> QSize:  # type: ignore[override]
        label_width = max(
            self.fontMetrics().horizontalAdvance(self._active_text),
            self.fontMetrics().horizontalAdvance(self._inactive_text),
        )
        return QSize(self._WIDTH + self._label_gap + 10 + label_width, self._HEIGHT + 2)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        active = self.isChecked() and self.isEnabled()
        track_rect = self._track_rect()
        text_x = self._WIDTH + self._label_gap
        if active:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(T.ACCENT))
            painter.drawEllipse(QRectF(text_x, track_rect.center().y() - 3, 6, 6))
            text_x += 10
        painter.setPen(QColor(T.ACCENT_HOVER) if active else QColor(T.FG_2))
        font = self.font()
        font.setWeight(QFont.Weight.Medium if active else QFont.Weight.Normal)
        painter.setFont(font)
        line_height = painter.fontMetrics().height()
        painter.drawText(
            text_x,
            int(track_rect.center().y() - line_height / 2),
            max(0, self.width() - text_x),
            line_height,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self.text(),
        )

    def _sync_label(self) -> None:
        label = self._active_text if self.isChecked() else self._inactive_text
        super().setText(label)
        self.setAccessibleName(label)
        self.setAccessibleDescription(f"持续分析开关：{label}")
