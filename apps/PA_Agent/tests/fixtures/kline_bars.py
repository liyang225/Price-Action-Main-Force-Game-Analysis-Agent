"""Synthetic newest-first K-line lists for tests."""
from __future__ import annotations

from pa_agent.data.base import KlineBar


def make_newest_first_bars(
    n: int,
    *,
    base_ts: float = 1_700_000_000.0,
    step_sec: float = 900.0,
    with_forming: bool = True,
) -> list[KlineBar]:
    """Build *n* closed bars plus optional forming bar at index 0 (seq=1)."""
    bars: list[KlineBar] = []
    if with_forming:
        bars.append(
            KlineBar(
                seq=1,
                ts_open=base_ts,
                open=2000.0,
                high=2010.0,
                low=1990.0,
                close=2005.0,
                volume=100.0,
                closed=False,
            )
        )
    start = 2 if with_forming else 1
    for seq in range(start, start + n):
        bars.append(
            KlineBar(
                seq=seq,
                ts_open=base_ts - (seq - 1) * step_sec,
                open=2000.0,
                high=2010.0,
                low=1990.0,
                close=2005.0,
                volume=100.0,
                closed=True,
            )
        )
    return bars
