"""Extract support/resistance levels from AI decision payloads."""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class StructureLevel:
    """A horizontal support/resistance level or price zone."""

    kind: str  # support | resistance
    low: float
    high: float
    label: str = ""

    @property
    def price(self) -> float:
        return (self.low + self.high) / 2.0

    @property
    def is_zone(self) -> bool:
        return abs(self.high - self.low) > 1e-9


_NUMBER = r"\d+(?:\.\d+)?"
_RANGE_RE = re.compile(rf"({_NUMBER})\s*[-~－—到至]\s*({_NUMBER})")
_SINGLE_RE = re.compile(_NUMBER)
_TEXT_SPLIT_RE = re.compile(r"[。；;，,\n]")


def extract_structure_levels(payload: Any, *, max_levels_per_kind: int = 3) -> list[StructureLevel]:
    """Extract support/resistance levels from structured fields and text.

    Accepts both structured JSON keys (``support``, ``resistance``) and
    narrative text such as ``支撑依次为4270-4280、4250-4260``.

    Fields that describe historical context (``htf_context``, ``bar_by_bar_summary``)
    are intentionally skipped — they contain historical swing highs/lows that are
    far from the current price range and would produce misleading chart lines.
    """
    found: list[StructureLevel] = []
    _walk(payload, found)
    return _dedupe(found, max_levels_per_kind=max_levels_per_kind)


# Keys whose values contain historical price descriptions that should NOT
# be scanned for support/resistance levels.
_SKIP_KEYS: frozenset[str] = frozenset({
    "htf_context",
    "bar_by_bar_summary",
    "reasoning",
    "diagnosis_confidence_reasoning",
    "trade_confidence_reasoning",
    "estimated_win_rate_reasoning",
    "invalidation_condition",
})


def filter_levels_near_price(
    levels: list[StructureLevel],
    bars: Iterable[Any],
    *,
    max_levels_per_kind: int = 3,
) -> list[StructureLevel]:
    """Keep only levels in the same price neighborhood as the current bars."""
    highs: list[float] = []
    lows: list[float] = []
    for bar in bars:
        try:
            highs.append(float(getattr(bar, "high")))
            lows.append(float(getattr(bar, "low")))
        except (TypeError, ValueError):
            continue
    if not highs or not lows:
        return _dedupe(levels, max_levels_per_kind=max_levels_per_kind)

    price_low = min(lows)
    price_high = max(highs)
    mids = [(lo + hi) / 2.0 for lo, hi in zip(lows, highs, strict=False) if hi > 0 and lo > 0]
    mid = statistics.median(mids) if mids else (price_low + price_high) / 2.0
    span = max(price_high - price_low, abs(mid) * 0.01, 1.0)
    # Use a tighter 8 % margin of the current mid-price rather than 20 %,
    # to exclude historical levels that are far outside the visible price range.
    pad = max(span * 3.0, abs(mid) * 0.08, 1.0)
    allowed_low = price_low - pad
    allowed_high = price_high + pad
    ratio_low = mid * 0.85 if mid > 0 else allowed_low
    ratio_high = mid * 1.15 if mid > 0 else allowed_high

    filtered = [
        level
        for level in levels
        if level.low > 0
        and allowed_low <= level.low <= allowed_high
        and allowed_low <= level.high <= allowed_high
        and ratio_low <= level.low <= ratio_high
        and ratio_low <= level.high <= ratio_high
    ]
    return _dedupe(filtered, max_levels_per_kind=max_levels_per_kind)


def format_level(level: StructureLevel) -> str:
    if level.is_zone:
        return f"{_fmt(level.low)}-{_fmt(level.high)}"
    return _fmt(level.price)


