"""Integration tests for Orchestrator + PreflightDataGate (Task 3).

Property 1b: data insufficient → zero AI calls, record.exception.type=="insufficient_data".
"""
from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame
from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
from pa_agent.util.threading import CancelToken, OrchestratorEvent


def _make_bar(seq: int) -> KlineBar:
    return KlineBar(
        seq=seq, ts_open=float(1_000_000 - seq * 60_000),
        open=2000.0, high=2010.0, low=1990.0, close=2005.0,
        volume=1.0, closed=True,
    )


def _insufficient_frame_19bars() -> KlineFrame:
    n = 19
    bars = tuple(_make_bar(i + 1) for i in range(n))
    return KlineFrame(
        symbol="TEST", timeframe="1h", bars=bars, snapshot_ts_local_ms=1,
        indicators=IndicatorBundle(ema20=tuple([2000.0] * n), atr14=tuple([10.0] * n)),
    )


def _insufficient_frame_empty() -> KlineFrame:
    return KlineFrame(
        symbol="TEST", timeframe="1h", bars=(), snapshot_ts_local_ms=1,
        indicators=IndicatorBundle(ema20=(), atr14=()),
    )


def _insufficient_frame_all_nan() -> KlineFrame:
    n = 20
    bars = tuple(_make_bar(i + 1) for i in range(n))
    return KlineFrame(
        symbol="TEST", timeframe="1h", bars=bars, snapshot_ts_local_ms=1,
        indicators=IndicatorBundle(
            ema20=tuple([float("nan")] * n),
            atr14=tuple([float("nan")] * n),
        ),
    )


def _make_orchestrator():
    """Build orchestrator with mocked AI client and writer."""
    client = MagicMock()
    assembler = MagicMock()
    assembler.build_stage1.return_value = [{"role": "user", "content": "test"}]
    router = MagicMock(return_value=[])
    validator = MagicMock()
    writer = MagicMock()
    exp_reader = MagicMock()
    exp_reader.read_for_stage2.return_value = []
    exp_reader.read_top5.return_value = []

    orch = TwoStageOrchestrator(
        client=client,
        assembler=assembler,
        router=router,
        validator=validator,
        pending_writer=writer,
        exp_reader=exp_reader,
        settings=None,
    )
    return orch, client, assembler, writer


@pytest.mark.parametrize("frame_factory,expected_check", [
    (_insufficient_frame_19bars, "bar_count_lt_20"),
    (_insufficient_frame_empty, "bars_empty_or_bad_ohlc"),
    (_insufficient_frame_all_nan, "indicators_all_nan"),
])
def test_insufficient_data_zero_ai_calls(frame_factory, expected_check):
    """Property 1b: insufficient data → zero AI calls, record.exception.type == insufficient_data."""
    orch, client, assembler, writer = _make_orchestrator()
    frame = frame_factory()
    cancel_token = CancelToken()
    events = []

    record = orch.submit(frame, cancel_token, lambda e: events.append(e))

    # Zero AI calls
    client.stream_chat.assert_not_called()
    assembler.build_stage1.assert_not_called()

    # Record has correct exception
    assert record.exception is not None
    assert record.exception["type"] == "insufficient_data"
    assert record.exception["failed_check"] == expected_check
    assert record.stage1_response is None
    assert record.stage1_diagnosis is None

    # Event emitted
    assert OrchestratorEvent.InsufficientData in events

    # save_partial called with "insufficient_data"
    writer.save_partial.assert_called()
    args = writer.save_partial.call_args[0]
    assert args[1] == "insufficient_data"


def test_insufficient_data_no_stage2_ai_call():
    """Stage2 AI is also not called for insufficient data."""
    orch, client, assembler, writer = _make_orchestrator()
    frame = _insufficient_frame_19bars()
    cancel_token = CancelToken()

    record = orch.submit(frame, cancel_token, lambda e: None)

    # No stage2 calls either
    client.stream_chat.assert_not_called()
    assert record.stage2_decision is None
    assert record.stage2_response is None


def test_insufficient_data_record_exception_type_distinct():
    """Verify insufficient_data record can be distinguished from other error types."""
    orch, client, assembler, writer = _make_orchestrator()
    frame = _insufficient_frame_19bars()

    record = orch.submit(frame, CancelToken(), lambda e: None)

    exc = record.exception
    assert exc["type"] == "insufficient_data"
    assert exc["type"] != "network_error"
    assert exc["type"] != "validation_error"
    assert exc["stage"] == "preflight"
