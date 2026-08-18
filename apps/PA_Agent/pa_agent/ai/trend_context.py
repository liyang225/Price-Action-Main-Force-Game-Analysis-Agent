"""Brooks-aligned trend context: background vs recent direction, spike detection."""
from __future__ import annotations

import math
from typing import Any

from pa_agent.ai.decision_nodes import (
    DIRECTION_BEAR_THRESHOLD,
    DIRECTION_BULL_THRESHOLD,
    EMA_SLOPE_LOOKBACK,
    OVERLAP_LOW_THRESHOLD,
    TREND_BAR_DOMINANCE_RATIO,
    _count_trend_bars,
    _find_swings,
    _mean_overlap_ratio,
)

# K41+ = background (major) structure; K40-K1 = recent; K8-K1 = inertia/spike
BACKGROUND_BAR_START_IDX: int = 40
RECENT_STRUCT_BARS: int = 40
SPIKE_NEAR_WINDOW: int = 8
SPIKE_MIN_TREND_BARS: int = 3
SPIKE_OVERLAP_MAX: float = 0.35

_WITH_TREND_ZH: dict[str, str] = {
    "bullish": "近期或主要任一同向即顺势；当前做多顺近期",
    "bearish": "近期或主要任一同向即顺势；当前做空顺近期",
    "neutral": "方向中性，等待结构明朗后再定义顺势",
}


def _bar_count(frame: Any) -> int:
    bars = getattr(frame, "bars", ()) or ()
    try:
        return max(int(getattr(b, "seq", 0)) for b in bars)
    except (TypeError, ValueError):
        return len(bars)


def _direction_vote_on_slice(
    bars_slice: tuple[Any, ...],
    ema_slice: tuple[float, ...],
    atr_val: float | None,
    *,
    window: int,
    slope_lookback: int,
) -> tuple[int, str]:
    """Five-signal vote on a bar sub-slice (index 0 = newest in slice). Returns (score, summary)."""
    n = len(bars_slice)
    if n < 5:
        return 0, "数据不足"

    W = min(window, n)
    close_prices: list[float] = []
    for bar in list(bars_slice)[:W]:
        try:
            close_prices.append(float(bar.close))
        except (TypeError, ValueError, AttributeError):
            close_prices.append(float("nan"))

    s1 = 0
    try:
        if ema_slice and len(ema_slice) >= 1 and not math.isnan(float(ema_slice[0])):
            k = min(slope_lookback, n - 1)
            if k >= 1 and len(ema_slice) > k and not math.isnan(float(ema_slice[k])):
                d = float(ema_slice[0]) - float(ema_slice[k])
                thr = 0.05 * atr_val if atr_val and atr_val > 0 else 0.0
                if d > thr:
                    s1 = 1
                elif d < -thr:
                    s1 = -1
    except (TypeError, ValueError):
        pass

    s2 = 0
    h = W // 2
    if h >= 1 and len(close_prices) >= 2 * h:
        def _wavg(vals: list[float], start: int) -> float:
            tw = tv = 0.0
            for li, v in enumerate(vals):
                if math.isnan(v):
                    continue
                w = W - (start + li)
                tw += w
                tv += w * v
            return tv / tw if tw > 0 else float("nan")

        near = _wavg(close_prices[:h], 0)
        far = _wavg(close_prices[h : 2 * h], h)
        if not math.isnan(near) and not math.isnan(far):
            diff = near - far
            thr2 = 0.1 * atr_val if atr_val and atr_val > 0 else 0.0
            if diff > thr2:
                s2 = 1
            elif diff < -thr2:
                s2 = -1

    s3 = 0
    try:
        sh, sl = _find_swings(bars_slice, W)
        if len(sh) >= 2 and len(sl) >= 2:
            if sh[0] > sh[1] and sl[0] > sl[1]:
                s3 = 1
            elif sl[0] < sl[1] and sh[0] < sh[1]:
                s3 = -1
    except (TypeError, ValueError, IndexError):
        pass

    bull_tb, bear_tb = _count_trend_bars(bars_slice, W)
    s4 = 0
    if bull_tb + bear_tb > 0:
        if bull_tb >= bear_tb * TREND_BAR_DOMINANCE_RATIO:
            s4 = 1
        elif bear_tb >= bull_tb * TREND_BAR_DOMINANCE_RATIO:
            s4 = -1

    s5 = 0
    mean_overlap = _mean_overlap_ratio(bars_slice, W)
    if mean_overlap is not None and mean_overlap < OVERLAP_LOW_THRESHOLD:
        if s1 > 0:
            s5 = 1
        elif s1 < 0:
            s5 = -1

    score = s1 + s2 + s3 + s4 + s5
    summary = f"score={score}(EMA{s1}+重心{s2}+结构{s3}+趋势棒{s4}+重叠{s5})"
    return score, summary


def _score_to_direction(score: int) -> str:
    if score >= DIRECTION_BULL_THRESHOLD:
        return "bullish"
    if score <= DIRECTION_BEAR_THRESHOLD:
        return "bearish"
    return "neutral"


