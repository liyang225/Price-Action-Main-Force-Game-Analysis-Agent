"""Cursor SDK-backed client for PA Agent.

Implements the same interface as :class:`pa_agent.ai.deepseek_client.DeepSeekClient`
for orchestrators: ``stream_chat()`` and ``update_provider()``.
"""

from __future__ import annotations

import logging
import os
import queue
import secrets
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from pa_agent.ai.cursor_connector import resolve_cursor_sdk_model_id
from pa_agent.ai.deepseek_client import AIReply, AIUsage, CancelledError
from pa_agent.config.settings import AIProviderSettings

logger = logging.getLogger(__name__)

_PATCHED_CURSOR_SDK_BRIDGE = False
_PATCHED_CURSOR_SDK_AUTH_TOKENS = False
_PATCHED_CURSOR_SDK_BRIDGE_ARGV = False
_PATCHED_CURSOR_SDK_POPEN = False

_AUTH_TOKEN_FLAGS = (
    "--tool-callback-auth-token",
    "--store-callback-auth-token",
)


def _safe_bridge_auth_token() -> str:
    """Auth tokens must not start with '-' or the Node bridge argv parser rejects them."""
    while True:
        token = secrets.token_urlsafe(32)
        if token and not token.startswith("-"):
            return token


def _sanitize_bridge_auth_token(token: str | None) -> str:
    value = (token or "").strip()
    if not value or value.startswith("-"):
        return _safe_bridge_auth_token()
    return value


def _sanitize_cursor_bridge_argv(argv: list[str]) -> list[str]:
    """Ensure callback auth tokens are valid bridge argv values."""
    out = list(argv)
    i = 0
    while i < len(out):
        if out[i] in _AUTH_TOKEN_FLAGS:
            if i + 1 >= len(out):
                out.append(_safe_bridge_auth_token())
            else:
                out[i + 1] = _sanitize_bridge_auth_token(out[i + 1])
            i += 2
            continue
        i += 1
    return out


def _default_workspace() -> str:
    return os.environ.get("PA_AGENT_ROOT") or str(Path(__file__).resolve().parents[2])


def _patch_cursor_sdk_bridge_auth_tokens() -> None:
    """Patch cursor-sdk callback token generation (bridge argv parser bug on Windows)."""
    global _PATCHED_CURSOR_SDK_AUTH_TOKENS  # noqa: PLW0603
    if _PATCHED_CURSOR_SDK_AUTH_TOKENS:
        return
    try:
        import cursor_sdk._store_callback as store_cb  # type: ignore
        import cursor_sdk._tool_callback as tool_cb  # type: ignore
    except Exception:
        return
    tool_cb._new_auth_token = _safe_bridge_auth_token  # type: ignore[attr-defined]
    store_cb._new_auth_token = _safe_bridge_auth_token  # type: ignore[attr-defined]
    _PATCHED_CURSOR_SDK_AUTH_TOKENS = True


def _patch_cursor_sdk_bridge_argv() -> None:
    """Sanitize callback auth tokens when building bridge argv."""
    global _PATCHED_CURSOR_SDK_BRIDGE_ARGV  # noqa: PLW0603
    if _PATCHED_CURSOR_SDK_BRIDGE_ARGV:
        return
    try:
        import cursor_sdk._store_callback as store_cb  # type: ignore
        import cursor_sdk._tool_callback as tool_cb  # type: ignore
    except Exception:
        return

    _orig_tool = tool_cb.tool_callback_bridge_argv
    _orig_store = store_cb.store_callback_bridge_argv

    def _tool_argv(endpoint: Any) -> list[str]:
        return _sanitize_cursor_bridge_argv(_orig_tool(endpoint))

    def _store_argv(endpoint: Any) -> list[str]:
        return _sanitize_cursor_bridge_argv(_orig_store(endpoint))

    tool_cb.tool_callback_bridge_argv = _tool_argv  # type: ignore[assignment]
    store_cb.store_callback_bridge_argv = _store_argv  # type: ignore[assignment]
    _PATCHED_CURSOR_SDK_BRIDGE_ARGV = True


