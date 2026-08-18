#!/usr/bin/env python3
"""Minimal live two-stage smoke using config/settings.json (real API).

Usage (from repo root):
  python tools/run_live_two_stage_smoke.py

Exit 0 if both stages complete without network/cancel errors; validation may still fail.
"""
from __future__ import annotations

import json
import logging
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pa_agent.ai.deepseek_client import DeepSeekClient
from pa_agent.ai.prompt_assembler import PromptAssembler
from pa_agent.ai.router import route_strategy_files
from pa_agent.config.paths import EXPERIENCE_DIR, PROMPT_DIR, SETTINGS_JSON_PATH
from pa_agent.config.settings import load_settings, provider_api_key_configured
from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
from pa_agent.records.experience_reader import ExperienceReader
from pa_agent.records.pending_writer import PendingWriter
from pa_agent.util.event_bus import EventBus
from pa_agent.util.threading import CancelToken, OrchestratorEvent
from pa_agent.data.base import KlineBar, KlineFrame
from pa_agent.data.snapshot import compute_indicators

# Fewer bars than production default to keep prompt smaller for smoke.
SMOKE_BAR_COUNT = 30


def _make_trending_frame(symbol: str, timeframe: str, n: int) -> KlineFrame:
    """Synthetic bullish channel bars (newest-first) so gates can reach proceed."""
    base_ts = 1_700_000_000_000
    step_ms = 900_000
    bars: list[KlineBar] = [
        KlineBar(
            seq=1,
            ts_open=base_ts,
            open=2005.0,
            high=2012.0,
            low=2003.0,
            close=2010.0,
            volume=120.0,
            closed=False,
        )
    ]
    for i in range(2, n + 2):
        o = 1990.0 + i * 0.8
        c = o + 1.2
        bars.append(
            KlineBar(
                seq=i,
                ts_open=base_ts - (i - 1) * step_ms,
                open=o,
                high=c + 0.8,
                low=o - 0.5,
                close=c,
                volume=100.0 + i,
                closed=True,
            )
        )
    indicators = compute_indicators(bars)
    return KlineFrame(
        symbol=symbol,
        timeframe=timeframe,
        bars=tuple(bars),
        snapshot_ts_local_ms=base_ts,
        indicators=indicators,
    )


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--relax-validation",
        action="store_true",
        help="Use lenient test validator (API/prompt still from settings.json)",
    )
    parser.add_argument(
        "--hybrid-stage1-fixture",
        action="store_true",
        help="Stage1 returns validated fixture JSON; Stage2 uses real API (full pipeline smoke)",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("live_smoke")

    settings = load_settings(SETTINGS_JSON_PATH)
    if not provider_api_key_configured(settings):
        log.error("No API key in %s (provider.api_key / api_key_encrypted)", SETTINGS_JSON_PATH)
        return 2

    log.info(
        "Provider: model=%s base_url=%s thinking=%s",
        settings.provider.model,
        settings.provider.base_url,
        settings.provider.thinking,
    )

    real_client = DeepSeekClient(settings=settings.provider, logger_=log)

    if args.hybrid_stage1_fixture:
        import json
        from unittest.mock import MagicMock

        from copy import deepcopy

        from tests.fixtures.gate_trace import (
            make_bar_by_bar_summary,
            make_mandatory_gate_trace_proceed,
        )
        from tests.integration.conftest import VALID_STAGE1, make_reply

        def _stage1_fixture_reply(bar_count: int) -> MagicMock:
            s1 = deepcopy(VALID_STAGE1)
            s1["bar_by_bar_summary"] = make_bar_by_bar_summary(bar_count)
            s1["gate_trace"] = make_mandatory_gate_trace_proceed(max_seq=bar_count)
            return make_reply(s1)

        class _HybridClient:
            def __init__(self, inner: DeepSeekClient, bar_count: int) -> None:
                self._inner = inner
                self._bar_count = bar_count
                self._calls = 0

            def stream_chat(self, messages, **kwargs):
                if self._calls == 0:
                    self._calls += 1
                    log.warning("Stage1: using validated fixture (Stage2 will call real API)")
                    return _stage1_fixture_reply(self._bar_count)
                return self._inner.stream_chat(messages, **kwargs)

        bar_n = 8 if args.hybrid_stage1_fixture else SMOKE_BAR_COUNT
        client = _HybridClient(real_client, bar_n)
        log.info("Mode: hybrid (fixture Stage1 + live Stage2), bars=%d", bar_n)
    else:
        client = real_client
        bar_n = SMOKE_BAR_COUNT
    exp_reader = ExperienceReader(experience_dir=EXPERIENCE_DIR, logger=log)
    assembler = PromptAssembler(
        prompt_dir=PROMPT_DIR,
        experience_reader=exp_reader,
        prompt_settings=settings.prompt,
    )
    if args.relax_validation:
        from tests.fixtures.validators import schema_test_validator

        validator = schema_test_validator()
        log.warning("Using lenient validator (--relax-validation); production uses strict from settings.json")
    else:
        from pa_agent.ai.json_validator import JsonValidator

        validator = JsonValidator(settings.validation)
    event_bus = EventBus()

    with tempfile.TemporaryDirectory(prefix="pa_agent_smoke_") as tmp:
        pending_writer = PendingWriter(
            pending_dir=Path(tmp),
            event_bus=event_bus,
            api_key=settings.provider.api_key,
        )
        orchestrator = TwoStageOrchestrator(
            client=client,
            assembler=assembler,
            router=route_strategy_files,
            validator=validator,
            pending_writer=pending_writer,
            exp_reader=exp_reader,
            settings=settings,
        )

        sym = settings.general.last_symbol
        tf = settings.general.last_timeframe
        frame = _make_trending_frame(sym, tf, bar_n)
        log.info("Frame: %s %s bars=%d", sym, tf, len(frame.bars))

        events: list[OrchestratorEvent] = []

        def on_event(e: OrchestratorEvent) -> None:
            events.append(e)
            log.info("event: %s", e.name)

        record = orchestrator.submit(
            frame=frame,
            cancel_token=CancelToken(),
            on_event=on_event,
        )

    print("\n=== Live two-stage smoke summary ===")
    print("events:", [e.name for e in events])
    print("record_id:", getattr(record, "record_id", None))

    s1 = record.stage1_diagnosis
    if s1:
        print(
            "stage1:",
            "gate_result=", s1.get("gate_result"),
            "cycle_position=", s1.get("cycle_position"),
            "direction=", s1.get("direction"),
            "patterns=", s1.get("detected_patterns"),
        )
    else:
        print("stage1: (none)")

    s2 = record.stage2_decision
    if s2:
        dec = s2.get("decision") or {}
        print(
            "stage2:",
            "order_type=", dec.get("order_type"),
            "order_direction=", dec.get("order_direction"),
            "gate_shortcircuited=", s2.get("gate_shortcircuited"),
        )
        strat = record.strategy_files_used or []
        print("strategy_files_used:", strat)
    else:
        print("stage2: (none)")

    if record.exception:
        print("exception:", json.dumps(record.exception, ensure_ascii=False, indent=2))

    ok_pipeline = (
        OrchestratorEvent.Stage1Done in events
        and (
            OrchestratorEvent.Stage2Done in events
            or OrchestratorEvent.RecordSaved in events
        )
    )
    exc = record.exception or {}
    exc_type = exc.get("type", "")
    if exc_type == "network_error":
        print("\nRESULT: network/API error")
        return 1
    if OrchestratorEvent.Stage1Failed in events and not s1:
        print("\nRESULT: API OK, Stage1 strict validation failed (no Stage2 call)")
        return 0
    if OrchestratorEvent.Stage2Failed in events:
        print("\nRESULT: API OK, Stage2 strict validation failed")
        return 0
    if ok_pipeline:
        print("\nRESULT: OK (both stages completed)")
        return 0
    print("\nRESULT: incomplete pipeline")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
