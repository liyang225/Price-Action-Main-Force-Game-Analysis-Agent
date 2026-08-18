"""Tests for FreeChatSession: keep_reasoning_in_resend=True preserves
reasoning_content in history_for_api and JSONL output always has ai_reasoning.

Task 12.5 — Validates: Requirements R11.4, R11.5, R12.4
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from pa_agent.orchestrator.free_chat import FreeChatSession
from pa_agent.records.schema import AnalysisRecord, RecordMeta
from pa_agent.util.threading import CancelToken


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_reply(content: str = "AI response", reasoning: str = "AI reasoning") -> MagicMock:
    """Build a mock AIReply."""
    reply = MagicMock()
    reply.content = content
    reply.reasoning_content = reasoning
    reply.raw = {}
    reply.usage = MagicMock()
    reply.usage.prompt_tokens = 100
    reply.usage.completion_tokens = 50
    reply.usage.cached_prompt_tokens = 0
    reply.usage.total_tokens = 150
    return reply


def _make_base_record() -> AnalysisRecord:
    """Build a minimal AnalysisRecord for testing."""
    meta = RecordMeta(
        timestamp_local_iso="2026-05-18T14:00:13.000",
        timestamp_local_ms=1_747_569_613_000,
        symbol="XAUUSD",
        timeframe="1h",
        bar_count=2,
        ai_provider={"model": "deepseek-v4-pro"},
    )
    return AnalysisRecord(
        meta=meta,
        kline_data=[],
        htf_text="",
        stage1_messages=[],
        stage1_response=None,
        stage1_diagnosis=None,
        stage2_messages=[
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "user msg"},
        ],
        stage2_response={
            "content": "stage2 content",
            "reasoning_content": "stage2 reasoning",
        },
        stage2_decision={"decision": {"order_type": "不下单"}},
        strategy_files_used=[],
        experience_loaded=[],
        exception=None,
        usage_total={},
    )


def _make_session_with_toggle(client: MagicMock) -> FreeChatSession:
    """Build a FreeChatSession with keep_reasoning_in_resend=True."""
    session = FreeChatSession(
        base_record=_make_base_record(),
        client=client,
        assembler=MagicMock(),
        pending_writer=MagicMock(),
        ledger=MagicMock(),
    )
    session.keep_reasoning_in_resend = True
    return session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFreeChatKeepsReasoningWhenToggled:
    """keep_reasoning_in_resend=True: reasoning preserved in API messages."""

    def test_followup_assistant_keeps_reasoning_in_api_when_toggled(self):
        """When toggled, prior follow-up assistant turns include reasoning_content."""
        client = MagicMock()
        client.stream_chat.side_effect = [
            _make_reply("reply 1", "reasoning 1"),
            _make_reply("reply 2", "reasoning 2"),
        ]
        session = _make_session_with_toggle(client)
        cancel = CancelToken()

        session.send("q1", cancel)
        session.send("q2", cancel)

        messages: list[dict] = client.stream_chat.call_args_list[1][0][0]
        assert messages[3]["role"] == "assistant"
        assert messages[3]["content"] == "reply 1"
        assert messages[3].get("reasoning_content") == "reasoning 1"

    def test_previous_turns_keep_reasoning_in_api(self):
        """On the second send, the first assistant turn in history_for_api
        must include reasoning_content."""
        client = MagicMock()
        client.stream_chat.side_effect = [
            _make_reply("reply 1", "reasoning 1"),
            _make_reply("reply 2", "reasoning 2"),
        ]
        session = _make_session_with_toggle(client)
        cancel = CancelToken()

        session.send("question 1", cancel)
        session.send("question 2", cancel)

        messages: list[dict] = client.stream_chat.call_args_list[1][0][0]
        assert len(messages) == 5
        assert messages[3]["role"] == "assistant"
        assert messages[3]["content"] == "reply 1"
        assert messages[3].get("reasoning_content") == "reasoning 1"

    def test_three_turns_all_assistant_messages_have_reasoning_in_api(self):
        """After 3 sends with toggle on, every assistant message in every
        history_for_api call must have reasoning_content."""
        client = MagicMock()
        client.stream_chat.side_effect = [
            _make_reply("reply 1", "reasoning 1"),
            _make_reply("reply 2", "reasoning 2"),
            _make_reply("reply 3", "reasoning 3"),
        ]
        session = _make_session_with_toggle(client)
        cancel = CancelToken()

        session.send("q1", cancel)
        session.send("q2", cancel)
        session.send("q3", cancel)

        for call_args in client.stream_chat.call_args_list:
            messages: list[dict] = call_args[0][0]
            for msg in messages:
                if msg.get("role") == "assistant":
                    assert "reasoning_content" in msg, (
                        f"reasoning_content missing from assistant message: {msg}"
                    )

    def test_append_followup_jsonl_always_has_ai_reasoning(self):
        """FollowupTurn objects passed to append_followup must always have
        ai_reasoning set (not None) when the reply has reasoning_content."""
        client = MagicMock()
        client.stream_chat.side_effect = [
            _make_reply("reply 1", "reasoning 1"),
            _make_reply("reply 2", "reasoning 2"),
            _make_reply("reply 3", "reasoning 3"),
        ]
        pending_writer = MagicMock()
        session = FreeChatSession(
            base_record=_make_base_record(),
            client=client,
            assembler=MagicMock(),
            pending_writer=pending_writer,
            ledger=MagicMock(),
        )
        session.keep_reasoning_in_resend = True
        cancel = CancelToken()

        session.send("q1", cancel)
        session.send("q2", cancel)
        session.send("q3", cancel)

        calls = pending_writer.append_followup.call_args_list
        assert len(calls) == 3
        for i, c in enumerate(calls, start=1):
            turn_obj = c[0][1]  # FollowupTurn
            assert turn_obj.ai_reasoning is not None, (
                f"Turn {i}: ai_reasoning is None"
            )
            assert turn_obj.ai_reasoning == f"reasoning {i}"

    def test_append_followup_jsonl_has_ai_reasoning_without_toggle(self):
        """Even with keep_reasoning_in_resend=False (default), the JSONL
        FollowupTurn must still contain ai_reasoning (it's always persisted)."""
        client = MagicMock()
        client.stream_chat.return_value = _make_reply("reply", "my reasoning")
        pending_writer = MagicMock()
        session = FreeChatSession(
            base_record=_make_base_record(),
            client=client,
            assembler=MagicMock(),
            pending_writer=pending_writer,
            ledger=MagicMock(),
        )
        # Default: keep_reasoning_in_resend = False
        cancel = CancelToken()

        session.send("hello", cancel)

        calls = pending_writer.append_followup.call_args_list
        assert len(calls) == 1
        turn_obj = calls[0][0][1]
        assert turn_obj.ai_reasoning == "my reasoning"

    def test_history_full_always_has_reasoning_regardless_of_toggle(self):
        """history_full must always preserve reasoning_content, regardless of
        the keep_reasoning_in_resend flag."""
        client = MagicMock()
        client.stream_chat.side_effect = [
            _make_reply("reply 1", "reasoning 1"),
            _make_reply("reply 2", "reasoning 2"),
        ]
        session = _make_session_with_toggle(client)
        cancel = CancelToken()

        session.send("q1", cancel)
        session.send("q2", cancel)

        assistant_msgs = [
            m for m in session.history_full if m.get("role") == "assistant"
        ]
        assert len(assistant_msgs) == 2
        for i, msg in enumerate(assistant_msgs, start=1):
            assert msg.get("reasoning_content") == f"reasoning {i}"

    def test_toggle_can_be_set_after_construction(self):
        """keep_reasoning_in_resend can be changed after construction and
        affects subsequent sends."""
        client = MagicMock()
        client.stream_chat.side_effect = [
            _make_reply("reply 1", "reasoning 1"),
            _make_reply("reply 2", "reasoning 2"),
        ]
        session = FreeChatSession(
            base_record=_make_base_record(),
            client=client,
            assembler=MagicMock(),
            pending_writer=MagicMock(),
            ledger=MagicMock(),
        )
        cancel = CancelToken()

        session.send("q1", cancel)
        msgs_first: list[dict] = client.stream_chat.call_args_list[0][0][0]
        assert not any(m.get("reasoning_content") for m in msgs_first)

        session.keep_reasoning_in_resend = True
        session.send("q2", cancel)
        msgs_second: list[dict] = client.stream_chat.call_args_list[1][0][0]
        followup_asst = next(m for m in msgs_second if m.get("role") == "assistant")
        assert followup_asst.get("reasoning_content") == "reasoning 1"
