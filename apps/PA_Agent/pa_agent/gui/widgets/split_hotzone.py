from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QSplitter, QSplitterHandle


class SplitHotzone(QObject):
    """Turns a splitter's native handle into a deliberately styled hotzone."""

    ratioChanged = pyqtSignal(float)

    def __init__(self, handle: QSplitterHandle) -> None:
        super().__init__(handle)
        self._handle = handle
        self._handle.setObjectName("splitHotzone")
        self._handle.setCursor(Qt.CursorShape.SizeVerCursor)
        self._handle.installEventFilter(self)
        self._dragging = False

    def _set_split_from_global(self, point: QPoint) -> None:
        splitter = self._handle.splitter()
        if splitter is None or splitter.count() < 2:
            return
        local_y = splitter.mapFromGlobal(point).y()
        total = splitter.height()
        if total <= 0:
            return
        first_min = splitter.widget(0).minimumHeight()
        second_min = splitter.widget(1).minimumHeight()
        first = max(
            first_min,
            min(local_y, total - second_min - self._handle.height()),
        )
        second = max(second_min, total - self._handle.height() - first)
        splitter.setSizes([first, second])

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is not self._handle:
            return super().eventFilter(watched, event)
        if event.type() == QEvent.Type.MouseButtonPress:
            mouse_event = event
            if (
                isinstance(mouse_event, QMouseEvent)
                and mouse_event.button() == Qt.MouseButton.LeftButton
            ):
                self._dragging = True
                self._set_split_from_global(mouse_event.globalPosition().toPoint())
                return True
        elif event.type() == QEvent.Type.MouseMove and self._dragging:
            mouse_event = event
            if isinstance(mouse_event, QMouseEvent):
                self._set_split_from_global(mouse_event.globalPosition().toPoint())
                return True
        elif event.type() == QEvent.Type.MouseButtonRelease and self._dragging:
            mouse_event = event
            if (
                isinstance(mouse_event, QMouseEvent)
                and mouse_event.button() == Qt.MouseButton.LeftButton
            ):
                self._dragging = False
                splitter = self._handle.splitter()
                if splitter is not None:
                    sizes = splitter.sizes()
                    total = sum(sizes)
                    if total > 0:
                        self.ratioChanged.emit(sizes[0] / total)
                return True
        return super().eventFilter(watched, event)
