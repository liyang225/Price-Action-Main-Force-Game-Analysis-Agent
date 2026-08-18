"""Append extra KKAI thinking probes to kkai_thinking_test_result.txt."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

API_KEY = os.environ.get("KKAI_API_KEY", "").strip()
URL = "https://api.kkone.vip/v1/chat/completions"
MODEL = "claude-opus-4-5"
TIMEOUT = 180
OUT = Path(__file__).resolve().parents[1] / "kkai_thinking_test_result.txt"
MSG = [{"role": "user", "content": "1+1=? 只答数字"}]
_lines: list[str] = []


def log(msg: str) -> None:
    print(msg, flush=True)
    _lines.append(msg)


def post(payload: dict) -> tuple[int, dict | None]:
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
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", errors="replace")
        return exc.code, {"_error_body": err[:800]}


def summarize(name: str, payload: dict) -> None:
    log(f"\n=== {name} ===")
    log("request: " + json.dumps({k: v for k, v in payload.items() if k != "messages"}, ensure_ascii=False))
    try:
        status, body = post(payload)
    except Exception as exc:
        log(f"HTTP status=ERROR {exc!r}")
        return
    log(f"HTTP status={status}")
    if body is None or "_error_body" in body:
        log((body or {}).get("_error_body", "no body")[:800])
        return
    if not body.get("choices"):
        log("ERROR empty choices")
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


def stream_thinking() -> None:
    log("\n=== stream_thinking_object ===")
    payload = {
        "model": MODEL,
        "stream": True,
        "max_tokens": 2048,
        "thinking": {"type": "enabled", "budget_tokens": 1024},
        "messages": MSG,
    }
    log("request: " + json.dumps({k: v for k, v in payload.items() if k != "messages"}, ensure_ascii=False))
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
            log(f"HTTP status={resp.status}")
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
                choices = obj.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                if delta.get("reasoning_content"):
                    reasoning_chunks += 1
                if delta.get("content"):
                    content_chunks += 1
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", errors="replace")
        log(f"HTTP status={exc.code}")
        log(err[:800])
        return
    except Exception as exc:
        log(f"HTTP status=ERROR {exc!r}")
        return
    log(f"stream reasoning_content deltas={reasoning_chunks} content deltas={content_chunks}")


def main() -> int:
    if not API_KEY:
        log("Set KKAI_API_KEY first.")
        return 1
    base = {"model": MODEL, "stream": False, "messages": MSG, "max_tokens": 2048}
    summarize("enable_thinking_true", {**base, "enable_thinking": True})
    summarize("enable_thinking_budget_2048", {**base, "enable_thinking": True, "thinking_budget": 2048})
    summarize(
        "thinking_budget_8192_max_16384",
        {
            "model": MODEL,
            "stream": False,
            "max_tokens": 16384,
            "thinking": {"type": "enabled", "budget_tokens": 8192},
            "messages": MSG,
        },
    )
    stream_thinking()
    existing = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
    OUT.write_text(existing + "\n".join(_lines) + "\n", encoding="utf-8")
    log(f"appended to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
