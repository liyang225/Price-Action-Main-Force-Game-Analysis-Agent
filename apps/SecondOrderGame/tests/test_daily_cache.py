from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json

import pytest

from src.data.daily_cache import (
    DailyMaterialArchiveError,
    DailyMaterialCache,
    DailyMaterialCacheClosedError,
)


def test_midday_decisions_share_a_stable_snapshot_while_background_updates_continue(
    tmp_path, daily_clock
) -> None:
    daily_clock.current = datetime(2026, 8, 10, 11, 30, tzinfo=timezone.utc)
    cache = DailyMaterialCache(tmp_path / "archives", clock=daily_clock)
    cache.put("news", "semiconductors", {"title": "Morning update"})

    first_decision = cache.snapshot()
    cache.put("news", "semiconductors", {"title": "Afternoon update"})
    second_decision = cache.snapshot()

    assert first_decision.trading_date.isoformat() == "2026-08-10"
    assert first_decision.materials == {
        "news": {"semiconductors": {"title": "Morning update"}}
    }
    assert second_decision == first_decision


def test_next_decision_can_refresh_without_mutating_previous_snapshot(tmp_path, daily_clock) -> None:
    daily_clock.current = datetime(2026, 8, 10, 11, 30, tzinfo=timezone.utc)
    cache = DailyMaterialCache(tmp_path / "archives", clock=daily_clock)
    cache.put("news", "semiconductors", {"title": "First"})
    first = cache.snapshot()
    cache.put("news", "semiconductors", {"title": "Second"})

    refreshed = cache.snapshot(refresh=True)

    assert first.materials["news"]["semiconductors"]["title"] == "First"
    assert refreshed.materials["news"]["semiconductors"]["title"] == "Second"


def test_decision_bundle_is_frozen_as_one_complete_snapshot(tmp_path, daily_clock) -> None:
    cache = DailyMaterialCache(tmp_path / "archives", clock=daily_clock)

    snapshot = cache.put_many_and_snapshot(
        {
            "market": {"global": {"breadth": "strong"}},
            "sector": {"半导体": {"cycle": "启动"}},
            "news": {"半导体": {"status": "ready"}},
            "stock_signals": {"000001.SZ": {"bar_time": "2026-08-10 11:30:00"}},
        }
    )

    assert set(snapshot.materials) == {"market", "sector", "news", "stock_signals"}
    assert cache.status()["categories"] == {
        "market": 1,
        "sector": 1,
        "news": 1,
        "stock_signals": 1,
    }


def test_cache_can_peek_one_material_without_freezing_a_decision_snapshot(
    tmp_path, daily_clock
) -> None:
    cache = DailyMaterialCache(tmp_path / "archives", clock=daily_clock)
    cache.put("news", "半导体", {"items": ["first"]})

    peeked = cache.get("news", "半导体")
    peeked["items"].append("mutated by caller")

    assert cache.get("news", "半导体") == {"items": ["first"]}
    assert cache.status()["decision_snapshot_created"] is False


def test_cache_preview_reports_named_entries_for_lifecycle_ui(tmp_path, daily_clock) -> None:
    cache = DailyMaterialCache(tmp_path / "archives", clock=daily_clock)
    cache.put("news", "半导体", {"status": "ready"})
    cache.put("market", "global", {"status": "ready"})

    preview = cache.preview()

    assert preview["news"]["半导体"] == {"status": "ready"}
    assert preview["market"]["global"] == {"status": "ready"}
    preview["news"]["半导体"]["status"] = "changed"
    assert cache.get("news", "半导体")["status"] == "ready"


def test_close_time_can_archive_a_complete_daily_snapshot(tmp_path, daily_clock) -> None:
    daily_clock.current = datetime(2026, 8, 10, 15, 0, tzinfo=timezone.utc)
    cache = DailyMaterialCache(tmp_path / "archives", clock=daily_clock)
    cache.put("news", "banking", {"title": "Closing update"})

    archive_path = cache.archive()

    assert archive_path == tmp_path / "archives" / "2026-08-10.json"
    assert json.loads(archive_path.read_text(encoding="utf-8")) == {
        "trading_date": "2026-08-10",
        "materials": {"news": {"banking": {"title": "Closing update"}}},
    }
    with pytest.raises(DailyMaterialCacheClosedError):
        cache.put("news", "banking", {"title": "Too late"})