def compute_background_direction(frame: Any) -> str:
    """Major-trend direction from K{n}-K41 (bars older than the recent 40-bar window)."""
    bars = tuple(getattr(frame, "bars", ()) or ())
    n = _bar_count(frame)
    if n <= BACKGROUND_BAR_START_IDX + 8:
        return "neutral"

    bg_bars = bars[BACKGROUND_BAR_START_IDX:]
    indicators = getattr(frame, "indicators", None)
    ema20 = tuple(getattr(indicators, "ema20", ()) or ())
    atr14 = tuple(getattr(indicators, "atr14", ()) or ())
    ema_bg = ema20[BACKGROUND_BAR_START_IDX:] if len(ema20) > BACKGROUND_BAR_START_IDX else ()
    atr_val: float | None = None
    try:
        if atr14 and not math.isnan(float(atr14[0])):
            atr_val = float(atr14[0])
    except (TypeError, ValueError):
        pass

    W = min(30, len(bg_bars))
    score, _ = _direction_vote_on_slice(
        bg_bars, ema_bg, atr_val, window=W, slope_lookback=min(EMA_SLOPE_LOOKBACK, W - 1)
    )
    return _score_to_direction(score)


def detect_recent_spike(frame: Any) -> str | None:
    """Detect K8-K1 spike (≥3 trend bars, low overlap, directional dominance)."""
    bars = getattr(frame, "bars", ()) or ()
    n = _bar_count(frame)
    W = min(SPIKE_NEAR_WINDOW, n)
    if W < SPIKE_MIN_TREND_BARS + 1:
        return None

    bull_tb, bear_tb = _count_trend_bars(bars, W)
    overlap = _mean_overlap_ratio(bars, W)
    if overlap is None or overlap > SPIKE_OVERLAP_MAX:
        return None

    indicators = getattr(frame, "indicators", None)
    ema20 = tuple(getattr(indicators, "ema20", ()) or ())
    atr14 = tuple(getattr(indicators, "atr14", ()) or ())
    atr_val: float | None = None
    try:
        if atr14 and not math.isnan(float(atr14[0])):
            atr_val = float(atr14[0])
    except (TypeError, ValueError):
        pass

    score, _ = _direction_vote_on_slice(
        tuple(bars), ema20, atr_val, window=W, slope_lookback=min(5, n - 1)
    )

    if bull_tb >= SPIKE_MIN_TREND_BARS and bull_tb >= bear_tb * TREND_BAR_DOMINANCE_RATIO and score >= 2:
        return "bullish"
    if bear_tb >= SPIKE_MIN_TREND_BARS and bear_tb >= bull_tb * TREND_BAR_DOMINANCE_RATIO and score <= -2:
        return "bearish"
    return None


def build_trend_context(frame: Any, trading_direction: str) -> dict[str, Any]:
    """Build trend_context dict for stage-1 JSON (program-filled)."""
    bg = compute_background_direction(frame)
    spike = detect_recent_spike(frame)
    td = trading_direction if trading_direction in ("bullish", "bearish", "neutral") else "neutral"

    conflict = (
        td in ("bullish", "bearish")
        and bg in ("bullish", "bearish")
        and td != bg
    )
    if td == bg and td != "neutral":
        relationship = "aligned"
    elif conflict:
        relationship = "conflict"
    elif bg == "neutral":
        relationship = "neutral_background"
    else:
        relationship = "mixed"

    return {
        "background_direction": bg,
        "trading_direction": td,
        "primary_direction": td,
        "conflict": conflict,
        "relationship": relationship,
        "recent_spike": spike,
        "with_trend_rule": _WITH_TREND_ZH.get(td, _WITH_TREND_ZH["neutral"]),
    }


def render_three_window_summary(frame: Any, trend_ctx: dict[str, Any]) -> str:
    """Compact Brooks三窗口摘要 for stage-1 prefill."""
    n = _bar_count(frame)
    bg = trend_ctx.get("background_direction", "neutral")
    td = trend_ctx.get("trading_direction", "neutral")
    spike = trend_ctx.get("recent_spike")
    rel = trend_ctx.get("relationship", "mixed")
    conflict = trend_ctx.get("conflict", False)

    bg_hi = n
    bg_lo = min(BACKGROUND_BAR_START_IDX + 1, n)
    lines = [
        "## 程序三窗口结构摘要（Brooks 单周期多尺度，供 §2.2 参考）",
        "",
        f"- **长程背景 K{bg_hi}-K{bg_lo}**：主要趋势方向 ≈ **{bg}**（磁力位/阻力支撑参考，不否决近期）",
        f"- **近期结构 K{min(RECENT_STRUCT_BARS, n)}-K1**：交易主方向 ≈ **{td}**（cycle_position / 入场逻辑优先此窗口）",
        f"- **即时惯性 K{min(SPIKE_NEAR_WINDOW, n)}-K1**："
        + (f"检测到 **{spike}** 尖峰特征" if spike else "无典型尖峰，按通道/区间逻辑"),
        f"- **关系**：{rel}" + ("（新旧趋势冲突 → 近期为主，背景作风险参考）" if conflict else ""),
        f"- **顺势规则**：{trend_ctx.get('with_trend_rule', '')}",
        "",
        "node 2.2 须按上表填写：同向=共振提高置信；冲突=不否决近期、不自动减半仓位。",
    ]
    return "\n".join(lines)
