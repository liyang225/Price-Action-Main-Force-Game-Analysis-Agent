"""Xiaomi MiMo API compatibility helpers.

Reference: https://github.com/Miku-cy/MiMo-API-Compat-Fix

MiMo uses an OpenAI-compatible API with DeepSeek-style ``reasoning_content``.
When an assistant message contains ``tool_calls`` but omits ``reasoning_content``,
the upstream API returns HTTP 400. Multi-turn conversations should also pass back
historical ``reasoning_content`` for best results.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

MIMO_DEFAULT_BASE_URL = "https://api.xiaomimimo.com/v1"
MIMO_TOKEN_PLAN_BASE_URL = "https://token-plan-cn.xiaomimimo.com/v1"

MIMO_MAX_OUTPUT_TOKENS_DEFAULT = 131_072
MIMO_MAX_OUTPUT_TOKENS_FLASH = 65_536

MIMO_REASONING_MODELS: tuple[str, ...] = (
    "mimo-v2.5",
    "mimo-v2.5-pro",
    "mimo-v2-pro",
    "mimo-v2-omni",
    "mimo-v2-flash",
    "mimo-v2.5-flash",
)


def is_mimo_base_url(base_url: str) -> bool:
    url = (base_url or "").lower()
    return "xiaomimimo.com" in url or "mimo.xiaomi.com" in url


def is_mimo_model(model: str) -> bool:
    m = (model or "").strip().lower()
    if not m:
        return False
    if m.startswith(("openclaw", "openclaw_wb", "openclaw_cs")):
        return False
    return "mimo" in m


def is_mimo_provider(base_url: str, model: str) -> bool:
    return is_mimo_base_url(base_url) or is_mimo_model(model)


def is_mimo_reasoning_model(model: str) -> bool:
    m = (model or "").strip().lower()
    return any(token in m for token in MIMO_REASONING_MODELS) or is_mimo_model(model)


def mimo_max_output_tokens(model: str) -> int:
    m = (model or "").strip().lower()
    if "flash" in m:
        return MIMO_MAX_OUTPUT_TOKENS_FLASH
    return MIMO_MAX_OUTPUT_TOKENS_DEFAULT


def resolve_mimo_thinking_extra_body(*, thinking: bool) -> dict[str, Any]:
    """Return MiMo ``chat_template_kwargs`` block for extra_body."""
    return {"chat_template_kwargs": {"enable_thinking": bool(thinking)}}


def build_assistant_api_message(
    content: str,
    *,
    reasoning_content: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    preserve_reasoning_for_mimo: bool = False,
) -> dict[str, Any]:
    """Build an assistant message suitable for resending to MiMo."""
    msg: dict[str, Any] = {
        "role": "assistant",
        "content": content if content is not None else "",
    }
    if tool_calls:
        msg["tool_calls"] = tool_calls
    reasoning = (reasoning_content or "").strip()
    if reasoning:
        msg["reasoning_content"] = reasoning
    elif preserve_reasoning_for_mimo and tool_calls:
        msg["reasoning_content"] = ""
    return msg


class ReasoningCache:
    """In-memory cache for assistant reasoning_content (tool-call replay)."""

    def __init__(self, *, max_age_seconds: int = 86_400, max_entries: int = 2_048) -> None:
        self._cache: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._max_age = max_age_seconds
        self._max_entries = max_entries

    @staticmethod
    def _make_key(
        messages: list[dict[str, Any]],
        assistant_index: int,
        tool_call_ids: list[str],
    ) -> str:
        context_msgs = messages[:assistant_index]
        context_str = json.dumps(context_msgs, sort_keys=True, ensure_ascii=False)
        context_hash = hashlib.md5(context_str.encode()).hexdigest()[:12]
        tc_str = "|".join(sorted(tool_call_ids))
        return f"{context_hash}:{assistant_index}:{tc_str}"

    def get(
        self,
        messages: list[dict[str, Any]],
        assistant_index: int,
        tool_call_ids: list[str],
    ) -> str | None:
        key = self._make_key(messages, assistant_index, tool_call_ids)
        with self._lock:
            entry = self._cache.get(key)
            if not entry:
                return None
            if time.time() - float(entry.get("timestamp", 0)) > self._max_age:
                del self._cache[key]
                return None
            value = entry.get("reasoning_content")
            return str(value) if value else None

    def store(
        self,
        messages: list[dict[str, Any]],
        assistant_index: int,
        tool_call_ids: list[str],
        reasoning_content: str,
    ) -> None:
        if not reasoning_content:
            return
        key = self._make_key(messages, assistant_index, tool_call_ids)
        with self._lock:
            self._cache[key] = {
                "reasoning_content": reasoning_content,
                "timestamp": time.time(),
            }
            if len(self._cache) > self._max_entries:
                oldest = min(
                    self._cache,
                    key=lambda k: float(self._cache[k].get("timestamp", 0)),
                )
                del self._cache[oldest]


def patch_messages_for_mimo(
    messages: list[dict[str, Any]],
    *,
    model: str,
    reasoning_cache: ReasoningCache | None = None,
) -> list[dict[str, Any]]:
    """Patch outgoing messages for MiMo API compatibility."""
    if not messages:
        return messages

    patched = copy.deepcopy(messages)
    is_reasoning = is_mimo_reasoning_model(model)
    patch_count = 0

    for i, msg in enumerate(patched):
        if msg.get("role") != "assistant":
            continue

        if msg.get("content") is None:
            msg["content"] = ""
            patch_count += 1

        has_tool_calls = bool(msg.get("tool_calls"))
        has_reasoning = "reasoning_content" in msg

        if has_tool_calls and not has_reasoning:
            cached: str | None = None
            if reasoning_cache is not None:
                tool_call_ids = [
                    str(tc.get("id", ""))
                    for tc in msg.get("tool_calls", [])
                    if isinstance(tc, dict)
                ]
                cached = reasoning_cache.get(patched, i, tool_call_ids)
            msg["reasoning_content"] = cached if cached is not None else ""
            patch_count += 1

        if not is_reasoning and has_reasoning:
            del msg["reasoning_content"]
            patch_count += 1

    if patch_count:
        logger.debug("MiMo message patch applied to %d assistant field(s)", patch_count)
    return patched


def store_reasoning_from_response(
    request_messages: list[dict[str, Any]],
    response_message: dict[str, Any],
    reasoning_cache: ReasoningCache | None,
) -> None:
    """Cache reasoning_content from an assistant response with tool_calls."""
    if reasoning_cache is None:
        return
    reasoning = str(response_message.get("reasoning_content") or "")
    tool_calls = response_message.get("tool_calls") or []
    if not reasoning or not tool_calls:
        return
    tool_call_ids = [
        str(tc.get("id", "")) for tc in tool_calls if isinstance(tc, dict)
    ]
    assistant_index = len(request_messages)
    reasoning_cache.store(request_messages, assistant_index, tool_call_ids, reasoning)


def response_message_dict(content: str, reasoning_content: str, msg: Any) -> dict[str, Any]:
    """Normalise an SDK message object into a plain dict for caching."""
    out: dict[str, Any] = {
        "role": "assistant",
        "content": content,
        "reasoning_content": reasoning_content,
    }
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls:
        serialised: list[dict[str, Any]] = []
        for tc in tool_calls:
            if isinstance(tc, dict):
                serialised.append(tc)
                continue
            fn = getattr(tc, "function", None)
            serialised.append(
                {
                    "id": getattr(tc, "id", ""),
                    "type": getattr(tc, "type", "function"),
                    "function": {
                        "name": getattr(fn, "name", "") if fn else "",
                        "arguments": getattr(fn, "arguments", "") if fn else "",
                    },
                }
            )
        out["tool_calls"] = serialised
    return out
