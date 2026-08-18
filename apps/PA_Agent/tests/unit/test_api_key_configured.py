"""Tests for API key presence helper."""
from __future__ import annotations

from pa_agent.config.settings import Settings, provider_api_key_configured


def test_provider_api_key_configured_empty() -> None:
    s = Settings()
    s.provider.api_key = ""
    assert not provider_api_key_configured(s)
    assert not provider_api_key_configured(None)


def test_provider_api_key_configured_whitespace() -> None:
    s = Settings()
    s.provider.api_key = "   "
    assert not provider_api_key_configured(s)


def test_provider_api_key_configured_present() -> None:
    s = Settings()
    s.provider.api_key = "sk-test"
    assert provider_api_key_configured(s)
