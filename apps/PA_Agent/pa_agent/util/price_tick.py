"""Infer minimum price increment from K-line OHLC precision."""
from __future__ import annotations

import re
from typing import Any


def infer_price_tick_from_frame(kline_frame: Any) -> float | None:
    """Guess one tick from decimal places in the snapshot (e.g. XAU 0.01 or 0.001)."""
    bars = getattr(kline_frame, "bars", None) if kline_frame is not None else None
    if not bars:
        return None

    max_decimals = 0
    for bar in bars:
        for attr in ("open", "high", "low", "close"):
            try:
                value = float(getattr(bar, attr))
            except (TypeError, ValueError):
                continue
            text = f"{value:.12f}".rstrip("0")
            if "." in text:
                max_decimals = max(max_decimals, len(text.split(".")[1]))

    if max_decimals <= 0:
        return 1.0
    return 10 ** (-min(max_decimals, 6))


def round_to_tick(price: float, tick: float) -> float:
    if tick <= 0:
        return price
    return round(round(price / tick) * tick, 10)


def parse_k_seq(value: object) -> int | None:
    if value is None:
        return None
    m = re.search(r"K\s*(\d+)", str(value), flags=re.IGNORECASE)
    return int(m.group(1)) if m else None


def bar_by_seq(kline_frame: Any, seq: int) -> Any | None:
    for bar in getattr(kline_frame, "bars", ()) or ():
        if getattr(bar, "seq", None) == seq:
            return bar
    return None


def canonical_breakout_extreme(order_direction: str) -> str | None:
    """Return schema-correct entry_basis_extreme for a breakout order."""
    direction = str(order_direction or "").strip()
    if direction == "做多":
        return "high"
    if direction == "做空":
        return "low"
    return None


def normalize_breakout_basis_extreme(decision: dict[str, Any]) -> bool:
    """Align entry_basis_extreme with order_direction (做空→low, 做多→high)."""
    if decision.get("order_type") != "突破单":
        return False
    want = canonical_breakout_extreme(str(decision.get("order_direction", "") or ""))
    if not want:
        return False
    have = str(decision.get("entry_basis_extreme", "") or "").strip().lower()
    if have == want:
        return False
    decision["entry_basis_extreme"] = want
    return True


def breakout_entry_target(
    *,
    direction: str,
    extreme: str,
    basis_high: float,
    basis_low: float,
    tick: float,
) -> float | None:
    """Return the minimum valid breakout entry (strictly outside the cited extreme)."""
    if direction == "做多" and extreme == "high":
        return round_to_tick(basis_high + tick, tick)
    if direction == "做空" and extreme == "low":
        return round_to_tick(basis_low - tick, tick)
    return None


def normalize_breakout_entry_price(
    decision: dict[str, Any],
    *,
    kline_frame: Any = None,
    tick: float | None = None,
) -> bool:
    """Force entry_price to basis extreme ± 1 tick for breakout orders.

    Always recomputes entry_price from the cited entry_basis_bar's extreme,
    regardless of what the AI provided. This prevents AI hallucinations where
    the model references one bar's seq but uses price data from a different bar
    — the entry_basis_bar is the single source of truth.

    Returns True when entry_price was adjusted (or recomputed to same value).
    """
    if decision.get("order_type") != "突破单":
        return False
    if kline_frame is None:
        return False

    basis_seq = parse_k_seq(decision.get("entry_basis_bar"))
    if basis_seq is None:
        return False
    bar = bar_by_seq(kline_frame, basis_seq)
    if bar is None:
        return False

    direction = str(decision.get("order_direction", "") or "")
    extreme = str(decision.get("entry_basis_extreme", "") or "")
    step = tick if tick and tick > 0 else infer_price_tick_from_frame(kline_frame) or 0.01

    target = breakout_entry_target(
        direction=direction,
        extreme=extreme,
        basis_high=float(bar.high),
        basis_low=float(bar.low),
        tick=step,
    )
    if target is None:
        return False

    try:
        current = float(decision.get("entry_price"))
    except (TypeError, ValueError):
        current = None

    if current == target:
        return False

    decision["entry_price"] = target
    return True


def format_breakout_tick_hint(kline_frame: Any) -> str:
    """One-line Stage-2 user hint with inferred tick and formula."""
    tick = infer_price_tick_from_frame(kline_frame)
    if tick is None:
        return ""
    tick_s = f"{tick:g}"
    return (
        f"**突破单定价（程序推断最小跳动 ≈ {tick_s}）**：做多时 "
        f"`entry_price` 必须 **严格大于** `entry_basis_bar` 的 high，"
        f"推荐 `entry_price = 该 K 线 high + {tick_s}`（禁止等于 high）；"
        f"做空时 `entry_price` 必须 **严格低于** low，推荐 `low - {tick_s}`。"
        f"`entry_rule` 必须写明：`K{{n}} low/high = {{实际价格}}，entry = {{实际价格}} ± {tick_s}`，"
        f"勿重复 order_type/方向长句。"
        f"**程序会用 entry_basis_bar 对应棒的极点重算 entry_price，忽略你给的数值——"
        f"请确保 entry_basis_bar 序号与你实际引用的 K 线一致。**"
    )
