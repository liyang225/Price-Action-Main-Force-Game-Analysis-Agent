"""Unit tests for Cursor SDK stream event mapping."""
from __future__ import annotations

from types import SimpleNamespace

from pa_agent.ai.cursor_sdk_client import (
    _consume_cursor_stream_event,
    _ensure_cursor_sdk_patches,
    _patch_cursor_sdk_bridge_auth_tokens,
    _safe_bridge_auth_token,
    _sanitize_cursor_bridge_argv,
)


def test_safe_bridge_auth_token_never_starts_with_dash() -> None:
    for _ in range(100):
        assert not _safe_bridge_auth_token().startswith("-")


def test_patch_cursor_sdk_bridge_auth_tokens() -> None:
    _patch_cursor_sdk_bridge_auth_tokens()
    import cursor_sdk._tool_callback as tool_cb  # type: ignore

    for _ in range(20):
        assert not tool_cb._new_auth_token().startswith("-")


def test_sanitize_cursor_bridge_argv_fixes_dash_prefixed_token() -> None:
    argv = [
        "cursor-sdk-bridge.js",
        "--tool-callback-url",
        "http://127.0.0.1:1",
        "--tool-callback-auth-token",
        "-startsWithDash",
    ]
    fixed = _sanitize_cursor_bridge_argv(argv)
    assert fixed[4] != "-startsWithDash"
    assert not fixed[4].startswith("-")


def test_bridge_launches_after_cursor_sdk_patches() -> None:
    _ensure_cursor_sdk_patches()
    from cursor_sdk import CursorClient  # type: ignore

    client = CursorClient.launch_bridge(workspace=".")
    try:
        assert client is not None
    finally:
        client.close()


def test_consume_thinking_delta_emits_reasoning_callback() -> None:
    reasoning: list[str] = []
    content: list[str] = []
    emitted: list[str] = []

    event = SimpleNamespace(
        interaction_update=SimpleNamespace(type="thinking-delta", text="alpha "),
        sdk_message=None,
        step=None,
    )
    _consume_cursor_stream_event(
        event,
        reasoning_parts=reasoning,
        content_parts=content,
        on_reasoning_token=emitted.append,
        on_content_token=None,
    )

    assert reasoning == ["alpha "]
    assert emitted == ["alpha "]
    assert content == []


def test_consume_text_delta_emits_content_callback() -> None:
    reasoning: list[str] = []
    content: list[str] = []
    emitted: list[str] = []

    event = SimpleNamespace(
        interaction_update=SimpleNamespace(type="text-delta", text='{"ok":'),
        sdk_message=None,
        step=None,
    )
    _consume_cursor_stream_event(
        event,
        reasoning_parts=reasoning,
        content_parts=content,
        on_reasoning_token=None,
        on_content_token=emitted.append,
    )

    assert content == ['{"ok":']
    assert emitted == ['{"ok":']
    assert reasoning == []
