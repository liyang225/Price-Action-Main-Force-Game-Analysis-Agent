"""One-off KKAI thinking probe for claude-opus-4-5. Run: python tools/test_kkai_thinking.py"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import os

API_KEY = os.environ.get("KKAI_API_KEY", "").strip()
URL = "https://api.kkone.vip/v1/chat/completions"
MODEL = "claude-opus-4-5"
TIMEOUT = 180
OUT = Path(__file__).resolve().parents[1] / "kkai_thinking_test_result.txt"
_lines: list[str] = []


def log(msg: str) -> None:
    print(msg, flush=True)
    _lines.append(msg)


def post(payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        URL,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def summarize(name: str, payload: dict) -> None:
    log(f"\n=== {name} ===")
    log("request: " + json.dumps({k: v for k, v in payload.items() if k != "messages"}, ensure_ascii=False))
    try:
        body = post(payload)
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", errors="replace")
        log(f"HTTP {exc.code} {err[:800]}")
        return
    except Exception as exc:
        log(f"ERROR {exc!r}")
        return
    msg = body["choices"][0]["message"]
    rc = msg.get("reasoning_content") or ""
    ct = msg.get("content") or ""
    log(f"reasoning_len={len(rc)} content_len={len(ct)}")
    if rc:
        log("reasoning_preview: " + rc[:300].replace("\n", " "))
    if ct:
        log("content_preview: " + ct[:150].replace("\n", " "))
    usage = body.get("usage") or {}
    log("usage: " + json.dumps(usage, ensure_ascii=False))


def stream_test() -> None:
    log("\n=== stream_reasoning_effort_medium ===")
    payload = {
        "model": MODEL,
        "stream": True,
        "max_tokens": 2048,
        "reasoning_effort": "medium",
        "messages": [{"role": "user", "content": "1+1=? 只答数字"}],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        URL,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
    )
    reasoning_chunks = 0
    content_chunks = 0
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    obj = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                delta = obj.get("choices", [{}])[0].get("delta", {})
                if delta.get("reasoning_content"):
                    reasoning_chunks += 1
                if delta.get("content"):
                    content_chunks += 1
    except Exception as exc:
        log(f"ERROR {exc!r}")
        return
    log(f"stream reasoning deltas={reasoning_chunks} content deltas={content_chunks}")


def main() -> int:
    if not API_KEY:
        log("Set KKAI_API_KEY environment variable first.")
        OUT.write_text("\n".join(_lines) + "\n", encoding="utf-8")
        return 1
    base = {"model": MODEL, "stream": False, "messages": [{"role": "user", "content": "1+1=? 只答数字"}]}
    summarize("baseline", {**base, "max_tokens": 512})
    summarize("reasoning_effort_medium", {**base, "max_tokens": 2048, "reasoning_effort": "medium"})
    summarize("reasoning_effort_low", {**base, "max_tokens": 2048, "reasoning_effort": "low"})
    summarize(
        "thinking_object",
        {**base, "max_tokens": 2048, "thinking": {"type": "enabled", "budget_tokens": 1024}},
    )
    stream_test()
    OUT.write_text("\n".join(_lines) + "\n", encoding="utf-8")
    log(f"written {OUT}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        OUT.write_text(f"FATAL: {exc!r}\n", encoding="utf-8")
        raise