def test_archive_is_not_available_before_the_close(tmp_path, daily_clock) -> None:
    daily_clock.current = datetime(2026, 8, 10, 11, 30, tzinfo=timezone.utc)
    cache = DailyMaterialCache(tmp_path / "archives", clock=daily_clock)

    with pytest.raises(ValueError, match="15:00"):
        cache.archive()


def test_next_day_discards_yesterdays_live_material_and_rebuilds_the_cache(
    tmp_path, daily_clock
) -> None:
    daily_clock.current = datetime(2026, 8, 10, 15, 0, tzinfo=timezone.utc)
    cache = DailyMaterialCache(tmp_path / "archives", clock=daily_clock)
    cache.put("news", "semiconductors", {"title": "Yesterday"})
    cache.archive()

    daily_clock.current = datetime(2026, 8, 10, 23, 59, tzinfo=timezone.utc)
    assert cache.snapshot().trading_date.isoformat() == "2026-08-10"
    daily_clock.current = datetime(2026, 8, 11, 0, 0, tzinfo=timezone.utc)
    cache.put("news", "semiconductors", {"title": "Today"})

    assert cache.snapshot().materials == {
        "news": {"semiconductors": {"title": "Today"}}
    }


def test_a_restarted_process_never_reads_an_archived_previous_day_as_live_cache(
    tmp_path, daily_clock
) -> None:
    archive_directory = tmp_path / "archives"
    daily_clock.current = datetime(2026, 8, 10, 15, 0, tzinfo=timezone.utc)
    first_process = DailyMaterialCache(archive_directory, clock=daily_clock)
    first_process.put("news", "semiconductors", {"title": "Yesterday"})
    first_process.archive()

    daily_clock.current = datetime(2026, 8, 11, 9, 30, tzinfo=timezone.utc)
    restarted_process = DailyMaterialCache(archive_directory, clock=daily_clock)

    assert restarted_process.snapshot().trading_date.isoformat() == "2026-08-11"
    assert restarted_process.snapshot().materials == {}


def test_a_failed_archive_does_not_close_or_discard_the_live_cache(
    tmp_path, daily_clock
) -> None:
    archive_path = tmp_path / "not-a-directory"
    archive_path.write_text("blocked", encoding="utf-8")
    daily_clock.current = datetime(2026, 8, 10, 15, 0, tzinfo=timezone.utc)
    cache = DailyMaterialCache(archive_path, clock=daily_clock)
    cache.put("news", "semiconductors", {"title": "Keep this"})

    with pytest.raises(DailyMaterialArchiveError):
        cache.archive()

    cache.put("signals", "SZ.000001", {"signal": "absorption"})
    assert cache.snapshot().materials == {
        "news": {"semiconductors": {"title": "Keep this"}},
        "signals": {"SZ.000001": {"signal": "absorption"}},
    }


def test_mutable_custom_materials_are_rejected_before_they_can_reach_a_snapshot(
    tmp_path, daily_clock
) -> None:
    cache = DailyMaterialCache(tmp_path / "archives", clock=daily_clock)

    with pytest.raises(TypeError, match="immutable"):
        cache.put("news", "semiconductors", bytearray(b"mutable"))


def test_frozen_dataclasses_cannot_hide_mutable_fields_from_a_snapshot(
    tmp_path, daily_clock
) -> None:
    @dataclass(frozen=True)
    class MaterialWithMutableField:
        entries: list[str]

    cache = DailyMaterialCache(tmp_path / "archives", clock=daily_clock)

    with pytest.raises(TypeError, match="recursively immutable"):
        cache.put("news", "semiconductors", MaterialWithMutableField(["mutable"]))
