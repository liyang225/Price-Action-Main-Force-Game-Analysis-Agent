"""Tests for demo record loading."""
from __future__ import annotations

import json
from pathlib import Path

from pa_agent.demo.record_loader import (
    frame_from_record_klines,
    is_demo_playable,
    list_pending_record_paths,
    load_analysis_record,
    pick_playable_demo_record,
    try_load_analysis_record,
)


def test_load_pending_sample_record() -> None:
    paths = list_pending_record_paths()
    if not paths:
        return
    record = load_analysis_record(paths[0])
    assert record.meta.symbol
    assert record.kline_data


def test_pick_playable_demo_record_skips_invalid(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    good = tmp_path / "good.json"
    good.write_text(
        json.dumps(
            {
                "meta": {
                    "timestamp_local_iso": "2026-01-01T00:00:00",
                    "timestamp_local_ms": 0,
                    "symbol": "XAUUSDm",
                    "timeframe": "15m",
                    "bar_count": 1,
                    "ai_provider": {},
                },
                "kline_data": [
                    {
                        "seq": 1,
                        "ts_open": 1000,
                        "open": 1,
                        "high": 2,
                        "low": 0.5,
                        "close": 1.5,
                        "volume": 10,
                        "closed": True,
                    }
                ],
                "htf_text": "",
                "stage1_messages": [],
                "stage1_response": None,
                "stage1_diagnosis": {"gate_result": "proceed"},
                "stage2_messages": [],
                "stage2_response": None,
                "stage2_decision": None,
                "strategy_files_used": [],
                "experience_loaded": [],
                "exception": None,
                "usage_total": {},
            }
        ),
        encoding="utf-8",
    )
    picked = pick_playable_demo_record(directory=tmp_path)
    assert picked is not None
    path, record = picked
    assert path == good
    assert is_demo_playable(record)
    assert try_load_analysis_record(bad) is None


def test_frame_from_record_klines() -> None:
    paths = list_pending_record_paths()
    if not paths:
        return
    record = load_analysis_record(paths[0])
    frame = frame_from_record_klines(
        record.kline_data,
        symbol=record.meta.symbol,
        timeframe=record.meta.timeframe,
    )
    assert frame.bars[0].seq == 1
    assert len(frame.indicators.ema20) == len(frame.bars)
