"""Single source of truth for market cycle enumerations, labels, and display helpers.

Used by: prompt assembler, JSON schema, stage2 normalizer, JSON validator, and GUI panels.
"""
from __future__ import annotations

# ── Cycle enum order (used for argmax tie-breaking — first wins) ──────────────

CYCLE_ORDER: tuple[str, ...] = (
    "spike",
    "micro_channel",
    "tight_channel",
    "normal_channel",
    "broad_channel",
    "trending_tr",
    "trading_range",
    "extreme_tr",
)

# Predictable cycle values (excludes "unknown")
CYCLE_ENUM: tuple[str, ...] = CYCLE_ORDER

# ── Chinese display labels ────────────────────────────────────────────────────

CYCLE_POSITION_ZH: dict[str, str] = {
    "spike": "尖峰 (Spike)",
    "micro_channel": "微型通道",
    "tight_channel": "窄通道",
    "normal_channel": "正常通道",
    "broad_channel": "宽通道",
    "trending_tr": "趋势型交易区间",
    "trading_range": "交易区间",
    "extreme_tr": "极端交易区间",
    "unknown": "未知",
}

# Direction → Chinese prefix for cycle labels
_DIRECTION_PREFIX_ZH: dict[str, str] = {
    "bullish": "上涨",
    "bearish": "下跌",
    "neutral": "震荡",
}

# Range-style cycles: structure is sideways; direction refines the bias.
RANGE_DISPLAY_CYCLES: frozenset[str] = frozenset(
    {"trading_range", "extreme_tr", "trending_tr"}
)


# ── Public helpers ────────────────────────────────────────────────────────────

def format_cycle_position(raw: str) -> str:
    """Return the Chinese display text for a cycle enum value.

    Falls back to the raw value (or '—' for empty/None) for unknown keys.
    Pure function — does not modify any argument.
    """
    key = (raw or "").strip().lower()
    if not key:
        return "—"
    return CYCLE_POSITION_ZH.get(key, raw)


def format_cycle_with_direction(
    cycle_position: str,
    direction: str | None,
) -> str:
    """Return the Chinese display text for a cycle, with an optional direction prefix.

    The prefix (上涨 / 下跌 / 震荡) is applied to all known cycle enums except "unknown":
      - bullish + broad_channel → "上涨宽通道"
      - bearish + trending_tr   → "下跌趋势型交易区间"
      - neutral + trading_range → "震荡交易区间"
      - missing/other direction → no prefix

    Pure function — does not modify any argument.
    """
    base = format_cycle_position(cycle_position)
    cp = (cycle_position or "").strip().lower()
    if not cp or cp == "unknown":
        return base
    d = (direction or "").strip().lower()
    prefix = _DIRECTION_PREFIX_ZH.get(d, "")
    return f"{prefix}{base}" if prefix else base


def format_trend_label(
    direction: str | None,
    cycle_position: str | None,
) -> str:
    """Map direction + cycle to a short trend label for the GUI.

    Range cycles keep the sideways structure but expose directional bias:
      - bearish + trading_range → 震荡偏空
      - bullish + trading_range → 震荡偏多
      - neutral/missing + trading_range → 震荡

    Channel/spike cycles use directional trend labels when available.
    """
    cp = (cycle_position or "").strip().lower()
    d = (direction or "").strip().lower()

    if cp in RANGE_DISPLAY_CYCLES:
        if d == "bullish":
            return "震荡偏多"
        if d == "bearish":
            return "震荡偏空"
        return "震荡"

    if d == "bullish":
        return "上涨"
    if d == "bearish":
        return "下跌"
    if d == "neutral":
        return "震荡"

    if cp in ("spike", "micro_channel", "tight_channel"):
        return "趋势运行中"
    return "—"
