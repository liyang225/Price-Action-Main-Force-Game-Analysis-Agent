"""Tests for the persisted watchlist and analysis-pool membership."""
from __future__ import annotations

import json

import pytest

from pa_agent.records.watchlist import WatchlistStore


def test_watchlist_item_round_trip_with_analysis_pool(tmp_path):
    path = tmp_path / "watchlist.json"
    store = WatchlistStore(path)

    item = store.add(
        name="贵州茅台",
        symbol="600519",
        data_source="tradingview",
        exchange="SSE",
        timeframe="1d",
    )
    store.add_to_analysis_pool([item.id])

    restored = WatchlistStore(path)
    assert len(restored.items) == 1
    assert restored.items[0].name == "贵州茅台"
    assert restored.items[0].symbol == "600519"
    assert restored.items[0].data_source == "tradingview"
    assert restored.items[0].exchange == "SSE"
    assert restored.items[0].timeframe == "1d"
    assert restored.in_analysis_pool(item.id)
    assert restored.analysis_pool_items() == (restored.items[0],)


def test_analysis_pool_add_and_remove_are_idempotent(tmp_path):
    store = WatchlistStore(tmp_path / "watchlist.json")
    first = store.add(name="苹果", symbol="AAPL")
    second = store.add(name="微软", symbol="MSFT")

    assert store.add_to_analysis_pool([first.id, second.id, first.id]) == [first.id, second.id]
    assert store.add_to_analysis_pool([first.id]) == []
    assert store.remove_from_analysis_pool([first.id, first.id]) == [first.id]
    assert not store.in_analysis_pool(first.id)
    assert store.in_analysis_pool(second.id)


def test_watchlist_updates_editable_fields_but_not_symbol(tmp_path):
    path = tmp_path / "watchlist.json"
    store = WatchlistStore(path)
    item = store.add(name="原名称", symbol="600519", data_source="tradingview", timeframe="15m")

    updated = store.update(
        item.id,
        name="新名称",
        data_source="akshare",
        exchange="SSE",
        timeframe="1d",
    )

    assert updated is not None
    assert updated.name == "新名称"
    assert updated.symbol == "600519"
    assert updated.data_source == "akshare"
    assert updated.exchange == "SSE"
    assert updated.timeframe == "1d"
    restored = WatchlistStore(path).get(item.id)
    assert restored == updated
    assert store.update("missing", name="未保存") is None


def test_deleting_watchlist_item_also_removes_pool_membership(tmp_path):
    store = WatchlistStore(tmp_path / "watchlist.json")
    item = store.add(name="测试", symbol="TEST")
    store.add_to_analysis_pool([item.id])

    assert store.remove(item.id) == item
    assert store.get(item.id) is None
    assert not store.in_analysis_pool(item.id)


def test_watchlist_rejects_blank_symbol_and_ignores_invalid_saved_members(tmp_path):
    path = tmp_path / "watchlist.json"
    store = WatchlistStore(path)
    with pytest.raises(ValueError, match="股票代码不能为空"):
        store.add(name="无代码", symbol="")

    path.write_text(
        json.dumps(
            {
                "items": [{"id": "known", "name": "测试", "symbol": "TEST"}],
                "analysis_pool_ids": ["known", "missing", "known"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    restored = WatchlistStore(path)
    assert restored.analysis_pool_ids == frozenset({"known"})
