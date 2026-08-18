"""One-off PackyAPI thinking probe. Run: py -3 tools/test_packyapi_thinking.py"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

API_KEY = os.environ.get("PACKY_API_KEY", "").strip()
BASE = os.environ.get("PACKY_BASE_URL", "https://www.packyapi.com/v1").rstrip("/")
MODEL = os.environ.get("PACKY_MODEL", "claude-sonnet-4-5-20250929")
TIMEOUT = 180
OUT = Path(__file__).resolve().parents[1] / "packyapi_thinking_test_result.txt"
_lines: list[str] = []


def log(msg: str) -> None:
    print(msg, flush=True)
    _lines.append(msg)


def post(path: str, payload: dict) -> dict:
    url = f"{BASE}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get(path: str) -> dict:
    url = f"{BASE}{path}"
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def summarize(name: str, payload: dict) -> None:
    log(f"\n=== {name} ===")
    log(
        "request: "
        + json.dumps(
            {k: v for k, v in payload.items() if k != "messages"},
            ensure_ascii=False,
        )
    )
    try:
        body = post("/chat/completions", payload)
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


def stream_test(extra: dict, label: str) -> None:
    log(f"\n=== stream_{label} ===")
    payload = {
        "model": MODEL,
        "stream": True,
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": "1+1=? 只答数字"}],
        **extra,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}/chat/completions",
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


def list_models() -> None:
    log("\n=== models (first 40) ===")
    try:
        body = get("/models")
    except Exception as exc:
        log(f"models ERROR {exc!r}")
        return
    data = body.get("data", body if isinstance(body, list) else [])
    ids = sorted(m.get("id", "") for m in data if isinstance(m, dict))
    log(f"count={len(ids)}")
    for mid in ids[:40]:
        log(f"  {mid}")
    if len(ids) > 40:
        log(f"  ... +{len(ids) - 40} more")


def main() -> int:
    if not API_KEY:
        log("Set PACKY_API_KEY environment variable first.")
        OUT.write_text("\n".join(_lines) + "\n", encoding="utf-8")
        return 1
    log(f"BASE={BASE} MODEL={MODEL}")
    list_models()
    base = {
        "model": MODEL,
        "stream": False,
        "messages": [{"role": "user", "content": "1+1=? 只答数字"}],
    }
    summarize("baseline", {**base, "max_tokens": 512})
    summarize(
        "reasoning_effort_medium",
        {**base, "max_tokens": 2048, "reasoning_effort": "medium"},
    )
    summarize(
        "thinking_budget",
        {
            **base,
            "max_tokens": 2048,
            "thinking": {"type": "enabled", "budget_tokens": 8192},
        },
    )
    stream_test({"reasoning_effort": "medium"}, "reasoning_effort_medium")
    stream_test(
        {"thinking": {"type": "enabled", "budget_tokens": 8192}},
        "thinking_budget",
    )
    OUT.write_text("\n".join(_lines) + "\n", encoding="utf-8")
    log(f"written {OUT}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        OUT.write_text(f"FATAL: {exc!r}\n", encoding="utf-8")
        raise
