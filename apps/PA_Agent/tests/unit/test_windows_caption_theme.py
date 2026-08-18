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


def test_main_window_caption_identity_is_hidden_without_affecting_dialogs(qtbot, monkeypatch) -> None:
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
    monkeypatch.setattr(theme_apply, "_transparent_window_icon_handle", lambda: 123)

    theme_apply._hide_windows_main_window_caption_identity(main_window)
    theme_apply._hide_windows_main_window_caption_identity(dialog)

    user32.SetWindowTextW.assert_called_once()
    assert user32.SendMessageW.call_count == 2
    assert [call.args[2] for call in user32.SendMessageW.call_args_list] == [0, 1]
    assert [call.args[3] for call in user32.SendMessageW.call_args_list] == [123, 123]
