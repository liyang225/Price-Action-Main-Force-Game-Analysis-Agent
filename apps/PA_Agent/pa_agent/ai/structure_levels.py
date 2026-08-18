"""Deterministic support/resistance refresh for Stage 1 diagnosis."""
from __future__ import annotations

import logging
import math
import re
from typing import Any

from pa_agent.util.price_tick import infer_price_tick_from_frame

logger = logging.getLogger(__name__)

# Swing pivots for S/R refill: only scan recent bars (K1..K40), not full history.
_SWING_LOOKBACK_BARS = 40

_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _price_text(value: float, tick: float | None) -> str:
    if tick is None or tick <= 0:
        text = f"{value:.6f}".rstrip("0").rstrip(".")
        return "0" if text in ("", "-0") else text
    decimals = max(0, min(6, int(round(-math.log10(tick))) if tick < 1 else 0))
    text = f"{value:.{decimals}f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _parse_level_bounds(raw: Any) -> tuple[float, float] | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        v = float(raw)
        return (v, v) if v > 0 else None
    text = str(raw).strip()
    if not text:
        return None
    nums = [float(m) for m in _NUMBER.findall(text)]
    if not nums:
        return None
    lo, hi = min(nums), max(nums)
    return (lo, hi) if lo > 0 else None


def _level_mid(bounds: tuple[float, float]) -> float:
    lo, hi = bounds
    return (lo + hi) / 2.0


def _filter_valid_supports(
    levels: Any,
    close: float,
    *,
    tolerance: float = 0.0,
) -> list[str]:
    """Keep supports strictly below *close* (broken supports are dropped)."""
    if not isinstance(levels, list):
        return []
    valid: list[tuple[float, str]] = []
    for raw in levels:
        bounds = _parse_level_bounds(raw)
        if bounds is None:
            continue
        lo, hi = bounds
        if hi < close - tolerance:
            valid.append((_level_mid(bounds), str(raw)))
    valid.sort(key=lambda x: x[0], reverse=True)
    return [text for _, text in valid]


def _filter_valid_resistances(
    levels: Any,
    close: float,
    *,
    tolerance: float = 0.0,
) -> list[str]:
    """Keep resistances strictly above *close* (broken resistances are dropped)."""
    if not isinstance(levels, list):
        return []
    valid: list[tuple[float, str]] = []
    for raw in levels:
        bounds = _parse_level_bounds(raw)
        if bounds is None:
            continue
        lo, hi = bounds
        if lo > close + tolerance:
            valid.append((_level_mid(bounds), str(raw)))
    valid.sort(key=lambda x: x[0])
    return [text for _, text in valid]


def _is_swing_low(bars: tuple[Any, ...], idx: int) -> bool:
    low = float(bars[idx].low)
    if idx > 0 and low >= float(bars[idx - 1].low):
        return False
    if idx + 1 < len(bars) and low >= float(bars[idx + 1].low):
        return False
    return True


def _is_swing_high(bars: tuple[Any, ...], idx: int) -> bool:
    high = float(bars[idx].high)
    if idx > 0 and high <= float(bars[idx - 1].high):
        return False
    if idx + 1 < len(bars) and high <= float(bars[idx + 1].high):
        return False
    return True


def _recent_bars(bars: tuple[Any, ...], *, lookback: int = _SWING_LOOKBACK_BARS) -> tuple[Any, ...]:
    if lookback <= 0 or len(bars) <= lookback:
        return bars
    return tuple(bars[:lookback])


def _swing_support_prices(
    bars: tuple[Any, ...],
    close: float,
    *,
    max_levels: int = 3,
) -> list[float]:
    """Swing lows below *close*, nearest-first (highest low under price)."""
    window = _recent_bars(bars)
    candidates: list[float] = []
    for idx in range(len(window)):
        low = float(window[idx].low)
        if low >= close:
            continue
        if _is_swing_low(window, idx):
            candidates.append(low)
    if not candidates:
        for bar in window:
            low = float(bar.low)
            if low < close:
                candidates.append(low)
    dedup = sorted({round(v, 8) for v in candidates}, reverse=True)
    return dedup[:max_levels]


def _swing_resistance_prices(
    bars: tuple[Any, ...],
    close: float,
    *,
    max_levels: int = 3,
) -> list[float]:
    """Swing highs above *close*, nearest-first (lowest high above price)."""
    window = _recent_bars(bars)
    candidates: list[float] = []
    for idx in range(len(window)):
        high = float(window[idx].high)
        if high <= close:
            continue
        if _is_swing_high(window, idx):
            candidates.append(high)
    if not candidates:
        for bar in window:
            high = float(bar.high)
            if high > close:
                candidates.append(high)
    dedup = sorted({round(v, 8) for v in candidates})
    return dedup[:max_levels]


