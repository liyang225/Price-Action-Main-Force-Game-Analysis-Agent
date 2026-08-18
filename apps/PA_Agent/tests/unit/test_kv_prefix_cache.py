"""Unit tests for DeepSeek KV prefix-chain provider detection."""
from __future__ import annotations

from pa_agent.ai.deepseek_client import supports_kv_prefix_chain
from pa_agent.config.settings import AIProviderSettings


def test_prefix_chain_enabled_for_deepseek_native():
    settings = AIProviderSettings(
        base_url="https://api.deepseek.com",
        model="deepseek-reasoner",
        api_key="sk-test",
    )
    assert supports_kv_prefix_chain(settings) is True


def test_prefix_chain_disabled_for_openclaw_agent():
    settings = AIProviderSettings(
        base_url="https://api.deepseek.com",
        model="openclaw",
        api_key="sk-test",
    )
    assert supports_kv_prefix_chain(settings) is False


def test_prefix_chain_disabled_for_openclaw_wb():
    settings = AIProviderSettings(
        base_url="https://gateway.example.com",
        model="openclaw_wb",
        api_key="sk-test",
    )
    assert supports_kv_prefix_chain(settings) is False


def test_prefix_chain_disabled_for_openclaw_cs():
    settings = AIProviderSettings(
        base_url="http://127.0.0.1:58579/v1",
        model="openclaw_cs",
        api_key="sk-test",
    )
    assert supports_kv_prefix_chain(settings) is False