def _patch_cursor_sdk_subprocess_popen() -> None:
    """Last-resort argv sanitization right before bridge subprocess launch."""
    global _PATCHED_CURSOR_SDK_POPEN  # noqa: PLW0603
    if _PATCHED_CURSOR_SDK_POPEN:
        return
    try:
        import subprocess

        import cursor_sdk._bridge as bridge_mod  # type: ignore
    except Exception:
        return

    _orig_popen = subprocess.Popen

    def _popen(argv: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(argv, (list, tuple)) and argv:
            cmd0 = os.fspath(argv[0]).replace("\\", "/")
            if "cursor-sdk-bridge" in cmd0:
                if sys.platform == "win32":
                    kwargs["creationflags"] = (
                        int(kwargs.get("creationflags") or 0)
                        | subprocess.CREATE_NO_WINDOW
                    )
                return _orig_popen(_sanitize_cursor_bridge_argv(list(argv)), *args, **kwargs)
        return _orig_popen(argv, *args, **kwargs)

    subprocess.Popen = _popen  # type: ignore[misc, assignment]
    bridge_mod.subprocess.Popen = _popen  # type: ignore[attr-defined]
    _PATCHED_CURSOR_SDK_POPEN = True


def _ensure_cursor_sdk_patches() -> None:
    _patch_cursor_sdk_bridge_auth_tokens()
    _patch_cursor_sdk_bridge_argv()
    _patch_cursor_sdk_subprocess_popen()
    _patch_cursor_sdk_bridge_windows()


def _patch_cursor_sdk_bridge_windows() -> None:
    """Patch cursor_sdk bridge discovery on Windows.

    cursor-sdk v0.1.x uses selectors.DefaultSelector() to wait on the bridge
    subprocess stderr fd. On Windows, select() only supports sockets, so waiting
    on a pipe fd can raise WinError 10038.

    We replace the discovery reader with a thread + queue approach.
    """
    global _PATCHED_CURSOR_SDK_BRIDGE  # noqa: PLW0603
    if _PATCHED_CURSOR_SDK_BRIDGE:
        return
    if sys.platform != "win32":
        _PATCHED_CURSOR_SDK_BRIDGE = True
        return

    try:
        import cursor_sdk._bridge as bridge_mod  # type: ignore
        from cursor_sdk.errors import CursorSDKError  # type: ignore
    except Exception:
        # If cursor_sdk can't import, we'll fail later with a clearer message.
        return

    ready_prefix = getattr(bridge_mod, "READY_LINE_PREFIX", "cursor-sdk-bridge ready ")
    parse_line = getattr(bridge_mod, "parse_discovery_line", None)
    if parse_line is None:
        return

    def _read_discovery_threaded(process: Any, timeout: float) -> Any:
        if process.stderr is None:
            raise CursorSDKError("Bridge process stderr is unavailable")

        q: "queue.Queue[str | None]" = queue.Queue()

        def _reader() -> None:
            try:
                while True:
                    line = process.stderr.readline()
                    if not line:
                        q.put(None)
                        return
                    q.put(line)
            except Exception:
                q.put(None)

        t = threading.Thread(target=_reader, name="cursor_sdk_bridge_stderr", daemon=True)
        t.start()

        deadline = time.monotonic() + timeout
        stderr_lines: list[str] = []
        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                line = q.get(timeout=min(0.2, remaining))
            except queue.Empty:
                exit_code = process.poll()
                if exit_code is not None:
                    raise CursorSDKError(
                        f"Bridge exited before discovery with status {exit_code}: "
                        + "".join(stderr_lines)
                    )
                continue

            if line is None:
                exit_code = process.poll()
                if exit_code is not None:
                    raise CursorSDKError(
                        f"Bridge exited before discovery with status {exit_code}: "
                        + "".join(stderr_lines)
                    )
                continue

            stderr_lines.append(line)
            if line.startswith(ready_prefix):
                discovery = parse_line(line)
                if discovery is not None:
                    return discovery

        raise CursorSDKError("Timed out waiting for bridge discovery")

    bridge_mod._read_discovery = _read_discovery_threaded  # type: ignore[attr-defined]
    _PATCHED_CURSOR_SDK_BRIDGE = True


def _messages_to_prompt(messages: list[dict[str, Any]]) -> str:
    """Flatten OpenAI-style messages into a single prompt string for Cursor Agent."""
    parts: list[str] = []
    for m in messages:
        role = str(m.get("role", "user"))
        content = m.get("content", "")
        if isinstance(content, list):
            text_chunks = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_chunks.append(str(block.get("text", "")))
            content = "\n".join(text_chunks)
        parts.append(f"[{role}]\n{content}")
    return "\n\n".join(parts).strip()


def _consume_cursor_stream_event(
    event: Any,
    *,
    reasoning_parts: list[str],
    content_parts: list[str],
    on_reasoning_token: Callable[[str], None] | None,
    on_content_token: Callable[[str], None] | None,
) -> None:
    """Map one Cursor SDK stream event to PA Agent token callbacks."""
    update = getattr(event, "interaction_update", None)
    if update is not None:
        update_type = getattr(update, "type", None)
        if update_type == "thinking-delta":
            chunk = str(getattr(update, "text", "") or "")
            if chunk:
                reasoning_parts.append(chunk)
                if on_reasoning_token is not None:
                    on_reasoning_token(chunk)
            return
        if update_type == "text-delta":
            chunk = str(getattr(update, "text", "") or "")
            if chunk:
                content_parts.append(chunk)
                if on_content_token is not None:
                    on_content_token(chunk)
            return

    sdk_message = getattr(event, "sdk_message", None)
    if sdk_message is not None:
        message_type = getattr(sdk_message, "type", None)
        if message_type == "thinking":
            chunk = str(getattr(sdk_message, "text", "") or "")
            if chunk:
                reasoning_parts.append(chunk)
                if on_reasoning_token is not None:
                    on_reasoning_token(chunk)
            return
        if message_type == "assistant":
            message = getattr(sdk_message, "message", None)
            blocks = getattr(message, "content", ()) if message is not None else ()
            for block in blocks:
                chunk = str(getattr(block, "text", "") or "")
                if chunk:
                    content_parts.append(chunk)
                    if on_content_token is not None:
                        on_content_token(chunk)
            return

    step = getattr(event, "step", None)
    if step is not None:
        step_type = getattr(step, "type", None)
        if step_type == "thinkingMessage":
            message = getattr(step, "message", None)
            chunk = str(getattr(message, "text", "") or "") if message is not None else ""
            if chunk:
                reasoning_parts.append(chunk)
                if on_reasoning_token is not None:
                    on_reasoning_token(chunk)
            return
        if step_type == "assistantMessage":
            message = getattr(step, "message", None)
            chunk = str(getattr(message, "text", "") or "") if message is not None else ""
            if chunk:
                content_parts.append(chunk)
                if on_content_token is not None:
                    on_content_token(chunk)


class CursorSdkClient:
    """Cursor SDK wrapper with streaming reasoning/content callbacks."""

    def __init__(self, settings: AIProviderSettings, logger_: logging.Logger | None = None) -> None:
        self._settings = settings
        self._log = logger_ or logger

    def update_provider(self, settings: AIProviderSettings) -> None:
        self._settings = settings

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        on_reasoning_token: Callable[[str], None] | None = None,
        on_content_token: Callable[[str], None] | None = None,
        thinking: bool | None = None,
        reasoning_effort: str | None = None,
        cancel_token: Any | None = None,
        timeout_s: float = 20.0,
    ) -> AIReply:
        """Stream a Cursor Agent run and surface thinking/content to the GUI."""
        del reasoning_effort, timeout_s

        if cancel_token is not None and cancel_token.is_set():
            raise CancelledError("Request cancelled before API call")

        _ensure_cursor_sdk_patches()
        try:
            from cursor_sdk import Agent, AgentOptions, CursorClient, LocalAgentOptions  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "cursor-sdk 未安装或导入失败。请先安装依赖：pip install cursor-sdk"
            ) from exc

        prompt = _messages_to_prompt(messages)
        model_id = resolve_cursor_sdk_model_id(self._settings.model)
        cwd = _default_workspace()

        self._log.info("CursorSdkClient.stream_chat: model_id=%s chars=%d", model_id, len(prompt))

        reasoning_parts: list[str] = []
        content_parts: list[str] = []
        t0 = time.monotonic()

        client = CursorClient.launch_bridge(workspace=cwd)
        try:
            with Agent.create(
                AgentOptions(
                    api_key=self._settings.api_key,
                    model=model_id,
                    local=LocalAgentOptions(cwd=cwd),
                ),
                client=client,
            ) as agent:
                run = agent.send(prompt)
                try:
                    for event in run.events():
                        if cancel_token is not None and cancel_token.is_set():
                            if run.supports("cancel"):
                                run.cancel()
                            raise CancelledError("Request cancelled during Cursor stream")
                        _consume_cursor_stream_event(
                            event,
                            reasoning_parts=reasoning_parts,
                            content_parts=content_parts,
                            on_reasoning_token=on_reasoning_token,
                            on_content_token=on_content_token,
                        )
                finally:
                    result = run.wait()
        finally:
            client.close()

        latency_ms = (time.monotonic() - t0) * 1000
        reasoning_content = "".join(reasoning_parts)
        content = "".join(content_parts)
        final_text = content or str(getattr(result, "result", "") or "")
        if not content and final_text and on_content_token is not None:
            on_content_token(final_text)

        request_id = str(getattr(result, "id", "") or "")
        usage = AIUsage(prompt_tokens=0, cached_prompt_tokens=0, completion_tokens=0, total_tokens=0)

        if thinking:
            self._log.debug(
                "CursorSdkClient: thinking=%s requested; SDK AgentOptions has no thinking "
                "flag — reasoning depends on model stream events (got %d chars)",
                thinking,
                len(reasoning_content),
            )

        return AIReply(
            content=final_text,
            reasoning_content=reasoning_content,
            raw={
                "id": request_id,
                "status": getattr(result, "status", None),
                "model": model_id,
                "content": final_text,
                "reasoning_content": reasoning_content,
                "latency_ms": latency_ms,
            },
            usage=usage,
            request_id=request_id,
            latency_ms=latency_ms,
        )


_ensure_cursor_sdk_patches()
