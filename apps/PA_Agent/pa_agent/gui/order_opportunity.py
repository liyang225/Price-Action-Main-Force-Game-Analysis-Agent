"""Detect stage-2 order opportunities and format alert text."""
from __future__ import annotations

import logging
import os
import sys
from typing import Any

from PyQt6.QtCore import Qt

logger = logging.getLogger(__name__)

ORDER_OPPORTUNITY_TYPES: frozenset[str] = frozenset({"限价单", "突破单", "市价单"})


def _parse_trade_confidence(decision: dict[str, Any]) -> int | None:
    """Extract trade_confidence as 0-100 int, or None if absent/invalid."""
    raw = decision.get("trade_confidence")
    if raw is None or raw == "":
        return None
    try:
        return max(0, min(100, int(float(str(raw).strip()))))
    except (ValueError, TypeError):
        return None


def has_order_opportunity(
    decision: dict[str, Any] | None,
    *,
    confidence_threshold: int | None = None,
) -> bool:
    """Return True when stage-2 decision proposes an actual order.

    When *confidence_threshold* is provided, the decision is only treated as
    an order opportunity when ``trade_confidence >= confidence_threshold``.
    """
    if not isinstance(decision, dict):
        return False
    if str(decision.get("order_type") or "") not in ORDER_OPPORTUNITY_TYPES:
        return False
    # Confidence gate: if threshold set, require trade_confidence >= threshold
    if confidence_threshold is not None and confidence_threshold > 0:
        conf = _parse_trade_confidence(decision)
        if conf is None or conf < confidence_threshold:
            return False
    return True


def _fmt_price(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value)


def format_order_alert_message(decision: dict[str, Any]) -> str:
    """Short summary for the order-opportunity popup."""
    direction = decision.get("order_direction") or "—"
    order_type = decision.get("order_type") or "—"
    entry = _fmt_price(decision.get("entry_price"))
    stop = _fmt_price(decision.get("stop_loss_price"))
    target = _fmt_price(decision.get("take_profit_price"))
    target2 = _fmt_price(decision.get("take_profit_price_2"))
    reasoning = str(decision.get("reasoning") or "").strip()
    lines = [
        f"方向：{direction}",
        f"方式：{order_type}",
        f"入场：{entry}",
        f"止损：{stop}",
        f"TP1：{target}",
        f"TP2：{target2}",
    ]
    if reasoning:
        preview = reasoning if len(reasoning) <= 200 else reasoning[:200] + "…"
        lines.append("")
        lines.append(preview)
    lines.append("")
    lines.append("已切换到「决策」页，请核对详情。")
    return "\n".join(lines)


def _windows_alert_wav_paths() -> list[str]:
    media = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Media")
    names = (
        "notify.wav",
        "Windows Notify.wav",
        "Alarm01.wav",
        "Windows Exclamation.wav",
    )
    return [os.path.join(media, name) for name in names]


ORDER_ALERT_AUTO_CLOSE_MS = 120_000


def show_order_opportunity_alert(parent: Any, decision: dict[str, Any]) -> None:
    """Non-modal alert that auto-closes after :data:`ORDER_ALERT_AUTO_CLOSE_MS`."""
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QMessageBox

    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Information)
    box.setWindowTitle("下单机会")
    box.setText(format_order_alert_message(decision))
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    timer = QTimer(box)
    timer.setSingleShot(True)
    timer.timeout.connect(box.accept)
    timer.start(ORDER_ALERT_AUTO_CLOSE_MS)
    # Non-modal: avoid blocking the main event loop after analysis completes.
    box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
    box.show()


def play_order_alert_sound() -> bool:
    """Play a short alert sound (best-effort). Returns True if playback was attempted."""
    if sys.platform == "win32":
        import winsound

        for path in _windows_alert_wav_paths():
            if not os.path.isfile(path):
                continue
            try:
                winsound.PlaySound(
                    path,
                    winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
                )
                return True
            except Exception as exc:
                logger.debug("order alert PlaySound file %s failed: %s", path, exc)

        for alias in ("SystemExclamation", "SystemHand", "SystemAsterisk"):
            try:
                winsound.PlaySound(
                    alias,
                    winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
                )
                return True
            except Exception as exc:
                logger.debug("order alert PlaySound alias %s failed: %s", alias, exc)

        try:
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            return True
        except Exception as exc:
            logger.debug("order alert MessageBeep failed: %s", exc)

    try:
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            app.beep()
            return True
    except Exception as exc:
        logger.debug("order alert QApplication.beep failed: %s", exc)

    return False
