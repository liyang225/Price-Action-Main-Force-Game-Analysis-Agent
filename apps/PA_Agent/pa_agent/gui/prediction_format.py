"""Shared display helpers for next-bar and next-cycle prediction panels.

Extracted from decision_panel.py so that FutureTrendPanel can import them
without creating a circular dependency.
"""
from __future__ import annotations

from pa_agent.gui.theme import tokens as T

# ── Colour constants ──────────────────────────────────────────────────────────
# CN convention: bullish/阳线 → red, bearish/阴线 → green, neutral → muted.

_PREDICTION_DOMINANT_COLOR: dict[str, str] = {
    "bullish": T.MKT_UP,
    "bearish": T.MKT_DOWN,
    "neutral": T.FG_2,
}

_PREDICTION_UNPREDICTABLE_COLOR: str = T.FG_3
_PREDICTION_UNPREDICTABLE_LABEL: str = "不可预测"


# ── Formatting helpers ────────────────────────────────────────────────────────

def _format_prediction_probs_line(probs: dict) -> str:
    """Format bullish/bearish/neutral probabilities as a single display line."""
    bull = probs.get("bullish", "?")
    bear = probs.get("bearish", "?")
    neut = probs.get("neutral", "?")
    return f"阳线的概率为{bull}%  ·  阴线的概率为{bear}%  ·  中性的概率为{neut}%"


def _format_prediction_probs_html(probs: dict) -> str:
    """Render neutral probabilities, highlighting only the largest value."""
    values = []
    for key, label in (("bullish", "阳线"), ("bearish", "阴线"), ("neutral", "中性")):
        raw = probs.get(key, "?")
        try:
            numeric = float(raw)
        except (TypeError, ValueError):
            numeric = float("-inf")
        values.append((key, label, raw, numeric))
    maximum = max((item[3] for item in values), default=float("-inf"))
    parts = []
    for _key, label, raw, numeric in values:
        color = T.ACCENT if numeric == maximum else T.FG
        parts.append(f"{label}的概率为<span style='color:{color};'>{raw}%</span>")
    return "  ·  ".join(parts)


def _dominant_prediction_direction(probs: dict) -> str | None:
    """Return bullish/bearish/neutral for styling by highest probability."""
    parsed: list[tuple[str, float]] = []
    for key in ("bullish", "bearish", "neutral"):
        raw = probs.get(key)
        if raw is None or raw == "":
            continue
        try:
            parsed.append((key, float(raw)))
        except (TypeError, ValueError):
            continue
    if not parsed:
        return None
    return max(parsed, key=lambda item: item[1])[0]
