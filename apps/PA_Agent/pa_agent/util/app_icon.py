"""Application icon installation (window title bar + Windows taskbar).

Assets live under <project>/assets and are produced by tools/build_icon.py:
- pa-agent.ico          multi-size icon (16/24/32/48/64/128/256), preferred
- pa-agent-icon.svg     256px master, fallback when the .ico is missing
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

logger = logging.getLogger(__name__)

_ASSETS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "assets",
)

# Stable AppUserModelID so Windows groups taskbar buttons and shows our icon
# instead of the python.exe / pythonw.exe default.
_APP_USER_MODEL_ID = "PAAgent.PAAgent.Desktop"


def _set_app_user_model_id() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_APP_USER_MODEL_ID)
    except Exception:  # pragma: no cover - defensive, never block startup
        logger.debug("SetCurrentProcessExplicitAppUserModelID failed", exc_info=True)


def install_app_icon(app: QApplication) -> QIcon:
    """Set the application window icon; returns the icon (possibly null)."""
    _set_app_user_model_id()

    ico_path = os.path.join(_ASSETS_DIR, "pa-agent.ico")
    svg_path = os.path.join(_ASSETS_DIR, "pa-agent-icon.svg")

    icon = QIcon()
    if os.path.isfile(ico_path):
        icon = QIcon(ico_path)
    elif os.path.isfile(svg_path):
        icon = QIcon(svg_path)

    if icon.isNull():
        logger.warning("No application icon found in %s", _ASSETS_DIR)
    else:
        app.setWindowIcon(icon)
    return icon
