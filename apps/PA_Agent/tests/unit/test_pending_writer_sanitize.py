"""Unit tests for PendingWriter._sanitize and api_key integration."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pa_agent.records.pending_writer import PendingWriter
from pa_agent.util.mask_secret import mask_secret


# ---------------------------------------------------------------------------
# _sanitize static method
# ---------------------------------------------------------------------------

class TestSanitize:
    def test_empty_api_key_is_noop(self):
        data = {"key": "some-secret-value", "nested": {"x": "some-secret-value"}}
        result = PendingWriter._sanitize(data, "")
        assert result == data

    def test_replaces_exact_match_in_string(self):
        api_key = "sk-abc123"
        data = {"field": api_key}
        result = PendingWriter._sanitize(data, api_key)
        assert result["field"] == mask_secret(api_key)

    def test_replaces_substring_within_string(self):
        api_key = "sk-abc123"
        data = {"field": f"Authorization: Bearer {api_key} extra"}
        result = PendingWriter._sanitize(data, api_key)
        assert api_key not in result["field"]
        assert mask_secret(api_key) in result["field"]

    def test_replaces_in_nested_dict(self):
        api_key = "sk-abc123"
        data = {"outer": {"inner": {"deep": api_key}}}
        result = PendingWriter._sanitize(data, api_key)
        assert result["outer"]["inner"]["deep"] == mask_secret(api_key)

    def test_replaces_in_list(self):
        api_key = "sk-abc123"
        data = {"items": [api_key, "safe", f"prefix-{api_key}"]}
        result = PendingWriter._sanitize(data, api_key)
        assert result["items"][0] == mask_secret(api_key)
        assert result["items"][1] == "safe"
        assert api_key not in result["items"][2]

    def test_replaces_in_nested_list_of_dicts(self):
        api_key = "sk-abc123"
        data = {"turns": [{"role": "user", "content": f"key={api_key}"}]}
        result = PendingWriter._sanitize(data, api_key)
        assert api_key not in result["turns"][0]["content"]
        assert mask_secret(api_key) in result["turns"][0]["content"]

    def test_non_string_values_are_unchanged(self):
        api_key = "sk-abc123"
        data = {"count": 42, "flag": True, "nothing": None}
        result = PendingWriter._sanitize(data, api_key)
        assert result == data

    def test_multiple_occurrences_in_one_string(self):
        api_key = "sk-abc123"
        data = {"field": f"{api_key} and {api_key}"}
        result = PendingWriter._sanitize(data, api_key)
        assert api_key not in result["field"]
        masked = mask_secret(api_key)
        assert result["field"] == f"{masked} and {masked}"


# ---------------------------------------------------------------------------
# Constructor api_key parameter
# ---------------------------------------------------------------------------

class TestConstructorApiKey:
    def test_default_api_key_is_empty(self, tmp_path):
        writer = PendingWriter(pending_dir=tmp_path)
        assert writer._api_key == ""

    def test_api_key_stored(self, tmp_path):
        writer = PendingWriter(pending_dir=tmp_path, api_key="sk-test")
        assert writer._api_key == "sk-test"


# ---------------------------------------------------------------------------
# save_full / save_partial sanitize before writing
# ---------------------------------------------------------------------------

def _make_record(api_key_in_content: str):
    """Build a minimal AnalysisRecord with the api_key embedded in a text field."""
    from pa_agent.records.schema import AnalysisRecord, RecordMeta

    meta = RecordMeta(
        timestamp_local_iso="2026-05-18T14:00:00+08:00",
        timestamp_local_ms=1_700_000_000_000,
        symbol="XAUUSD",
        timeframe="1h",
        bar_count=100,
        ai_provider={"model": "deepseek", "note": api_key_in_content},
    )
    return AnalysisRecord(
        meta=meta,
        kline_data=[],
        htf_text=f"htf context key={api_key_in_content}",
        stage1_messages=[{"role": "user", "content": api_key_in_content}],
        stage1_response=None,
        stage1_diagnosis=None,
        stage2_messages=[],
        stage2_response=None,
        stage2_decision=None,
        strategy_files_used=[],
        experience_loaded=[],
        exception=None,
        usage_total={"prompt_tokens": 0, "completion_tokens": 0},
    )


class TestSaveFullSanitizes:
    def test_api_key_not_in_written_file(self, tmp_path):
        api_key = "sk-supersecret"
        record = _make_record(api_key)
        writer = PendingWriter(pending_dir=tmp_path, api_key=api_key)
        path = writer.save_full(record)
        content = path.read_text(encoding="utf-8")
        assert api_key not in content
        assert mask_secret(api_key) in content

    def test_no_api_key_writes_plaintext(self, tmp_path):
        api_key = "sk-supersecret"
        record = _make_record(api_key)
        writer = PendingWriter(pending_dir=tmp_path)  # no api_key
        path = writer.save_full(record)
        content = path.read_text(encoding="utf-8")
        assert api_key in content


class TestSavePartialSanitizes:
    def test_api_key_not_in_written_file(self, tmp_path):
        api_key = "sk-supersecret"
        record = _make_record(api_key)
        writer = PendingWriter(pending_dir=tmp_path, api_key=api_key)
        path = writer.save_partial(record, reason="timeout")
        content = path.read_text(encoding="utf-8")
        assert api_key not in content
        assert mask_secret(api_key) in content

    def test_partial_reason_preserved(self, tmp_path):
        api_key = "sk-supersecret"
        record = _make_record(api_key)
        writer = PendingWriter(pending_dir=tmp_path, api_key=api_key)
        path = writer.save_partial(record, reason="timeout")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["_partial_reason"] == "timeout"

    def test_exception_partial_reason_mirrored(self, tmp_path):
        record = _make_record("sk-test")
        record = record.model_copy(
            update={
                "exception": {
                    "type": "validation_error",
                    "stage": "stage2",
                    "category": "c",
                    "message": "bad field",
                }
            }
        )
        writer = PendingWriter(pending_dir=tmp_path)
        path = writer.save_partial(record, reason="stage2_c")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["_partial_reason"] == "stage2_c"
        assert data["exception"]["partial_reason"] == "stage2_c"
