from __future__ import annotations

import json

import pytest

from src.labeler.shadow_cutover import ShadowCutoverManager, ShadowStateStore
from src.labeler.sector_labeler_v2 import SectorV2Label, SectorV2Metrics


def _seed(store: ShadowStateStore, sector: str, days: int, *, structural_error_day=None):
    for day in range(1, days + 1):
        store.record_label(
            SectorV2Label(
                sector_code=sector,
                trading_date=f"2026-08-{day:02d}",
                status="labeled",
                metrics=SectorV2Metrics(10, 2, 2, 0.2, 2, 0, 1.0, 0.2, 1.2),
                cycle_position="发酵",
                consensus_state="一致",
                consensus_direction="转强",
                cycle_event="无",
                rule_hash="v2-hash",
                config_version=1,
            ),
            structural_error=day == structural_error_day,
        )


def test_cutover_waits_when_one_sector_is_one_qualified_day_short(tmp_path) -> None:
    store = ShadowStateStore(tmp_path / "shadow.db")
    _seed(store, "A", 5)
    _seed(store, "B", 4)
    manager = ShadowCutoverManager(store, tmp_path / "production", tmp_path / "reports")

    result = manager.attempt(
        ("A", "B"),
        rule_hash="v2-hash",
        relabel_history=lambda: {"A": ["发酵"], "B": ["启动"]},
        rebuild_c_counts=lambda labels: {"true_发酵": {"llm_发酵": 1}},
    )

    assert result.status == "not_ready"
    assert not (tmp_path / "production" / "active.json").exists()


@pytest.mark.parametrize("failure", ["relabel", "c_rebuild"])
def test_failed_rebuild_leaves_v1_active(tmp_path, failure) -> None:
    store = ShadowStateStore(tmp_path / "shadow.db")
    _seed(store, "A", 5)
    production = tmp_path / "production"
    production.mkdir()
    (production / "active.json").write_text(
        json.dumps({"version": "v1", "rule_hash": "v1-hash"}), encoding="utf-8"
    )
    manager = ShadowCutoverManager(store, production, tmp_path / "reports")

    def relabel():
        if failure == "relabel":
            raise RuntimeError("relabel failed")
        return {"A": ["发酵"]}

    def rebuild(labels):
        if failure == "c_rebuild":
            raise RuntimeError("C rebuild failed")
        return {"true_发酵": {"llm_发酵": 1}}

    result = manager.attempt(
        ("A",), rule_hash="v2-hash", relabel_history=relabel, rebuild_c_counts=rebuild
    )

    assert result.status == "failed"
    assert json.loads((production / "active.json").read_text(encoding="utf-8"))["version"] == "v1"


def test_successful_cutover_relabels_rebuilds_reports_then_switches(tmp_path) -> None:
    store = ShadowStateStore(tmp_path / "shadow.db")
    _seed(store, "A", 5)
    manager = ShadowCutoverManager(store, tmp_path / "production", tmp_path / "reports")

    result = manager.attempt(
        ("A",),
        rule_hash="v2-hash",
        relabel_history=lambda: {"A": ["启动", "发酵"]},
        rebuild_c_counts=lambda labels: {"true_发酵": {"llm_发酵": 1}},
    )

    assert result.status == "cutover"
    active = json.loads((tmp_path / "production" / "active.json").read_text(encoding="utf-8"))
    assert active["version"] == "v2"
    assert active["rule_hash"] == "v2-hash"
    assert result.report_path.exists()


@pytest.mark.parametrize("failure_call", [1, 2])
def test_failure_after_release_publish_removes_all_v2_artifacts(
    tmp_path, monkeypatch, failure_call
) -> None:
    store = ShadowStateStore(tmp_path / "shadow.db")
    _seed(store, "A", 5)
    production = tmp_path / "production"
    production.mkdir()
    active = production / "active.json"
    active.write_text(json.dumps({"version": "v1", "rule_hash": "v1-hash"}), encoding="utf-8")
    reports = tmp_path / "reports"
    manager = ShadowCutoverManager(store, production, reports)

    from src.labeler import shadow_cutover

    original_atomic_json = shadow_cutover._atomic_json
    calls = 0

    def fail_publish(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == failure_call:
            raise OSError("artifact publish failed")
        return original_atomic_json(*args, **kwargs)

    monkeypatch.setattr("src.labeler.shadow_cutover._atomic_json", fail_publish)
    result = manager.attempt(
        ("A",),
        rule_hash="v2-hash",
        relabel_history=lambda: {"A": ["发酵"]},
        rebuild_c_counts=lambda labels: {"true_发酵": {"llm_发酵": 1}},
    )

    assert result.status == "failed"
    assert json.loads(active.read_text(encoding="utf-8"))["version"] == "v1"
    assert not (production / "v2-v2-hash").exists()
    assert not list(reports.glob("sector-labeler-v2-*.json"))
