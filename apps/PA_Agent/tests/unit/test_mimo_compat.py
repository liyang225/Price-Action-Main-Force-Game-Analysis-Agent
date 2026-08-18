"""Unit tests for Xiaomi MiMo API compatibility helpers."""
from __future__ import annotations

from pa_agent.ai.mimo_compat import (
    ReasoningCache,
    build_assistant_api_message,
    is_mimo_provider,
    mimo_max_output_tokens,
    patch_messages_for_mimo,
    resolve_mimo_thinking_extra_body,
    store_reasoning_from_response,
)


def test_is_mimo_provider_detects_base_url_and_model() -> None:
    assert is_mimo_provider("https://api.xiaomimimo.com/v1", "gpt-4") is True
    assert is_mimo_provider("https://api.deepseek.com", "mimo-v2-flash") is True
    assert is_mimo_provider("https://api.deepseek.com", "deepseek-v4-pro") is False


def test_resolve_mimo_thinking_extra_body() -> None:
    on = resolve_mimo_thinking_extra_body(thinking=True)
    off = resolve_mimo_thinking_extra_body(thinking=False)
    assert on == {"chat_template_kwargs": {"enable_thinking": True}}
    assert off == {"chat_template_kwargs": {"enable_thinking": False}}


def test_patch_messages_injects_reasoning_for_tool_calls() -> None:
    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "x", "arguments": "{}"}}],
        },
    ]
    patched = patch_messages_for_mimo(messages, model="mimo-v2-flash")
    assert patched[1]["reasoning_content"] == ""


def test_patch_messages_uses_cached_reasoning_for_tool_calls() -> None:
    cache = ReasoningCache()
    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "x", "arguments": "{}"}}],
        },
    ]
    store_reasoning_from_response(
        [{"role": "user", "content": "hi"}],
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": "cached-thought",
            "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "x", "arguments": "{}"}}],
        },
        cache,
    )
    patched = patch_messages_for_mimo(messages, model="mimo-v2-flash", reasoning_cache=cache)
    assert patched[1]["reasoning_content"] == "cached-thought"


def test_build_assistant_api_message_preserves_reasoning() -> None:
    msg = build_assistant_api_message("answer", reasoning_content="thought")
    assert msg["role"] == "assistant"
    assert msg["content"] == "answer"
    assert msg["reasoning_content"] == "thought"


def test_mimo_max_output_tokens_flash_vs_pro() -> None:
    assert mimo_max_output_tokens("mimo-v2-flash") == 65_536
    assert mimo_max_output_tokens("mimo-v2.5-pro") == 131_072
