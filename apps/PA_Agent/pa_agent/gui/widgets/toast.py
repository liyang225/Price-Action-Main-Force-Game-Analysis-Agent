"""ToastOverlay — lightweight non-blocking notification overlay."""
from __future__ import annotations

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget


class _ToastLabel(QLabel):
    """A single toast notification label."""

    def __init__(self, message: str, parent: QWidget | None = None) -> None:
        super().__init__(message, parent)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            "QLabel {"
            " background-color: rgba(28,33,40,0.92);"
            " color: #E8ECF1;"
            " border: 1px solid #4A7EBB;"
            " border-radius: 8px;"
            " padding: 8px 16px;"
            " font-size: 13px;"
            "}"
        )
        self.adjustSize()


class ToastOverlay(QWidget):
    """Stacking toast overlay anchored to the bottom-right of its parent.

    Usage::

        overlay = ToastOverlay(main_window)
        overlay.show_message("分析完成", duration_ms=3000)
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowFlags(Qt.WindowType.Widget)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._layout.setAlignment(
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight
        )

        self._toasts: list[_ToastLabel] = []
        self.raise_()

    def show_message(self, message: str, *, duration_ms: int = 3000) -> None:
        """Show a toast message that auto-dismisses after *duration_ms* ms."""
        toast = _ToastLabel(message, self)
        self._layout.addWidget(toast)
        self._toasts.append(toast)
        self._reposition()

        QTimer.singleShot(duration_ms, lambda: self._dismiss(toast))

    def _dismiss(self, toast: _ToastLabel) -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)
        self._layout.removeWidget(toast)
        toast.deleteLater()
        self._reposition()

    def _reposition(self) -> None:
        if self.parent() is None:
            return
        parent = self.parent()
        pw = parent.width()   # type: ignore[union-attr]
        ph = parent.height()  # type: ignore[union-attr]
        margin = 16
        w = min(320, pw - 2 * margin)
        h = min(200, ph - 2 * margin)
        self.setGeometry(pw - w - margin, ph - h - margin, w, h)