def levels_from_stage1_diagnosis(
    stage1: dict[str, Any] | None,
    *,
    max_per_kind: int = 3,
) -> list[StructureLevel]:
    """Build chart levels from stage1 ``support_levels`` / ``resistance_levels``.

    Arrays are ordered **near → far** (per prompt contract). Index 0 is the
    nearest level to current price; we preserve that order in labels.
    """
    if not isinstance(stage1, dict):
        return []
    out: list[StructureLevel] = []
    supports = stage1.get("support_levels") or []
    resistances = stage1.get("resistance_levels") or []
    if not isinstance(supports, list):
        supports = []
    if not isinstance(resistances, list):
        resistances = []

    for i, raw in enumerate(supports[:max_per_kind]):
        parsed = _parse_prices(str(raw), "support")
        if not parsed:
            continue
        base = parsed[0]
        label = "支撑" if i == 0 else f"支撑{i + 1}"
        out.append(StructureLevel(base.kind, base.low, base.high, label))

    for i, raw in enumerate(resistances[:max_per_kind]):
        parsed = _parse_prices(str(raw), "resistance")
        if not parsed:
            continue
        base = parsed[0]
        label = "阻力" if i == 0 else f"阻力{i + 1}"
        out.append(StructureLevel(base.kind, base.low, base.high, label))
    return out


def chart_levels_from_stage1_diagnosis(
    stage1: dict[str, Any] | None,
) -> list[StructureLevel]:
    """Return one support + one resistance line for the chart (farthest from price)."""
    if not isinstance(stage1, dict):
        return []
    supports = stage1.get("support_levels") or []
    resistances = stage1.get("resistance_levels") or []
    if not isinstance(supports, list):
        supports = []
    if not isinstance(resistances, list):
        resistances = []

    out: list[StructureLevel] = []
    far_support = _farthest_level(supports, "support")
    if far_support is not None:
        out.append(far_support)
    far_resistance = _farthest_level(resistances, "resistance")
    if far_resistance is not None:
        out.append(far_resistance)
    return out


def _farthest_level(raw_levels: list[Any], kind: str) -> StructureLevel | None:
    """Pick the structurally farthest level: lowest support / highest resistance."""
    candidates: list[tuple[float, StructureLevel]] = []
    for raw in raw_levels:
        for level in _parse_prices(str(raw), kind):
            candidates.append((level.price, level))
    if not candidates:
        return None
    if kind == "support":
        _, base = min(candidates, key=lambda item: item[0])
        label = "支撑"
    else:
        _, base = max(candidates, key=lambda item: item[0])
        label = "阻力"
    return StructureLevel(base.kind, base.low, base.high, label)


def nearest_support_resistance_labels(
    stage1: dict[str, Any] | None,
) -> tuple[str, str]:
    """Return (nearest_support_text, nearest_resistance_text) for summary UI."""
    if not isinstance(stage1, dict):
        return "—", "—"
    supports = stage1.get("support_levels") or []
    resistances = stage1.get("resistance_levels") or []
    if not isinstance(supports, list):
        supports = []
    if not isinstance(resistances, list):
        resistances = []

    sup_text = "—"
    if supports:
        parsed = _parse_prices(str(supports[0]), "support")
        sup_text = format_level(parsed[0]) if parsed else str(supports[0])

    res_text = "—"
    if resistances:
        parsed = _parse_prices(str(resistances[0]), "resistance")
        res_text = format_level(parsed[0]) if parsed else str(resistances[0])

    return sup_text, res_text


def _walk(node: Any, found: list[StructureLevel], *, key_hint: str = "") -> None:
    # Skip fields that contain historical descriptions rather than current S/R levels
    if key_hint in _SKIP_KEYS:
        return
    if isinstance(node, dict):
        kind = _kind_from_key(key_hint)
        if kind is not None:
            found.extend(_levels_from_value(node, kind))
        for key, value in node.items():
            child_hint = str(key)
            # Skip known non-S/R fields
            if child_hint in _SKIP_KEYS:
                continue
            child_kind = _kind_from_key(child_hint)
            if child_kind is not None:
                found.extend(_levels_from_value(value, child_kind))
            _walk(value, found, key_hint=child_hint)
        return

    if isinstance(node, list):
        kind = _kind_from_key(key_hint)
        if kind is not None:
            for item in node:
                found.extend(_levels_from_value(item, kind))
        for item in node:
            _walk(item, found, key_hint=key_hint)
        return

    if isinstance(node, str):
        found.extend(_levels_from_text(node))


