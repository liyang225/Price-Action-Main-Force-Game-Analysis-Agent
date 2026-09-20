from __future__ import annotations

import ctypes
from importlib import import_module
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication, QDialog, QMainWindow, QWidget


def test_apply_theme_styles_existing_and_new_top_level_windows(qtbot) -> None:
    from pa_agent.gui.theme.apply import apply_theme

    app = QApplication.instance()
    assert app is not None
    existing = QWidget()
    qtbot.addWidget(existing)

    with patch("pa_agent.gui.theme.apply._apply_windows_caption_style") as apply_caption:
        apply_theme(app)
        dialog = QDialog()
        qtbot.addWidget(dialog)
        dialog.show()
        app.processEvents()

    assert existing in [call.args[0] for call in apply_caption.call_args_list]
    assert dialog in [call.args[0] for call in apply_caption.call_args_list]


def test_windows_caption_style_uses_requested_caption_and_white_text(monkeypatch) -> None:
    theme_apply = import_module("pa_agent.gui.theme.apply")

    widget = MagicMock()
    widget.isWindow.return_value = True
    widget.winId.return_value = 123
    dwmapi = MagicMock()
    monkeypatch.setattr(theme_apply.sys, "platform", "win32")
    monkeypatch.setattr(theme_apply.ctypes, "windll", MagicMock(dwmapi=dwmapi), raising=False)

    theme_apply._apply_windows_caption_style(widget)

    attributes = [call.args[1] for call in dwmapi.DwmSetWindowAttribute.call_args_list]
    assert attributes == [20, 34, 35, 36]
    colors = [
        ctypes.cast(call.args[2], ctypes.POINTER(ctypes.c_uint)).contents.value
        for call in dwmapi.DwmSetWindowAttribute.call_args_list[1:3]
    ]
    assert colors == [0x00100D0B, 0x00100D0B]


def test_main_window_caption_text_is_hidden_without_replacing_icons(qtbot, monkeypatch) -> None:
    theme_apply = import_module("pa_agent.gui.theme.apply")
    main_window = QMainWindow()
    dialog = QDialog()
    qtbot.addWidget(main_window)
    qtbot.addWidget(dialog)
    user32 = MagicMock()
    monkeypatch.setattr(theme_apply.sys, "platform", "win32")
    monkeypatch.setattr(
        theme_apply.ctypes,
        "windll",
        MagicMock(user32=user32),
        raising=False,
    )

    theme_apply._hide_windows_main_window_caption_text(main_window)
    theme_apply._hide_windows_main_window_caption_text(dialog)

    user32.SetWindowTextW.assert_called_once()
    user32.SendMessageW.assert_not_called()


def test_application_icon_survives_theme_and_window_reshow(qtbot, monkeypatch) -> None:
    from pa_agent.util.app_icon import install_app_icon

    theme_apply = import_module("pa_agent.gui.theme.apply")
    app = QApplication.instance()
    assert app is not None
    icon = install_app_icon(app)
    assert not icon.isNull()
    window = QMainWindow()
    qtbot.addWidget(window)
    user32 = MagicMock()
    monkeypatch.setattr(theme_apply.sys, "platform", "win32")
    monkeypatch.setattr(theme_apply.ctypes, "windll", MagicMock(user32=user32), raising=False)

    theme_apply.apply_theme(app)
    for _ in range(2):
        window.show()
        app.processEvents()
        assert window.windowIcon().cacheKey() == icon.cacheKey()
        assert not window.windowIcon().pixmap(16, 16).isNull()
        window.hide()
    # Qt can still report the correct icon if native WM_SETICON overrides it.
    # Guard the native boundary as well as the Qt property.
    user32.SendMessageW.assert_not_called()
    assert user32.SetWindowTextW.called
