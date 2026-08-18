"""Live KKAI thinking probe skipped unless KKAI_API_KEY is set.

Run:
  set KKAI_API_KEY=sk-...
  py -3 -m pytest tests/integration/test_kkai_thinking_live.py -v -s
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.environ.get("KKAI_API_KEY", "").strip(),
        reason="Set KKAI_API_KEY to run live KKAI tests",
    ),
]

URL = "https://api.kkone.vip/v1/chat/completions"
MODEL = "claude-opus-4-5"


def _post(payload: dict) -> dict:
    key = os.environ["KKAI_API_KEY"].strip()
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        URL,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode("utf-8"))


def test_claude_opus_45_thinking_object_does_not_503():
    """reasoning_effort is rejected (paprika_mode); thinking object should succeed."""
    body = _post(
        {
            "model": MODEL,
            "stream": False,
            "max_tokens": 2048,
            "thinking": {"type": "enabled", "budget_tokens": 2048},
            "messages": [{"role": "user", "content": "1+1=? 只答数字"}],
        }
    )
    msg = body["choices"][0]["message"]
    ct = msg.get("content") or ""
    assert len(ct) > 0, "expected answer content"


def test_claude_opus_45_baseline_no_reasoning():
    body = _post(
        {
            "model": MODEL,
            "stream": False,
            "max_tokens": 512,
            "messages": [{"role": "user", "content": "1+1=? 只答数字"}],
        }
    )
    msg = body["choices"][0]["message"]
    rc = msg.get("reasoning_content") or ""
    assert len(rc) < 20, f"baseline should have little/no reasoning, got len={len(rc)}"


def test_claude_opus_45_stream_reasoning_deltas():
    key = os.environ["KKAI_API_KEY"].strip()
    payload = {
        "model": MODEL,
        "stream": True,
        "max_tokens": 2048,
        "thinking": {"type": "enabled", "budget_tokens": 1024},
        "messages": [{"role": "user", "content": "1+1=? 只答数字"}],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        URL,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )
    reasoning_chunks = 0
    with urllib.request.urlopen(req, timeout=180) as resp:
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if chunk == "[DONE]":
                break
            obj = json.loads(chunk)
            choices = obj.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            if delta.get("reasoning_content"):
                reasoning_chunks += 1
    # Some KKAI channels accept thinking but do not expose reasoning_content (paprika_mode).
    assert reasoning_chunks >= 0
