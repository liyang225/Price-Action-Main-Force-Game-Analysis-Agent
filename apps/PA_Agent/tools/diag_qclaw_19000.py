"""Diagnose QClaw internal proxy (19000) vs relay (19004) payloads."""
from __future__ import annotations

import json
from qclaw_gateway_token import read_gateway_token
import sys

import httpx

TOKEN = read_gateway_token()
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

ENDPOINTS = [
    ("direct-19000", "http://127.0.0.1:19000/proxy/llm/chat/completions"),
    ("relay-19004", "http://127.0.0.1:19004/chat/completions"),
]

PAYLOADS = [
    ("minimal", {
        "model": "pool-deepseek-v4-pro",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 16,
        "stream": False,
    }),
    ("reasoning_effort", {
        "model": "pool-deepseek-v4-pro",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 16,
        "stream": False,
        "reasoning_effort": "low",
    }),
    ("adaptive-thinking", {
        "model": "pool-deepseek-v4-pro",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 16,
        "stream": False,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "low"},
    }),
    ("deepseek-v4-pro", {
        "model": "deepseek-v4-pro",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 16,
        "stream": False,
        "reasoning_effort": "max",
    }),
    ("modelroute", {
        "model": "modelroute",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 16,
        "stream": False,
    }),
    ("stream-adaptive", {
        "model": "pool-deepseek-v4-pro",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 16,
        "stream": True,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "low"},
    }),
]


def try_post(url: str, payload: dict) -> tuple[int, str]:
    try:
        if payload.get("stream"):
            with httpx.stream(
                "POST", url, headers=HEADERS, json=payload, timeout=30.0
            ) as r:
                chunks = []
                for line in r.iter_lines():
                    if line.startswith("data: "):
                        chunks.append(line[6:][:120])
                    if len(chunks) >= 3:
                        break
                body = " | ".join(chunks) if chunks else r.text[:200]
                return r.status_code, body
        r = httpx.post(url, headers=HEADERS, json=payload, timeout=30.0)
        text = r.text[:300]
        if r.status_code == 200 and "reasoning_content" in text:
            text += " [HAS_REASONING]"
        return r.status_code, text
    except Exception as exc:
        return -1, str(exc)[:200]


def main() -> int:
    print("=== QClaw 19000 / 19004 payload matrix ===\n")
    for ep_name, url in ENDPOINTS:
        print(f"--- {ep_name} {url} ---")
        for pname, payload in PAYLOADS:
            code, body = try_post(url, payload)
            print(f"  [{pname}] {code}: {body[:180]}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
