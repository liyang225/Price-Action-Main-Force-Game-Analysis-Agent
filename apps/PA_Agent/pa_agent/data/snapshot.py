"""KlineFrame snapshot builder."""
from __future__ import annotations

import math

from pa_agent.data.bar_close_wait import has_forming_bar_at_head
from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame, normalize_kline_bar
from pa_agent.util.timefmt import now_local_ms

# Extra closed bars fetched before the AI window so EMA20/ATR14 can warm up.
# Only the newest *n* bars are sent to the model; indicators use this buffer.
INDICATOR_WARMUP_BARS = 50


def frame_is_pure_closed(frame: KlineFrame) -> bool:
    """True when every bar on the frame is marked closed (no forming slot)."""
    return bool(frame.bars) and all(b.closed for b in frame.bars)


def frames_equal_for_chart(a: KlineFrame, b: KlineFrame) -> bool:
    """True when two frames would render the same candles and EMA (ignore snapshot time)."""
    if a.symbol != b.symbol or a.timeframe != b.timeframe:
        return False
    if len(a.bars) != len(b.bars):
        return False
    if a.bars != b.bars:
        return False
    return _indicators_equal(a.indicators, b.indicators)


def _indicators_equal(a: IndicatorBundle, b: IndicatorBundle) -> bool:
    if len(a.ema20) != len(b.ema20) or len(a.atr14) != len(b.atr14):
        return False
    for x, y in zip(a.ema20, b.ema20, strict=True):
        if not _float_equal(x, y):
            return False
    for x, y in zip(a.atr14, b.atr14, strict=True):
        if not _float_equal(x, y):
            return False
    return True


def _float_equal(a: float, b: float) -> bool:
    if math.isnan(a) and math.isnan(b):
        return True
    return a == b


def take_snapshot_from_bars(
    bars_raw: list[KlineBar],
    n: int,
    symbol: str,
    timeframe: str,
    *,
    now_ms: int | None = None,
) -> KlineFrame:
    """Build an analysis KlineFrame from a newest-first bar list (same as AI table).

    Uses ``build_analysis_frame``: *n* newest **closed** bars; skips an unclosed
    bar at index 0 when present.

    Raises ValueError if insufficient bars are available.
    """
    frame = build_analysis_frame(bars_raw, n, symbol, timeframe, now_ms=now_ms)
    if frame is None:
        raise ValueError(
            f"Need at least {n} closed bars (or {n + 1} with a forming bar at index 0); "
            f"got {len(bars_raw)}."
        )
    return frame


def _newest_closed_slice(
    bars_raw: list[KlineBar],
    n: int,
    *,
    timeframe: str = "",
    symbol: str = "",
    now_ms: int | None = None,
) -> list[KlineBar] | None:
    """Return *n* newest closed bars from a newest-first list.

    Skips index 0 only when it is still forming. Stale ``closed=False`` after
    halt (e.g. TradingView) is kept as K1.
    """
    if not bars_raw or n < 1:
        return None
    forming = has_forming_bar_at_head(
        bars_raw,
        timeframe or None,
        symbol=symbol or None,
        now_ms=now_ms,
    )

    if forming:
        if len(bars_raw) < n + 1:
            return None
        return list(bars_raw[1 : n + 1])
    if len(bars_raw) < n:
        return None
    return list(bars_raw[:n])


def compute_indicators(bars: list[KlineBar]) -> IndicatorBundle:
    """Compute EMA20 and ATR14 for *bars* (newest-first order).

    Indicators are computed on the reversed (oldest-first) sequence and then
    reversed back so that index *i* aligns with ``bars[i]`` (K1 at index 0).
    """
    from pa_agent.indicators.ema import ema_full
    from pa_agent.indicators.atr import atr_full

    # bars is newest-first; indicators need oldest-first input
    bars_asc = list(reversed(bars))

    closes = [b.close for b in bars_asc]
    highs  = [b.high  for b in bars_asc]
    lows   = [b.low   for b in bars_asc]

    ema20_asc = ema_full(closes, period=20)
    atr14_asc = atr_full(highs, lows, closes, period=14)

    # Reverse back to newest-first
    ema20 = tuple(reversed(ema20_asc))
    atr14 = tuple(reversed(atr14_asc))

    return IndicatorBundle(ema20=ema20, atr14=atr14)


