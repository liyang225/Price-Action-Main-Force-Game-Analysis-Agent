"""Regression tests for follow-up chat from a historical analysis record."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from pa_agent.records.schema import AnalysisRecord
from tests.fixtures.ai_payloads import VALID_STAGE1, VALID_STAGE2


def _history_record() -> AnalysisRecord:
    return AnalysisRecord.model_validate(
        {
            "meta": {
                "timestamp_local_iso": "2026-07-25T10:00:00+08:00",
                "timestamp_local_ms": 1_753_408_000_000,
                "symbol": "XAUUSD",
                "timeframe": "1h",
                "bar_count": 1,
                "ai_provider": {},
            },
            "kline_data": [
                {
                    "ts_open": 1_753_408_000_000,
                    "open": 100.0,
                    "high": 102.0,
                    "low": 99.0,
                    "close": 101.0,
                    "volume": 10.0,
                }
            ],
            "htf_text": "",
            "stage1_messages": [],
            "stage1_response": {},
            "stage1_diagnosis": VALID_STAGE1,
            "stage2_messages": [],
            "stage2_response": {},
            "stage2_decision": VALID_STAGE2,
            "strategy_files_used": [],
            "experience_loaded": [],
            "exception": None,
            "usage_total": {},
        }
    )


def test_history_record_creates_followup_session():
    from pa_agent.gui.main_window import MainWindow
    from pa_agent.orchestrator.free_chat import FreeChatSession

    panel = MagicMock()
    window = SimpleNamespace(
        _ctx=SimpleNamespace(
            client=MagicMock(),
            assembler=MagicMock(),
            pending_writer=MagicMock(),
            ledger=MagicMock(),
            settings=None,
        ),
        _debug_widget=MagicMock(),
        _prompt_files_panel=MagicMock(),
        _decision_panel=MagicMock(),
        _future_trend_panel=MagicMock(),
        _decision_tree_panel=MagicMock(),
        _decision_flow_viz_panel=MagicMock(),
        _stream_panel=panel,
        _demo_mode=False,
        _history_overlay_active=True,
        _archive_completed_history_record=MagicMock(),
        _confidence_threshold=lambda: 0,
        _bind_decision_tree=MagicMock(),
        _make_kline_snapshot_fn=lambda: lambda: "",
    )
    record = _history_record()

    MainWindow._on_record_ready_impl(window, record)

    session = panel.set_session.call_args.args[0]
    assert isinstance(session, FreeChatSession)
    assert session._base_record is record
    panel.on_record_saved.assert_called_once()
    panel.set_input_enabled.assert_not_called()
