"""Tests for the structured OHLCV labeler status (加载状态 / 运行状态)."""

from __future__ import annotations

import pytest

from src.data.fake_client import FakeMarketDataSource
from src.data.daily_cache import DailyMaterialCache
from src.integration.labeler_status import (
    LabelerStatus,
    LabelerStatusTracker,
    LoadState,
    RunState,
)
from src.integration.pa_embedded_service import PAEmbeddedService


def test_initial_status_is_not_loaded_and_idle() -> None:
    tracker = LabelerStatusTracker()
    status = tracker.snapshot()
    assert status.load_state is LoadState.NOT_LOADED
    assert status.run_state is RunState.IDLE
    assert status.load_label == "未加载"
    assert status.run_label == "空闲"


def test_load_and_run_states_advance_independently() -> None:
    tracker = LabelerStatusTracker()
    tracker.set_load(LoadState.LOADING, "正在加载标注器规则与标注范围…")
    assert tracker.snapshot().run_state is RunState.IDLE  # run 不受影响
    tracker.set_load(LoadState.LOADED, "标注器规则与标注范围已就绪")
    tracker.set_run(RunState.RUNNING, "正在补跑历史标签…")
    status = tracker.snapshot()
    assert status.load_state is LoadState.LOADED
    assert status.run_state is RunState.RUNNING
    assert status.load_label == "已加载"
    assert status.run_label == "运行中"
    assert status.last_updated


def test_details_are_merged_across_run_updates() -> None:
    tracker = LabelerStatusTracker()
    tracker.set_run(RunState.RUNNING, "运行中", {"as_of": "2026-08-14"})
    tracker.set_run(RunState.COMPLETED, "已完成", {"ran_sweeps": 3})
    details = tracker.snapshot().details
    assert details["as_of"] == "2026-08-14"
    assert details["ran_sweeps"] == 3


def test_mark_running_guards_a_single_active_sweep() -> None:
    tracker = LabelerStatusTracker()
    assert tracker.mark_running() is True
    assert tracker.mark_running() is False  # 第二线程被拒
    assert tracker.is_running() is True
    tracker.clear_running()
    assert tracker.is_running() is False
    assert tracker.mark_running() is True


def test_subscribers_receive_every_change() -> None:
    tracker = LabelerStatusTracker()
    seen: list[LabelerStatus] = []
    tracker.subscribe(seen.append)
    tracker.set_load(LoadState.LOADING)
    tracker.set_run(RunState.RUNNING)
    assert [item.run_state for item in seen] == [RunState.IDLE, RunState.RUNNING]
    assert [item.load_state for item in seen] == [LoadState.LOADING, LoadState.LOADING]


def test_to_dict_is_json_friendly() -> None:
    tracker = LabelerStatusTracker()
    tracker.set_load(LoadState.LOADED)
    tracker.set_run(RunState.COMPLETED, "已完成", {"ran_sweeps": 1})
    payload = tracker.snapshot().to_dict()
    assert payload["load_state"] == "loaded"
    assert payload["load_label"] == "已加载"
    assert payload["run_state"] == "completed"
    assert payload["run_label"] == "已完成"
    assert payload["details"]["ran_sweeps"] == 1


def test_embedded_service_reports_skipped_when_scope_is_empty(tmp_path) -> None:
    """Empty sector scope -> load OK, run skipped, no background thread."""
    cache = DailyMaterialCache(tmp_path / "archives")
    service = PAEmbeddedService(
        market_source=FakeMarketDataSource(),
        model_client=object(),
        material_cache=cache,
        labeler_catchup_sectors=lambda: {},
        history_database=tmp_path / "history.db",
        material_auto_archive=False,
        capital_flow_catchup=False,
    )
    try:
        status = service.labeler_status()
        assert status.load_state is LoadState.LOADED
        assert status.run_state is RunState.SKIPPED
        assert not service._labeler_status.is_running()
    finally:
        service.close()


def test_embedded_service_reports_load_failure_when_scope_lookup_raises(tmp_path) -> None:
    cache = DailyMaterialCache(tmp_path / "archives")

    def boom() -> dict[str, str]:
        raise RuntimeError("registry unavailable")

    service = PAEmbeddedService(
        market_source=FakeMarketDataSource(),
        model_client=object(),
        material_cache=cache,
        labeler_catchup_sectors=boom,
        history_database=tmp_path / "history.db",
        material_auto_archive=False,
        capital_flow_catchup=False,
    )
    try:
        status = service.labeler_status()
        assert status.load_state is LoadState.LOAD_FAILED
        assert status.run_state is RunState.SKIPPED
        assert "registry unavailable" in status.load_message
    finally:
        service.close()


def test_embedded_service_marks_disabled_catchup_as_skipped(tmp_path) -> None:
    cache = DailyMaterialCache(tmp_path / "archives")
    service = PAEmbeddedService(
        market_source=FakeMarketDataSource(),
        model_client=object(),
        material_cache=cache,
        labeler_catchup=False,
        history_database=tmp_path / "history.db",
        material_auto_archive=False,
        capital_flow_catchup=False,
    )
    try:
        assert service.labeler_status().run_state is RunState.SKIPPED
    finally:
        service.close()


def test_embedded_service_shares_a_provided_tracker(tmp_path) -> None:
    tracker = LabelerStatusTracker()
    cache = DailyMaterialCache(tmp_path / "archives")
    service = PAEmbeddedService(
        market_source=FakeMarketDataSource(),
        model_client=object(),
        material_cache=cache,
        labeler_catchup_sectors=lambda: {},
        labeler_status_tracker=tracker,
        history_database=tmp_path / "history.db",
        material_auto_archive=False,
        capital_flow_catchup=False,
    )
    try:
        assert service._labeler_status is tracker
        assert tracker.snapshot().run_state is RunState.SKIPPED
    finally:
        service.close()