def _kind_from_key(key: str) -> str | None:
    text = key.lower()
    if "support" in text or "支撑" in text:
        return "support"
    if "resistance" in text or "阻力" in text or "关键上破" in text:
        return "resistance"
    return None


def _levels_from_value(value: Any, kind: str) -> list[StructureLevel]:
    if isinstance(value, (int, float)):
        return [_level(kind, float(value), float(value))]
    if isinstance(value, str):
        return _parse_prices(value, kind)
    if isinstance(value, dict):
        low = _first_number(value, ("low", "min", "lower", "start", "from", "bottom"))
        high = _first_number(value, ("high", "max", "upper", "end", "to", "top"))
        price = _first_number(value, ("price", "level", "value"))
        if low is not None and high is not None:
            return [_level(kind, low, high)]
        if price is not None:
            return [_level(kind, price, price)]
        return []
    if isinstance(value, Iterable):
        levels: list[StructureLevel] = []
        for item in value:
            levels.extend(_levels_from_value(item, kind))
        return levels
    return []


def _levels_from_text(text: str) -> list[StructureLevel]:
    levels: list[StructureLevel] = []
    for part in _TEXT_SPLIT_RE.split(text):
        kind = None
        if "支撑" in part or "support" in part.lower():
            kind = "support"
        elif "阻力" in part or "resistance" in part.lower() or "关键上破" in part:
            kind = "resistance"
        if kind is not None:
            if _has_negated_level_context(part, kind):
                continue
            levels.extend(_parse_prices(part, kind))
    return levels


def _has_negated_level_context(text: str, kind: str) -> bool:
    lowered = text.lower()
    if kind == "support":
        return any(token in text for token in ("不是支撑", "非支撑", "不是支撑价位")) or (
            "not support" in lowered
        )
    return any(token in text for token in ("不是阻力", "非阻力", "不是阻力价位")) or (
        "not resistance" in lowered
    )


def _parse_prices(text: str, kind: str) -> list[StructureLevel]:
    levels: list[StructureLevel] = []
    consumed: list[tuple[int, int]] = []
    for match in _RANGE_RE.finditer(text):
        if _is_non_price_match(text, *match.span()):
            continue
        low = float(match.group(1))
        high = float(match.group(2))
        levels.append(_level(kind, low, high))
        consumed.append(match.span())

    def _inside_consumed(start: int, end: int) -> bool:
        return any(start >= a and end <= b for a, b in consumed)

    for match in _SINGLE_RE.finditer(text):
        if _inside_consumed(*match.span()):
            continue
        if _is_non_price_match(text, *match.span()):
            continue
        levels.append(_level(kind, float(match.group(0)), float(match.group(0))))
    return levels


def _is_non_price_match(text: str, start: int, end: int) -> bool:
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    if before in ("K", "k"):
        return True
    if after == "%":
        return True
    return False


def _first_number(data: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    lowered = {str(k).lower(): v for k, v in data.items()}
    for key in keys:
        raw = lowered.get(key)
        if isinstance(raw, (int, float)):
            return float(raw)
        if isinstance(raw, str):
            match = _SINGLE_RE.search(raw)
            if match:
                return float(match.group(0))
    return None


def _level(kind: str, a: float, b: float) -> StructureLevel:
    low, high = sorted((float(a), float(b)))
    return StructureLevel(kind=kind, low=low, high=high)


def _dedupe(levels: list[StructureLevel], *, max_levels_per_kind: int) -> list[StructureLevel]:
    out: list[StructureLevel] = []
    counts = {"support": 0, "resistance": 0}
    seen: set[tuple[str, float, float]] = set()
    for level in levels:
        if level.kind not in counts or counts[level.kind] >= max_levels_per_kind:
            continue
        key = (level.kind, round(level.low, 6), round(level.high, 6))
        if key in seen:
            continue
        seen.add(key)
        counts[level.kind] += 1
        label = level.label or ("支撑" if level.kind == "support" else "阻力")
        out.append(StructureLevel(level.kind, level.low, level.high, label))
    return out


def _fmt(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text
