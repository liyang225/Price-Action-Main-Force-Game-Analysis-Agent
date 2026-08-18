"""Tests for the LLM observation sink wired into the production orchestrator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.integration.pa_embedded_service import PAEmbeddedService, normalize_pa_payload
from src.integration.pa_link import PAStage2Input
from src.labeler.confusion_counts import ConfusionCountStore


@pytest.fixture(autouse=True)
def _isolate_pa_service(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never read the deployed scope file, and keep auto-archive off in tests."""
    monkeypatch.setattr(
        "src.data.capital_flow_daily.DEFAULT_SCOPE_FILE", tmp_path / "scope.json"
    )
    original_init = PAEmbeddedService.__init__

    def isolated_init(self, *args, **kwargs):
        kwargs.setdefault("material_auto_archive", False)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(PAEmbeddedService, "__init__", isolated_init)


class _SinkResult:
    observations: list[tuple[str, str, str]] = []


def _result_payload() -> dict:
    return {
        "scenario_tree": {"branches": [], "analysis_metadata": {}},
        "integrated_gates": {},
        "completed_at": "2026-08-10T11:30:00",
    }


def _orchestrator_with_sink(sink):
    class Orchestrator:
        def run(self, pa, *, context):
            sector_analysis = {
                "sector_code": "BK0475",
                "sector_name": "半导体",
                "trading_session": {"trading_date": "2026-08-10"},
            }
            context.materials["sector_analysis"] = sector_analysis
            context.materials["market_window"] = {"code": "SZ.000001", "end": "2026-08-10"}
            # The orchestrator runs its cycle observation before adapting; here we
            # emulate the production path by invoking the sink through run().
            if sink is not None and context.materials:
                # Emulate _observe_cycle recording the LLM label.
                from src.integration import production_orchestrator as po

                sink(
                    po._sector_code_from_materials(context.materials),
                    po._trading_date_from_materials(context.materials),
                    "发酵",
                )
            return _Result()

        def close(self):
            pass

    class _Result:
        def to_dict(self):
            return _result_payload()

    return Orchestrator()


def test_embedded_service_wires_sink_into_orchestrator(tmp_path) -> None:
    captured: list[tuple[str, str, str]] = []

    class Builder:
        def __init__(self, source):
            self.source = source

        def build(self, pa):
            return _FakeContext()

    class _FakeContext:
        materials: dict = {}

    sink = lambda code, trading_date, label: captured.append((code, trading_date, label))
    service = PAEmbeddedService(
        market_source=object(),
        model_client=object(),
        context_builder_factory=Builder,
        orchestrator_factory=lambda client, **kwargs: _orchestrator_with_sink(
            kwargs.get("llm_observation_sink")
        ),
        llm_observation_sink=sink,
        history_database=tmp_path / "history.db",
    )
    result = service.run_analysis(
        {
            "symbol": "000001.SZ",
            "stage2_decision": {"decision": {"order_type": "限价单"}},
        }
    )
    assert result["ok"] is True
    assert captured == [("BK0475", "2026-08-10", "发酵")]


def test_sink_records_into_confusion_store(tmp_path) -> None:
    from src.labeler.confusion_counts import build_llm_observation_sink

    database = tmp_path / "confusion.db"
    sink = build_llm_observation_sink(database)
    sink("BK0475", "2026-08-10", "发酵")
    sink("unknown", "2026-08-10", "发酵")  # ignored silently
    with ConfusionCountStore(database) as store:
        observations = store.unreconciled_observations()
        assert len(observations) == 1
        assert observations[0] == {
            "sector_code": "BK0475",
            "trading_date": "2026-08-10",
            "llm_label": "发酵",
        }


def test_service_triggers_labeler_catchup_on_init(tmp_path) -> None:
    """PAEmbeddedService must fire the background catch-up sweep at startup."""
    import time

    from src.labeler.nightly import LabelerCatchUpReport

    messages: list[str] = []

    class Sink:
        def __call__(self, message: str) -> None:
            messages.append(message)

    service = PAEmbeddedService(
        market_source=object(),  # catch-up will fail gracefully on a bare object
        model_client=object(),
        context_builder_factory=lambda source: None,
        orchestrator_factory=lambda client, **_: None,
        llm_observation_sink=None,
        labeler_catchup=True,
        labeler_catchup_sectors=lambda: {"BK0475": "半导体"},
        labeler_catchup_sink=Sink(),
    )
    # The sweep runs on a daemon thread; give it a moment, then verify a
    # terminal message was emitted (either backfilled or skipped on error).
    deadline = time.time() + 5
    while time.time() < deadline and not messages:
        time.sleep(0.05)
    assert messages, "catch-up should have emitted at least one progress message"
    assert service._labeler_catchup is True
