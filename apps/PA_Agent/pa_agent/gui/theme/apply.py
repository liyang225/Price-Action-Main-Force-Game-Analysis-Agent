"""Apply the global application theme."""
from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from PyQt6.QtCore import QEvent, QObject, QSize, QTimer, Qt
from PyQt6.QtGui import QColor, QPainter, QPalette
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QMainWindow,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

_QSS_PATH = Path(__file__).with_name("dark.qss")
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_BORDER_COLOR = 34
_DWMWA_CAPTION_COLOR = 35
_DWMWA_TEXT_COLOR = 36
_WM_SETICON = 0x0080
_ICON_SMALL = 0
_ICON_BIG = 1
_TRANSPARENT_WINDOW_ICON_HANDLE: int | None = None


class _IconInfo(ctypes.Structure):
    _fields_ = (
        ("fIcon", ctypes.c_int),
        ("xHotspot", ctypes.c_ulong),
        ("yHotspot", ctypes.c_ulong),
        ("hbmMask", ctypes.c_void_p),
        ("hbmColor", ctypes.c_void_p),
    )

_COMBO_VIEW_QSS = """
QAbstractItemView {
    background: #181C22;
    color: #9AA5B1;
    border: none;
    outline: none;
    padding: 4px 0;
}
QAbstractItemView::item {
    color: #9AA5B1;
    background: transparent;
    border: none;
    border-left: 2px solid transparent;
    min-height: 22px;
    padding: 6px 10px;
}
QAbstractItemView::item:hover {
    background: #22272F;
    color: #E8ECF1;
}
QAbstractItemView::item:selected {
    background: rgba(74,126,187, 0.15);
    color: #5B8CC9;
    border-left: 2px solid #4A7EBB;
}
"""


class _ComboPopupItemDelegate(QStyledItemDelegate):
    """Paint combo rows independently from the operating-system theme."""

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index,
    ) -> None:
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        popup = option.widget.window() if option.widget is not None else None
        combo = popup.parentWidget() if popup is not None else None
        current_combo_item = isinstance(combo, QComboBox) and index.row() == combo.currentIndex()
        selected = (
            bool(option.state & QStyle.StateFlag.State_Selected) or current_combo_item
        ) and not hovered
        painter.save()
        if hovered:
            painter.fillRect(option.rect, QColor("#22272F"))
        elif selected:
            painter.fillRect(option.rect, QColor(74, 126, 187, 38))
            painter.fillRect(
                option.rect.left(),
                option.rect.top(),
                2,
                option.rect.height(),
                QColor("#4A7EBB"),
            )
        painter.restore()

        text_option = QStyleOptionViewItem(option)
        self.initStyleOption(text_option, index)
        text_option.state &= ~(
            QStyle.StateFlag.State_Selected
            | QStyle.StateFlag.State_MouseOver
            | QStyle.StateFlag.State_HasFocus
        )
        if hovered:
            text_color = QColor("#E8ECF1")
        elif selected:
            text_color = QColor("#5B8CC9")
        else:
            text_color = QColor("#9AA5B1")
        text_option.palette.setColor(QPalette.ColorRole.Text, text_color)
        text_option.palette.setColor(QPalette.ColorRole.WindowText, text_color)
        text_option.rect = option.rect.adjusted(10, 0, -10, 0)
        style = text_option.widget.style() if text_option.widget is not None else QApplication.style()
        style.drawControl(
            QStyle.ControlElement.CE_ItemViewItem,
            text_option,
            painter,
            text_option.widget,
        )

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:  # noqa: N802
        size = super().sizeHint(option, index)
        size.setHeight(max(34, size.height()))
        return size


