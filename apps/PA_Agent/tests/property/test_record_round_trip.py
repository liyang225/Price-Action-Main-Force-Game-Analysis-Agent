"""Property-based tests for AnalysisRecord JSON round-trip (task 10.5 / PR5).

**Validates: Requirements PR5**

Property tested:
- For any generated AnalysisRecord r:
  AnalysisRecord.model_validate(json.loads(json.dumps(r.model_dump()))) == r

This verifies that the pydantic schema survives a full JSON serialization /
deserialization cycle with deep equality, including:
  - float values in kline_data
  - optional fields both present and absent (None)
  - nested dicts, lists, and string fields
"""
from __future__ import annotations

import json

from hypothesis import given
from hypothesis import settings as h_settings
from hypothesis import strategies as st

from pa_agent.records.schema import AnalysisRecord, RecordMeta


# ── Strategies ────────────────────────────────────────────────────────────────

# Safe floats: avoid NaN, infinity, and values that lose precision in JSON
_safe_float = st.floats(
    allow_nan=False,
    allow_infinity=False,
    min_value=-1e15,
    max_value=1e15,
)

# A single kline bar dict as sent to the AI
_kline_bar_st = st.fixed_dictionaries(
    {
        "seq": st.integers(min_value=1, max_value=5000),
        "ts_open": st.integers(min_value=0, max_value=2**53),
        "open": _safe_float,
        "high": _safe_float,
        "low": _safe_float,
        "close": _safe_float,
        "volume": _safe_float,
        "closed": st.booleans(),
    }
)

# A single chat message dict
_message_st = st.fixed_dictionaries(
    {
        "role": st.sampled_from(["system", "user", "assistant"]),
        "content": st.text(),
    }
)

# Optional message dict with reasoning_content (as returned by DeepSeek)
_response_dict_st = st.none() | st.fixed_dictionaries(
    {
        "content": st.text(),
        "reasoning_content": st.none() | st.text(),
        "usage": st.fixed_dictionaries(
            {
                "prompt_tokens": st.integers(min_value=0),
                "completion_tokens": st.integers(min_value=0),
                "total_tokens": st.integers(min_value=0),
            }
        ),
    }
)

# Optional diagnosis / decision dict
_optional_dict_st = st.none() | st.dictionaries(
    st.text(min_size=1, max_size=30),
    st.one_of(
        st.text(),
        st.integers(),
        _safe_float,
        st.booleans(),
        st.none(),
    ),
    max_size=10,
)

# Optional exception dict
_exception_dict_st = st.none() | st.fixed_dictionaries(
    {
        "category": st.sampled_from(["a", "b", "c", "d", "network"]),
        "message": st.text(),
    }
)

# usage_total dict with integer values
_usage_total_st = st.fixed_dictionaries(
    {
        "prompt_tokens": st.integers(min_value=0),
        "completion_tokens": st.integers(min_value=0),
        "cached_prompt_tokens": st.integers(min_value=0),
        "total_tokens": st.integers(min_value=0),
    }
)

# ai_provider snapshot dict (sanitized, no plaintext key)
_ai_provider_st = st.fixed_dictionaries(
    {
        "model": st.text(min_size=1),
        "base_url": st.text(min_size=1),
    }
)

# experience entry dict
_experience_entry_st = st.fixed_dictionaries(
    {
        "filename": st.text(min_size=1),
        "case_type": st.sampled_from(["success", "failure"]),
        "content": st.dictionaries(st.text(min_size=1), st.text(), max_size=5),
    }
)


@st.composite
def analysis_record_st(draw: st.DrawFn) -> AnalysisRecord:
    """Composite strategy that builds a fully valid AnalysisRecord."""
    meta = RecordMeta(
        timestamp_local_iso=draw(st.text(min_size=1, max_size=30)),
        timestamp_local_ms=draw(st.integers(min_value=0, max_value=2**53)),
        symbol=draw(st.text(min_size=1, max_size=20)),
        timeframe=draw(st.text(min_size=1, max_size=10)),
        bar_count=draw(st.integers(min_value=1, max_value=5000)),
        ai_provider=draw(_ai_provider_st),
    )

    return AnalysisRecord(
        meta=meta,
        kline_data=draw(st.lists(_kline_bar_st, min_size=0, max_size=20)),
        htf_text=draw(st.text()),
        stage1_messages=draw(st.lists(_message_st, min_size=0, max_size=5)),
        stage1_response=draw(_response_dict_st),
        stage1_diagnosis=draw(_optional_dict_st),
        stage2_messages=draw(st.lists(_message_st, min_size=0, max_size=5)),
        stage2_response=draw(_response_dict_st),
        stage2_decision=draw(_optional_dict_st),
        strategy_files_used=draw(st.lists(st.text(min_size=1), max_size=10)),
        experience_loaded=draw(st.lists(_experience_entry_st, max_size=5)),
        exception=draw(_exception_dict_st),
        usage_total=draw(_usage_total_st),
    )


# ── Property ──────────────────────────────────────────────────────────────────

@given(analysis_record_st())
@h_settings(max_examples=100)
def test_analysis_record_json_round_trip(record: AnalysisRecord) -> None:
    """AnalysisRecord survives a JSON serialization / deserialization cycle.

    Asserts that:
      AnalysisRecord.model_validate(json.loads(json.dumps(r.model_dump()))) == r

    **Validates: Requirements PR5**
    """
    # Serialize to JSON string and back to a plain dict
    raw_dict = json.loads(json.dumps(record.model_dump()))

    # Reconstruct via pydantic validation
    reconstructed = AnalysisRecord.model_validate(raw_dict)

    # Deep equality check
    assert reconstructed == record, (
        f"Round-trip produced a different record.\n"
        f"Original:      {record!r}\n"
        f"Reconstructed: {reconstructed!r}"
    )
