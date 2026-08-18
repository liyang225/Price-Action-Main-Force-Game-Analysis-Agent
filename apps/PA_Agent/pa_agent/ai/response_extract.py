"""Extract visible content / thinking text from persisted API response dicts."""

from __future__ import annotations

from typing import Any


def reasoning_from_response(response: dict[str, Any] | None) -> str:
    """Return thinking text from DeepSeek flat or OpenAI choices-shaped payloads."""
    if not isinstance(response, dict):
        return ""
    top = response.get("reasoning_content")
    if isinstance(top, str) and top.strip():
        return top
    choices = response.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message") or {}
    return str(msg.get("reasoning_content") or "")


def content_from_response(response: dict[str, Any] | None) -> str:
    """Return assistant answer text from flat or choices-shaped payloads."""
    if not isinstance(response, dict):
        return ""
    top = response.get("content")
    if isinstance(top, str) and top.strip():
        return top
    choices = response.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message") or {}
    return str(msg.get("content") or "")
