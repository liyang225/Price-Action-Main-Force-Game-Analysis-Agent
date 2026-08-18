"""Tests for AI client factory routing."""
from __future__ import annotations

from pa_agent.ai.client_factory import create_ai_client
from pa_agent.ai.cursor_sdk_client import CursorSdkClient
from pa_agent.ai.deepseek_client import DeepSeekClient
from pa_agent.config.settings import AIProviderSettings


def test_create_ai_client_openclaw_cs_uses_cursor_sdk() -> None:
    settings = AIProviderSettings(
        model="openclaw_cs",
        base_url="",
        api_key="crsr_test",
    )
    client = create_ai_client(settings)
    assert isinstance(client, CursorSdkClient)


def test_create_ai_client_openclaw_uses_deepseek_client() -> None:
    settings = AIProviderSettings(
        model="openclaw",
        base_url="http://127.0.0.1:19000/v1",
        api_key="test",
    )
    client = create_ai_client(settings)
    assert isinstance(client, DeepSeekClient)
