"""Tests for provider quota (402) detection and retry policy."""
from __future__ import annotations

from pa_agent.ai.json_validator import JsonValidator, ValidationError
from pa_agent.ai.provider_errors import (
    PROVIDER_QUOTA_USER_MESSAGE,
    is_provider_quota_exhausted,
)
from pa_agent.ai.retry_policy import max_retries_for_category, should_retry


class _Settings:
    retry_enabled = True
    retry_max = 3
    retry_max_semantic = 1
    retry_stage2 = True


def test_is_provider_quota_exhausted_openclaw_message():
    text = "402 您的积分已用完，可通过购买或参与活动获取更多积分(错误码: 402)"
    assert is_provider_quota_exhausted(text)


def test_is_provider_quota_exhausted_negative():
    assert not is_provider_quota_exhausted('{"decision": {}}')
    assert not is_provider_quota_exhausted("Response is plain text, not JSON")


def test_validator_category_e_for_402_plain_text():
    validator = JsonValidator()
    text = "402 您的积分已用完，可通过购买或参与活动获取更多积分(错误码: 402)"
    result = validator.validate("stage2", text)
    assert isinstance(result, ValidationError)
    assert result.category == "e"
    assert result.message == PROVIDER_QUOTA_USER_MESSAGE
    assert "provider:quota_exhausted" in result.invalid_fields


def test_should_not_retry_category_e():
    assert max_retries_for_category("e", _Settings()) == 0
    assert not should_retry("e", [], [], attempt=0, settings=_Settings())


def test_should_still_retry_category_d_non_quota():
    assert should_retry("d", [], [], attempt=0, settings=_Settings())
