"""Tests for analysis snapshots (closed bars only)."""
from __future__ import annotations

import time

from pa_agent.data.base import KlineBar
from pa_agent.data.snapshot import build_analysis_frame, build_display_frame


def _bar(seq: int, ts_ms: float, *, closed: bool) -> KlineBar:
    return KlineBar(
        seq=seq,
        ts_open=ts_ms,
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume=100.0,
        amount=float(seq * 1_000),
        pct_chg=float(seq),
        closed=closed,
    )


def test_build_analysis_frame_drops_forming_bar() -> None:
    now_ms = int(time.time() * 1000)
    ts_k2 = float(now_ms - 330_000)
    ts_k3 = float(now_ms - 630_000)
    raw = [
        _bar(1, float(now_ms - 30_000), closed=False),
        _bar(2, ts_k2, closed=True),
        _bar(3, ts_k3, closed=True),
    ]
    frame = build_analysis_frame(raw, 2, "XAU", "5m", now_ms=now_ms)
    assert frame is not None
    assert len(frame.bars) == 2
    assert all(b.closed for b in frame.bars)
    assert frame.bars[0].ts_open == ts_k2
    assert frame.bars[0].seq == 1
    assert frame.bars[0].amount == 2_000.0
    assert frame.bars[0].pct_chg == 2.0
    assert frame.bars[1].ts_open == ts_k3


def test_build_analysis_frame_insufficient_data() -> None:
    now_ms = int(time.time() * 1000)
    raw = [
        _bar(1, float(now_ms - 30_000), closed=False),
        _bar(2, float(now_ms - 330_000), closed=True),
    ]
    assert build_analysis_frame(raw, 2, "XAU", "5m", now_ms=now_ms) is None


def test_display_frame_matches_analysis_frame() -> None:
    now_ms = int(time.time() * 1000)
    raw = [
        _bar(1, float(now_ms - 30_000), closed=False),
        _bar(2, float(now_ms - 330_000), closed=True),
        _bar(3, float(now_ms - 630_000), closed=True),
    ]
    a = build_analysis_frame(raw, 2, "XAU", "5m", now_ms=now_ms)
    d = build_display_frame(raw, 2, "XAU", "5m", now_ms=now_ms)
    assert a is not None and d is not None
    assert [b.ts_open for b in a.bars] == [b.ts_open for b in d.bars]
    assert a.bars[0].seq == 1
