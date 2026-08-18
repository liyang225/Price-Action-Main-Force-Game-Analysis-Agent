"""Probe QClaw / WorkBuddy thinking control. Run: python tools/probe_thinking_routes.py"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pa_agent.ai.deepseek_client import (
    DeepSeekClient,
    _effective_api_model,
    _resolve_thinking_params,
)
from pa_agent.ai.qclaw_connector import detect_qclaw, is_openclaw_model, qclaw_provider_settings
from pa_agent.ai.workbuddy_connector import (
    detect_workbuddy,
    is_workbuddy_route,
    resolve_workbuddy_api_model,
    workbuddy_provider_settings,
)
from pa_agent.config.settings import AIProviderSettings, load_settings

PROMPT = "只回答一个字：好。不要解释。"


def _banner(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


def _probe_label(settings: AIProviderSettings, thinking: bool, effort: str | None) -> str:
    extra, eff = _resolve_thinking_params(
        settings, thinking=thinking, reasoning_effort=effort
    )
    api_model = _effective_api_model(settings)
    return (
        f"route_model={settings.model!r} api_model={api_model!r} "
        f"thinking={thinking} effort={effort!r} "
        f"resolved_effort={eff!r} extra_body={json.dumps(extra, ensure_ascii=False)}"
    )


def _run_stream_client(
    settings: AIProviderSettings,
    *,
    thinking: bool,
    effort: str | None,
    label: str,
) -> dict[str, Any]:
    print(f"\n--- {label} ---")
    print(_probe_label(settings, thinking, effort))
    client = DeepSeekClient(settings)
    t0 = time.monotonic()
    try:
        reply = client.stream_chat(
            [{"role": "user", "content": PROMPT}],
            thinking=thinking,
            reasoning_effort=effort,
            timeout_s=120.0,
        )
    except Exception as exc:
        ms = (time.monotonic() - t0) * 1000
        print(f"ERROR after {ms:.0f}ms: {type(exc).__name__}: {exc}")
        return {"ok": False, "error": str(exc), "label": label, "mode": "pa_stream"}

    ms = (time.monotonic() - t0) * 1000
    reasoning = reply.reasoning_content or ""
    content = reply.content or ""
    usage = reply.usage
    result = {
        "ok": True,
        "label": label,
        "mode": "pa_stream",
        "latency_ms": round(ms),
        "reasoning_chars": len(reasoning),
        "content_chars": len(content),
        "content_preview": content[:120],
        "reasoning_preview": reasoning[:200],
        "completion_tokens": usage.completion_tokens,
        "prompt_tokens": usage.prompt_tokens,
    }
    print(
        f"OK {ms:.0f}ms | reasoning={len(reasoning)} chars | content={len(content)} chars | "
        f"completion_tokens={usage.completion_tokens}"
    )
    if reasoning:
        print(f"reasoning_preview: {reasoning[:180]!r}")
    print(f"content_preview: {content[:80]!r}")
    return result


def _run_raw_workbuddy_stream(
    settings: AIProviderSettings,
    *,
    label: str,
    extra_body: dict[str, Any] | None = None,
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    """Direct httpx stream to WorkBuddy with explicit payload knobs."""
    import httpx

    api_model = resolve_workbuddy_api_model(settings.model)
    url = f"{settings.base_url.rstrip('/')}/chat/completions"
    payload: dict[str, Any] = {
        "model": api_model,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": 256,
        "stream": True,
        "tool_choice": "none",
    }
    if extra_body:
        payload.update(extra_body)
    if reasoning_effort is not None:
        payload["reasoning_effort"] = reasoning_effort

    print(f"\n--- {label} ---")
    print(f"raw_payload_keys={sorted(payload.keys())}")
    if extra_body:
        print(f"extra_body={json.dumps(extra_body, ensure_ascii=False)}")

    t0 = time.monotonic()
    reasoning_parts: list[str] = []
    content_parts: list[str] = []
    try:
        with httpx.stream(
            "POST",
            url,
            headers={
                "Authorization": f"Bearer {settings.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=120.0,
        ) as resp:
            if resp.status_code != 200:
                body = resp.read().decode("utf-8", "replace")[:400]
                ms = (time.monotonic() - t0) * 1000
                print(f"HTTP {resp.status_code} after {ms:.0f}ms: {body}")
                return {
                    "ok": False,
                    "label": label,
                    "mode": "raw_stream",
                    "error": f"HTTP {resp.status_code}: {body}",
                }
            for line in resp.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                r = delta.get("reasoning_content")
                c = delta.get("content")
                if r:
                    reasoning_parts.append(str(r))
                if c:
                    content_parts.append(str(c))
    except Exception as exc:
        ms = (time.monotonic() - t0) * 1000
        print(f"ERROR after {ms:.0f}ms: {type(exc).__name__}: {exc}")
        return {"ok": False, "label": label, "mode": "raw_stream", "error": str(exc)}

    ms = (time.monotonic() - t0) * 1000
    reasoning = "".join(reasoning_parts)
    content = "".join(content_parts)
    result = {
        "ok": True,
        "label": label,
        "mode": "raw_stream",
        "latency_ms": round(ms),
        "reasoning_chars": len(reasoning),
        "content_chars": len(content),
        "content_preview": content[:120],
        "reasoning_preview": reasoning[:200],
    }
    print(
        f"OK {ms:.0f}ms | reasoning={len(reasoning)} chars | content={len(content)} chars"
    )
    if reasoning:
        print(f"reasoning_preview: {reasoning[:180]!r}")
    print(f"content_preview: {content[:80]!r}")
    return result


def _probe_route(name: str, settings: AIProviderSettings) -> list[dict[str, Any]]:
    _banner(name)
    print(f"base_url={settings.base_url}")
    print(f"model={settings.model} -> api={_effective_api_model(settings)}")
    cases = [
        ("thinking_off", False, None),
        ("effort_low", True, "low"),
        ("effort_max", True, "max"),
    ]
    out: list[dict[str, Any]] = []
    for label, thinking, effort in cases:
        out.append(
            _run_stream_client(
                settings,
                thinking=thinking,
                effort=effort,
                label=f"pa_{label}",
            )
        )
    return out


def _probe_workbuddy_raw(settings: AIProviderSettings) -> list[dict[str, Any]]:
    _banner("WorkBuddy raw payload experiments")
    return [
        _run_raw_workbuddy_stream(settings, label="raw_no_thinking_knobs"),
        _run_raw_workbuddy_stream(
            settings,
            label="raw_reasoning_effort_low",
            reasoning_effort="low",
        ),
        _run_raw_workbuddy_stream(
            settings,
            label="raw_reasoning_effort_max",
            reasoning_effort="max",
        ),
        _run_raw_workbuddy_stream(
            settings,
            label="raw_deepseek_thinking_disabled",
            extra_body={"thinking": {"type": "disabled"}},
        ),
        _run_raw_workbuddy_stream(
            settings,
            label="raw_deepseek_thinking_adaptive_low",
            extra_body={
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": "low"},
            },
        ),
        _run_raw_workbuddy_stream(
            settings,
            label="raw_deepseek_thinking_adaptive_max",
            extra_body={
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": "max"},
            },
        ),
    ]


def main() -> int:
    settings_path = ROOT / "config" / "settings.json"
    app_settings = load_settings(settings_path)
    provider = app_settings.provider
    results: dict[str, list[dict[str, Any]]] = {}

    if is_workbuddy_route(provider) or detect_workbuddy():
        wb = workbuddy_provider_settings(model=provider.model)
        if wb is not None:
            wb.api_key = provider.api_key or wb.api_key
            results["workbuddy_pa"] = _probe_route("WorkBuddy (PA stream_chat)", wb)
            results["workbuddy_raw"] = _probe_workbuddy_raw(wb)
        else:
            print("WorkBuddy detected but provider settings unavailable")

    if is_openclaw_model(provider.model) or detect_qclaw():
        qc = qclaw_provider_settings(
            model=provider.model if is_openclaw_model(provider.model) else None
        )
        if qc is not None:
            results["qclaw_pa"] = _probe_route("QClaw (PA stream_chat)", qc)
        else:
            print("QClaw detected but provider settings unavailable")

    if not results:
        if provider.api_key and provider.base_url:
            results["configured_provider"] = _probe_route("Configured provider", provider)
        else:
            print("No WorkBuddy/QClaw route available and no configured API key.")
            return 1

    _banner("SUMMARY")
    for route, rows in results.items():
        print(f"\n[{route}]")
        for row in rows:
            if not row.get("ok"):
                print(f"  {row['label']}: FAILED — {row.get('error')}")
                continue
            print(
                f"  {row['label']}: reasoning={row['reasoning_chars']} chars, "
                f"content={row['content_chars']} chars, "
                f"latency={row['latency_ms']}ms"
                + (
                    f", completion_tokens={row['completion_tokens']}"
                    if row.get("completion_tokens") is not None
                    else ""
                )
            )

    out_path = ROOT / "thinking_route_probe_result.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