def build_display_frame(
    bars_raw: list[KlineBar],
    n: int,
    symbol: str,
    timeframe: str,
    *,
    now_ms: int | None = None,
) -> KlineFrame | None:
    """Chart display frame — same semantics as AI (K1 = newest **closed** bar)."""
    return build_analysis_frame(bars_raw, n, symbol, timeframe, now_ms=now_ms)


def build_live_frame(
    bars_raw: list[KlineBar],
    n_closed: int,
    symbol: str,
    timeframe: str,
    *,
    now_ms: int | None = None,
) -> KlineFrame | None:
    """Live chart frame: include the forming bar + *n_closed* closed bars.

    This is for UI only. The analysis snapshot must still use
    ``build_analysis_frame`` so AI always sees closed-only candles.
    """
    has_forming = has_forming_bar_at_head(
        bars_raw,
        timeframe or None,
        symbol=symbol or None,
        now_ms=now_ms,
    )
    if has_forming:
        if len(bars_raw) < n_closed + 1:
            return None
        raw = bars_raw[: n_closed + 1]
    else:
        if len(bars_raw) < n_closed:
            return None
        raw = bars_raw[:n_closed]

    rebased: list[KlineBar] = []
    closed_idx = 0
    for i, b in enumerate(raw):
        is_forming = has_forming and i == 0
        seq = 0 if is_forming else (closed_idx + 1)
        if not is_forming:
            closed_idx += 1
        rebased.append(
            normalize_kline_bar(
                KlineBar(
                    seq=seq,
                    ts_open=b.ts_open,
                    open=b.open,
                    high=b.high,
                    low=b.low,
                    close=b.close,
                    volume=b.volume,
                    amount=getattr(b, "amount", 0.0),
                    pct_chg=getattr(b, "pct_chg", None),
                    closed=not is_forming,
                )
            )
        )
    indicators = compute_indicators(rebased)
    return KlineFrame(
        symbol=symbol,
        timeframe=timeframe,
        bars=tuple(rebased),
        indicators=indicators,
        snapshot_ts_local_ms=now_local_ms(),
    )


def _rebase_closed_bars(closed_raw: list[KlineBar]) -> list[KlineBar]:
    return [
        normalize_kline_bar(
            KlineBar(
                seq=i + 1,
                ts_open=b.ts_open,
                open=b.open,
                high=b.high,
                low=b.low,
                close=b.close,
                volume=b.volume,
                amount=getattr(b, "amount", 0.0),
                pct_chg=getattr(b, "pct_chg", None),
                closed=True,
            )
        )
        for i, b in enumerate(closed_raw)
    ]


def build_analysis_frame(
    bars_raw: list[KlineBar],
    n: int,
    symbol: str,
    timeframe: str,
    *,
    now_ms: int | None = None,
) -> KlineFrame | None:
    """Build a snapshot for AI analysis: *n* newest **closed** bars only.

    *bars_raw* is newest-first. If ``bars_raw[0].closed`` is False it is the
    forming bar and is discarded; otherwise all entries are treated as closed.

    Up to ``INDICATOR_WARMUP_BARS`` additional older closed bars are included
    when computing EMA20/ATR14, but only *n* bars are returned in the frame.

    Chart and AI must both use this (or ``build_display_frame``) so K-line
    seq numbers refer to the same candles.
    """
    forming = has_forming_bar_at_head(
        bars_raw,
        timeframe or None,
        symbol=symbol or None,
        now_ms=now_ms,
    )
    avail_closed = len(bars_raw) - (1 if forming else 0)
    if avail_closed < n:
        return None
    fetch_n = min(n + INDICATOR_WARMUP_BARS, avail_closed)
    closed_raw = _newest_closed_slice(
        bars_raw,
        fetch_n,
        timeframe=timeframe,
        symbol=symbol,
        now_ms=now_ms,
    )
    if closed_raw is None or len(closed_raw) < n:
        return None

    rebased_all = _rebase_closed_bars(closed_raw)
    indicators_all = compute_indicators(rebased_all)
    rebased = rebased_all[:n]
    indicators = IndicatorBundle(
        ema20=indicators_all.ema20[:n],
        atr14=indicators_all.atr14[:n],
    )
    return KlineFrame(
        symbol=symbol,
        timeframe=timeframe,
        bars=tuple(rebased),
        indicators=indicators,
        snapshot_ts_local_ms=now_local_ms(),
    )
