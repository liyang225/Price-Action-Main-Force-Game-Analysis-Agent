from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.integration.dsa_market_context import (
    DESKTOP_DSA_DATABASE,
    load_latest_dsa_market_context,
)
from src.integration.production_context import _market_material


def test_latest_dsa_market_review_is_projected_to_safe_structured_material(tmp_path) -> None:
    db = tmp_path / "stock_analysis.db"
    connection = sqlite3.connect(db)
    connection.execute(
        """CREATE TABLE analysis_history (
        code TEXT, report_type TEXT, created_at TEXT, analysis_summary TEXT,
        news_content TEXT, context_snapshot TEXT, raw_result TEXT)"""
    )
    payload = {
        "market_review_payload": {
            "region": "cn",
            "date": "2026-08-13",
            "indices": [{"name": "上证指数", "change_pct": 1.2}],
            "sectors": {"top": ["半导体"], "bottom": ["银行"]},
            "sentiment_index": 88,
            "market_light": {"risk_level": "high", "sentiment_score": 12},
            "news": [{"secret": "not projected"}],
        }
    }
    connection.execute(
        "INSERT INTO analysis_history VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("MARKET", "market_review", "2026-08-13T15:10:00", "市场偏强", "", json.dumps(payload), "{}"),
    )
    connection.commit()
    connection.close()

    result = load_latest_dsa_market_context(db)

    assert result["status"] == "ready"
    assert result["data"]["indices"][0]["name"] == "上证指数"
    assert "news" not in result["data"]
    assert "sentiment_index" not in result["data"]
    assert result["data"]["market_light"] == {"risk_level": "high"}
    assert result["display_sections"] == [{"title": "市场结论", "content": "市场偏强"}]


def test_dsa_market_context_never_reads_a_record_after_decision_time(tmp_path) -> None:
    db = tmp_path / "stock_analysis.db"
    connection = sqlite3.connect(db)
    connection.execute(
        """CREATE TABLE analysis_history (
        code TEXT, report_type TEXT, created_at TEXT, analysis_summary TEXT,
        news_content TEXT, context_snapshot TEXT, raw_result TEXT)"""
    )
    for created_at, marker in (
        ("2026-08-12T15:10:00", "past"),
        ("2026-08-13T15:10:00", "future"),
    ):
        payload = {"market_review_payload": {"region": "cn", "sections": [marker]}}
        connection.execute(
            "INSERT INTO analysis_history VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("MARKET", "market_review", created_at, marker, "", json.dumps(payload), "{}"),
        )
    connection.commit()
    connection.close()

    result = load_latest_dsa_market_context(
        db, as_of=datetime(2026, 8, 13, 11, 30)
    )

    assert result["status"] == "ready"
    assert result["summary"] == "past"
    assert result["data"]["sections"] == ["past"]


def test_production_market_material_honors_configured_database_path(tmp_path) -> None:
    db = tmp_path / "stock_analysis.db"
    connection = sqlite3.connect(db)
    connection.execute(
        """CREATE TABLE analysis_history (
        code TEXT, report_type TEXT, created_at TEXT, analysis_summary TEXT,
        news_content TEXT, context_snapshot TEXT, raw_result TEXT)"""
    )
    payload = {"market_review_payload": {"region": "cn", "sections": ["manual"]}}
    connection.execute(
        "INSERT INTO analysis_history VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("MARKET", "market_review", "2026-08-12T15:00:00", "manual path", "", json.dumps(payload), "{}"),
    )
    connection.commit()
    connection.close()

    result = _market_material(
        {"dsa_database_path": str(db)}, as_of=datetime(2026, 8, 13, 11, 30)
    )

    assert result["status"] == "ready"
    assert result["summary"] == "manual path"

    result_from_folder = _market_material(
        {"dsa_database_path": str(tmp_path)},
        as_of=datetime(2026, 8, 13, 11, 30),
    )
    assert result_from_folder["summary"] == "manual path"


@pytest.mark.skipif(
    not Path(DESKTOP_DSA_DATABASE).is_file(), reason="desktop DSA database unavailable"
)
def test_desktop_dsa_database_exposes_structured_sector_display() -> None:
    result = load_latest_dsa_market_context(DESKTOP_DSA_DATABASE, max_age_days=9999)

    assert result["status"] == "ready"
    assert result["data"]["sectors"]["top"]
    titles = {section["title"] for section in result["display_sections"]}
    assert "市场结论" in titles
    assert "指数结构" not in titles
    assert all(
        not (
            isinstance(section.get("content"), Mapping)
            and section["content"].get("key") == "overview"
        )
        for section in result["display_sections"]
    )


@pytest.mark.skipif(
    not Path(DESKTOP_DSA_DATABASE).is_file(), reason="desktop DSA database unavailable"
)
def test_desktop_dsa_record_is_rejected_when_older_than_decision_date() -> None:
    latest = load_latest_dsa_market_context(DESKTOP_DSA_DATABASE, max_age_days=9999)
    data_date = datetime.fromisoformat(latest["data_date"])
    decision_time = data_date + timedelta(days=1, hours=16)
    result = load_latest_dsa_market_context(
        DESKTOP_DSA_DATABASE, as_of=decision_time, max_age_days=0
    )

    assert result["status"] == "stale"
    assert result["data_date"] == data_date.date().isoformat()
    assert result["decision_date"] == decision_time.date().isoformat()
    assert result["usable_for_analysis"] is False