class _ComboPopupStyler(QObject):
    """Styles Qt's private combo popup container when it is created."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if (
            event.type() in (QEvent.Type.Polish, QEvent.Type.Show)
            and isinstance(watched, QComboBox)
        ):
            self._style_view(watched.view())
        if (
            event.type() == QEvent.Type.Show
            and isinstance(watched, QFrame)
            and watched.metaObject().className() == "QComboBoxPrivateContainer"
        ):
            self._style_popup(watched)
        return False

    @staticmethod
    def _style_popup(container: QFrame) -> None:
        container.setObjectName("comboPopupContainer")
        container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        container.setContentsMargins(0, 0, 0, 0)
        container.setStyleSheet(
            "QFrame#comboPopupContainer {"
            " background: #181C22;"
            " border: 1px solid #333A45;"
            " border-radius: 6px;"
            " padding: 0;"
            "}"
        )

        palette = container.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#181C22"))
        container.setPalette(palette)
        container.setAutoFillBackground(True)

        view = container.findChild(QAbstractItemView)
        if view is not None:
            _ComboPopupStyler._style_view(view)
            QTimer.singleShot(
                0,
                lambda popup=container, popup_view=view: (
                    _ComboPopupStyler._fit_popup_height(popup, popup_view)
                ),
            )

        shadow = QGraphicsDropShadowEffect(container)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 128))
        container.setGraphicsEffect(shadow)

    @staticmethod
    def _style_view(view: QAbstractItemView) -> None:
        view.setFrameShape(QFrame.Shape.NoFrame)
        view.setLineWidth(0)
        view.setMidLineWidth(0)
        view.setContentsMargins(0, 0, 0, 0)
        view.setStyleSheet(_COMBO_VIEW_QSS)
        view_palette = view.palette()
        view_palette.setColor(QPalette.ColorRole.Base, QColor("#181C22"))
        view_palette.setColor(QPalette.ColorRole.Text, QColor("#9AA5B1"))
        view_palette.setColor(QPalette.ColorRole.Highlight, QColor(74, 126, 187, 38))
        view_palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#5B8CC9"))
        view.setPalette(view_palette)
        if not isinstance(view.itemDelegate(), _ComboPopupItemDelegate):
            view.setItemDelegate(_ComboPopupItemDelegate(view))

    @staticmethod
    def _fit_popup_height(container: QFrame, view: QAbstractItemView) -> None:
        combo = container.parentWidget()
        row_count = view.model().rowCount()
        visible_rows = min(row_count, combo.maxVisibleItems()) if isinstance(combo, QComboBox) else row_count
        content_height = sum(view.sizeHintForRow(row) for row in range(visible_rows))
        missing_height = content_height - view.viewport().height()
        if missing_height <= 0:
            return

        available = container.screen().availableGeometry()
        target_height = min(container.height() + missing_height, available.height())
        target_y = min(container.y(), available.bottom() - target_height + 1)
        target_y = max(available.top(), target_y)
        container.setGeometry(container.x(), target_y, container.width(), target_height)


def _apply_windows_caption_style(widget: QWidget) -> None:
    if sys.platform != "win32" or not widget.isWindow():
        return
    try:
        dwmapi = ctypes.windll.dwmapi
        hwnd = ctypes.c_void_p(int(widget.winId()))
        dark_mode = ctypes.c_int(1)
        caption_color = ctypes.c_uint(0x00100D0B)
        white = ctypes.c_uint(0xFFFFFF)
        for attribute, value in (
            (_DWMWA_USE_IMMERSIVE_DARK_MODE, dark_mode),
            (_DWMWA_BORDER_COLOR, caption_color),
            (_DWMWA_CAPTION_COLOR, caption_color),
            (_DWMWA_TEXT_COLOR, white),
        ):
            dwmapi.DwmSetWindowAttribute(
                hwnd,
                attribute,
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
    except (AttributeError, OSError):
        return


def _transparent_window_icon_handle() -> int | None:
    global _TRANSPARENT_WINDOW_ICON_HANDLE
    if _TRANSPARENT_WINDOW_ICON_HANDLE is not None:
        return _TRANSPARENT_WINDOW_ICON_HANDLE
    try:
        gdi32 = ctypes.windll.gdi32
        user32 = ctypes.windll.user32
        gdi32.CreateBitmap.argtypes = (
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_void_p,
        )
        gdi32.CreateBitmap.restype = ctypes.c_void_p
        gdi32.DeleteObject.argtypes = (ctypes.c_void_p,)
        gdi32.DeleteObject.restype = ctypes.c_int
        user32.CreateIconIndirect.argtypes = (ctypes.POINTER(_IconInfo),)
        user32.CreateIconIndirect.restype = ctypes.c_void_p
        mask_bits = (ctypes.c_ubyte * 1)(0xFF)
        color_bits = (ctypes.c_ubyte * 4)(0, 0, 0, 0)
        mask = gdi32.CreateBitmap(1, 1, 1, 1, mask_bits)
        color = gdi32.CreateBitmap(1, 1, 1, 32, color_bits)
        if not mask or not color:
            return None
        icon_info = _IconInfo(1, 0, 0, mask, color)
        handle = user32.CreateIconIndirect(ctypes.byref(icon_info))
        gdi32.DeleteObject(mask)
        gdi32.DeleteObject(color)
        if not handle:
            return None
        _TRANSPARENT_WINDOW_ICON_HANDLE = int(handle)
        return _TRANSPARENT_WINDOW_ICON_HANDLE
    except (AttributeError, OSError):
        return None


def _hide_windows_main_window_caption_identity(widget: QWidget) -> None:
    if sys.platform != "win32" or not isinstance(widget, QMainWindow) or not widget.isWindow():
        return
    try:
        hwnd = ctypes.c_void_p(int(widget.winId()))
        user32 = ctypes.windll.user32
        transparent_icon = _transparent_window_icon_handle()
        if transparent_icon is None:
            return
        user32.SetWindowTextW(hwnd, "")
        user32.SendMessageW(hwnd, _WM_SETICON, _ICON_SMALL, transparent_icon)
        user32.SendMessageW(hwnd, _WM_SETICON, _ICON_BIG, transparent_icon)
    except (AttributeError, OSError):
        return


class _WindowsCaptionStyler(QObject):
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Show and isinstance(watched, QWidget):
            _apply_windows_caption_style(watched)
            QTimer.singleShot(
                0,
                lambda window=watched: _hide_windows_main_window_caption_identity(window),
            )
        return False


def apply_theme(app: QApplication) -> None:
    """Load dark.qss and set application-wide palette hints."""
    if _QSS_PATH.is_file():
        app.setStyleSheet(_QSS_PATH.read_text(encoding="utf-8"))
    app.setStyle("Fusion")
    styler = getattr(app, "_combo_popup_styler", None)
    if styler is None:
        styler = _ComboPopupStyler(app)
        app.installEventFilter(styler)
        app._combo_popup_styler = styler
    caption_styler = getattr(app, "_windows_caption_styler", None)
    if caption_styler is None:
        caption_styler = _WindowsCaptionStyler(app)
        app.installEventFilter(caption_styler)
        app._windows_caption_styler = caption_styler
    for widget in app.topLevelWidgets():
        _apply_windows_caption_style(widget)
        _hide_windows_main_window_caption_identity(widget)