def _merge_level_texts(
    kept: list[str],
    swing_prices: list[float],
    *,
    tick: float | None,
    max_levels: int,
    kind: str,
) -> list[str]:
    """Merge AI levels with swing pivots and sort near → far (prompt contract)."""
    entries: list[tuple[float, str]] = []
    seen: set[float] = set()

    for raw in kept:
        bounds = _parse_level_bounds(raw)
        if bounds is None:
            continue
        key = round(_level_mid(bounds), 8)
        if key in seen:
            continue
        seen.add(key)
        entries.append((key, str(raw)))

    for price in swing_prices:
        key = round(price, 8)
        if key in seen:
            continue
        seen.add(key)
        entries.append((key, _price_text(price, tick)))

    if kind == "support":
        entries.sort(key=lambda item: item[0], reverse=True)
    elif kind == "resistance":
        entries.sort(key=lambda item: item[0])
    else:
        raise ValueError(f"unknown kind: {kind!r}")

    return _trim_levels_preserve_extremes(entries, max_levels)


def _trim_levels_preserve_extremes(
    entries: list[tuple[float, str]],
    max_levels: int,
) -> list[str]:
    """Keep nearest + farthest after sort; fill interior slots from the far side."""
    if max_levels <= 0 or not entries:
        return []
    if len(entries) <= max_levels:
        return [text for _, text in entries]

    nearest = entries[0]
    farthest = entries[-1]
    interior_slots = max_levels - 2
    if interior_slots <= 0:
        return [nearest[1], farthest[1]][:max_levels]

    interior = entries[1:-1]
    picked: list[tuple[float, str]] = [nearest]
    if interior_slots > 0 and interior:
        # Prefer interior levels closer to the far end (structural S/R over micro swings).
        step = max(1, len(interior) // interior_slots)
        for slot in range(interior_slots):
            idx = min(len(interior) - 1, len(interior) - 1 - slot * step)
            candidate = interior[idx]
            if candidate not in picked:
                picked.append(candidate)
            if len(picked) >= max_levels - 1:
                break
    if farthest not in picked:
        picked.append(farthest)
    return [text for _, text in picked[:max_levels]]


def refresh_stage1_support_resistance(
    stage1: dict[str, Any],
    kline_frame: Any,
    *,
    max_levels: int = 3,
) -> bool:
    """Drop broken S/R levels and refill from recent swing structure.

    ``bars`` on *kline_frame* are newest-first (K1 = latest closed). A support
    below price must satisfy ``high < close``; resistance above price must
    satisfy ``low > close``. Levels on the wrong side after a breakout are
    removed and replaced with swing pivots from the current window.
    """
    bars = getattr(kline_frame, "bars", None) if kline_frame is not None else None
    if not bars:
        return False

    try:
        close = float(bars[0].close)
    except (TypeError, ValueError, IndexError):
        return False
    if close <= 0:
        return False

    tick = infer_price_tick_from_frame(kline_frame)
    tolerance = (tick or 0.0) * 0.5

    old_sup = list(stage1.get("support_levels") or [])
    old_res = list(stage1.get("resistance_levels") or [])

    kept_sup = _filter_valid_supports(old_sup, close, tolerance=tolerance)
    kept_res = _filter_valid_resistances(old_res, close, tolerance=tolerance)

    swing_sup = _swing_support_prices(tuple(bars), close, max_levels=max_levels)
    swing_res = _swing_resistance_prices(tuple(bars), close, max_levels=max_levels)

    new_sup = _merge_level_texts(
        kept_sup, swing_sup, tick=tick, max_levels=max_levels, kind="support"
    )
    new_res = _merge_level_texts(
        kept_res, swing_res, tick=tick, max_levels=max_levels, kind="resistance"
    )

    changed = new_sup != old_sup or new_res != old_res
    if changed:
        if old_sup != new_sup:
            logger.info(
                "support_levels refreshed for close=%.4f: %s -> %s",
                close,
                old_sup,
                new_sup,
            )
        if old_res != new_res:
            logger.info(
                "resistance_levels refreshed for close=%.4f: %s -> %s",
                close,
                old_res,
                new_res,
            )
        stage1["support_levels"] = new_sup
        stage1["resistance_levels"] = new_res
    return changed
