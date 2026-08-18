"""Live probe for PackyAPI (e.g. claude-officially group). Run: python tools/test_packy_officially_live.py"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pa_agent.ai.deepseek_client import DeepSeekClient, _OpenAI
from pa_agent.config.settings import load_settings

logging.basicConfig(level=logging.INFO)
OUT = ROOT / "packyapi_officially_test_result.txt"


def main() -> int:
    lines: list[str] = []

    def log(msg: str) -> None:
        print(msg, flush=True)
        lines.append(msg)

    settings = load_settings(ROOT / "config" / "settings.json")
    p = settings.provider
    if not (p.api_key or "").strip():
        log("ERROR: api_key empty (decrypt failed?)")
        OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return 1

    log(f"base_url={p.base_url}")
    log(f"model={p.model} thinking={p.thinking} effort={p.reasoning_effort}")

    if _OpenAI is None:
        log("ERROR: openai package not installed")
        OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return 1

    oai = _OpenAI(base_url=p.base_url, api_key=p.api_key)
    log("\n=== GET /models ===")
    try:
        listed = oai.models.list()
        ids = sorted(m.id for m in listed.data)
        log(f"model_count={len(ids)}")
        for mid in ids[:25]:
            log(f"  {mid}")
        if len(ids) > 25:
            log(f"  ... +{len(ids) - 25} more")
        sonnets = [i for i in ids if "sonnet" in i.lower()]
        log(f"sonnet_ids={sonnets[:15]}")
    except Exception as exc:
        log(f"models ERROR: {exc!r}")

    client = DeepSeekClient(p)

    log("\n=== chat (thinking off) ===")
    try:
        r0 = client.chat(
            [{"role": "user", "content": "1+1=? Reply with one digit only."}],
            thinking=False,
            timeout_s=120.0,
        )
        log(f"content={r0.content!r}")
        log(f"usage={r0.usage}")
    except Exception as exc:
        log(f"chat ERROR: {exc!r}")

    log("\n=== stream_chat (stage1-shaped system+user, thinking on) ===")
    try:
        from pa_agent.ai.prompt_assembler import PromptAssembler
        from pa_agent.data.base import Bar, KlineFrame

        bars = [
            Bar(
                seq=1,
                ts_open=0,
                open=1.0,
                high=1.1,
                low=0.9,
                close=1.05,
                volume=10.0,
                closed=True,
            )
        ]
        frame = KlineFrame(symbol="XAUUSDm", timeframe="15m", bars=bars)
        asm = PromptAssembler()
        s1_msgs = asm.build_stage1(frame)
        r_chunks_c = []
        r_chunks_r = []

        def on_c(t: str) -> None:
            r_chunks_c.append(t)

        def on_r(t: str) -> None:
            r_chunks_r.append(t)

        r2 = client.stream_chat(
            s1_msgs,
            thinking=True,
            reasoning_effort=p.reasoning_effort,
            on_reasoning_token=on_r,
            on_content_token=on_c,
            timeout_s=120.0,
        )
        log(f"stage1_shape content_len={len(r2.content)} reasoning_len={len(r2.reasoning_content)}")
        log(f"usage={r2.usage}")
        if r2.content.strip().startswith("{"):
            log("stage1_shape: content starts with JSON brace")
        else:
            log(f"stage1_shape content_preview={r2.content[:120]!r}")
    except Exception as exc:
        log(f"stage1_shape ERROR: {exc!r}")

    log("\n=== stream_chat (thinking on, mini JSON) ===")
    try:
        r_chunks_c: list[str] = []
        r_chunks_r: list[str] = []

        def on_c(t: str) -> None:
            r_chunks_c.append(t)

        def on_r(t: str) -> None:
            r_chunks_r.append(t)

        r1 = client.stream_chat(
            [
                {
                    "role": "user",
                    "content": (
                        "Output raw JSON in content only: "
                        '{"ok":true,"stage":"test"}. '
                        "No markdown fences. Keep reasoning brief."
                    ),
                }
            ],
            thinking=True,
            reasoning_effort=p.reasoning_effort,
            on_reasoning_token=on_r,
            on_content_token=on_c,
            timeout_s=180.0,
        )
        choice = (r1.raw.get("choices") or [{}])[0]
        log(f"content_len={len(r1.content)} reasoning_len={len(r1.reasoning_content)}")
        log(f"content_preview={r1.content[:400]!r}")
        log(f"stream_deltas reasoning={len(r_chunks_r)} content={len(r_chunks_c)}")
        log(f"usage={r1.usage}")
        log(f"finish_reason={choice.get('finish_reason')}")
        stripped = r1.content.strip()
        if stripped.startswith("{"):
            try:
                json.loads(stripped)
                log("content JSON: parse OK")
            except json.JSONDecodeError as exc:
                log(f"content JSON: parse FAIL {exc}")
        else:
            log("content JSON: no object in content")
    except Exception as exc:
        log(f"stream ERROR: {exc!r}")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"\nwritten {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
