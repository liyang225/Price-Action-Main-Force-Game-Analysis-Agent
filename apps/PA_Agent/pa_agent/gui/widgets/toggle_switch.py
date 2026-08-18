"""A compact binary switch for settings forms."""
from __future__ import annotations

from PyQt6.QtCore import (
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QAbstractButton

from pa_agent.gui.theme import tokens as T


def _css_ease() -> QEasingCurve:
    """Match CSS ``ease`` for the track colour, border, and glow transition."""
    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    curve.addCubicBezierSegment(QPointF(0.25, 0.1), QPointF(0.25, 1.0), QPointF(1, 1))
    return curve


def _thumb_ease() -> QEasingCurve:
    """Match the reference thumb's cubic-bezier(0.25, 0.8, 0.25, 1)."""
    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    curve.addCubicBezierSegment(QPointF(0.25, 0.8), QPointF(0.25, 1.0), QPointF(1, 1))
    return curve


def _mixed_color(start: str, end: str, progress: float) -> QColor:
    """Return a linear RGB blend for a bounded visual state transition."""
    source = QColor(start)
    target = QColor(end)
    ratio = max(0.0, min(1.0, progress))
    return QColor(
        round(source.red() + (target.red() - source.red()) * ratio),
        round(source.green() + (target.green() - source.green()) * ratio),
        round(source.blue() + (target.blue() - source.blue()) * ratio),
    )


class ToggleSwitch(QAbstractButton):
    """A 48×24 switch with CSS-equivalent state and thumb transitions."""

    stateChanged = pyqtSignal(int)

    _WIDTH = 48
    _HEIGHT = 24
    _THUMB_SIZE = 18

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._thumb_offset = 0.0
        self._track_progress = 0.0
        self._thumb_animation = QPropertyAnimation(self, b"thumbOffset", self)
        self._thumb_animation.setDuration(400)
        self._thumb_animation.setEasingCurve(_thumb_ease())
        self._track_animation = QPropertyAnimation(self, b"trackProgress", self)
        self._track_animation.setDuration(400)
        self._track_animation.setEasingCurve(_css_ease())
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("切换开关")
        self.toggled.connect(self._on_toggled)

    @pyqtProperty(float)
    def thumbOffset(self) -> float:  # noqa: N802
        """Current horizontal progress of the switch thumb, from 0 to 1."""
        return self._thumb_offset

    @thumbOffset.setter
    def thumbOffset(self, value: float) -> None:  # noqa: N802
        self._thumb_offset = max(0.0, min(1.0, float(value)))
        self.update()

    @pyqtProperty(float)
    def trackProgress(self) -> float:  # noqa: N802
        """Current colour, border, and glow transition progress, from 0 to 1."""
        return self._track_progress

    @trackProgress.setter
    def trackProgress(self, value: float) -> None:  # noqa: N802
        self._track_progress = max(0.0, min(1.0, float(value)))
        self.update()

    def _on_toggled(self, checked: bool) -> None:
        self.stateChanged.emit(
            Qt.CheckState.Checked.value if checked else Qt.CheckState.Unchecked.value
        )
        target = 1.0 if checked else 0.0
        for animation, current in (
            (self._thumb_animation, self._thumb_offset),
            (self._track_animation, self._track_progress),
        ):
            animation.stop()
            animation.setStartValue(current)
            animation.setEndValue(target)
            animation.start()

    def setChecked(self, checked: bool) -> None:  # noqa: N802
        """Keep the painted state in sync during signal-blocked settings loads."""
        was_checked = self.isChecked()
        super().setChecked(checked)
        if was_checked == self.isChecked() or not self.signalsBlocked():
            return
        self._thumb_animation.stop()
        self._track_animation.stop()
        target = 1.0 if self.isChecked() else 0.0
        self.thumbOffset = target
        self.trackProgress = target

    def sizeHint(self) -> QSize:  # type: ignore[override]
        return QSize(self._WIDTH, self._HEIGHT)

    def minimumSizeHint(self) -> QSize:  # type: ignore[override]
        return self.sizeHint()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        rect = self._track_rect()
        progress = self._track_progress if self.isEnabled() else 0.0
        track_color = _mixed_color("#16181D", "#0B172E", progress)
        border_color = _mixed_color("#2B303B", T.ACCENT, progress)

        # A bounded inner/outer blue glow replaces the previous visual noise:
        # it communicates the on state without a looping shimmer effect.
        if progress:
            glow = QColor(T.ACCENT)
            glow.setAlpha(round(76 * progress))
            painter.setPen(QPen(glow, 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
            inner_glow = QColor(T.ACCENT)
            inner_glow.setAlpha(round(64 * progress))
            painter.setPen(QPen(inner_glow, 1.0))
            painter.drawRoundedRect(
                rect.adjusted(3, 3, -3, -3),
                (rect.height() - 6) / 2,
                (rect.height() - 6) / 2,
            )

        painter.setPen(QPen(border_color, 1.0))
        painter.setBrush(track_color)
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)

        left_x = rect.left() + 2
        right_x = rect.right() - self._THUMB_SIZE - 2
        thumb_x = left_x + (right_x - left_x) * self._thumb_offset
        thumb_rect = QRectF(
            thumb_x,
            rect.center().y() - self._THUMB_SIZE / 2,
            self._THUMB_SIZE,
            self._THUMB_SIZE,
        )
        if progress:
            thumb_glow = QColor(T.ACCENT)
            thumb_glow.setAlpha(round(204 * progress))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(thumb_glow)
            painter.drawEllipse(thumb_rect.adjusted(-1.5, -1.5, 1.5, 1.5))
        painter.setBrush(
            _mixed_color("#6B7280", "#FFFFFF", progress)
            if self.isEnabled()
            else QColor(T.FG_3)
        )
        painter.drawEllipse(thumb_rect)

    def _track_rect(self) -> QRectF:
        """Return the fixed 48×24 visual track, centered in its widget."""
        top = (self.height() - self._HEIGHT) / 2 + 0.5
        return QRectF(0.5, top, self._WIDTH - 1.0, self._HEIGHT - 1.0)
