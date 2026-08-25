"""Read the latest safe A-share market-review payload produced by DSA."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
DESKTOP_DSA_DATABASE = Path(r"E:\Daily stock analysis\data\stock_analysis.db")
PROJECT_DSA_DATABASE = ROOT / "daily_stock_analysis-main" / "data" / "stock_analysis.db"
DEFAULT_DSA_DATABASE = (
    DESKTOP_DSA_DATABASE if DESKTOP_DSA_DATABASE.is_file() else PROJECT_DSA_DATABASE
)


def load_latest_dsa_market_context(
    database: Path | str = DEFAULT_DSA_DATABASE,
    *,
    as_of: datetime | None = None,
    max_age_days: int = 4,
) -> dict[str, Any]:
    """Return DSA's latest structured market cache without importing its `src` package."""
    path = _resolve_database_path(database)
    if not path.is_file():
        return {
            "status": "unavailable",
            "source": "DSA analysis_history",
            "reason": f"DSA 大盘缓存尚未生成：{path}",
        }
    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        where_as_of = " AND created_at <= ?" if as_of is not None else ""
        parameters = (as_of.isoformat(),) if as_of is not None else ()
        row = connection.execute(
            f"""
            SELECT created_at, analysis_summary, news_content, context_snapshot, raw_result
            FROM analysis_history
            WHERE code = 'MARKET' AND report_type = 'market_review'
            {where_as_of}
            ORDER BY created_at DESC
            LIMIT 1
            """,
            parameters,
        ).fetchone()
    except (OSError, sqlite3.Error) as exc:
        return {
            "status": "error",
            "source": "DSA analysis_history",
            "reason": str(exc),
        }
    finally:
        if "connection" in locals():
            connection.close()
    if row is None:
        return {
            "status": "unavailable",
            "source": "DSA analysis_history",
            "reason": "DSA 数据库中还没有大盘复盘记录",
        }

    snapshot = _object(row["context_snapshot"])
    payload = snapshot.get("market_review_payload")
    if not isinstance(payload, Mapping):
        raw = _object(row["raw_result"])
        payload = raw.get("market_review_payload") if isinstance(raw, Mapping) else None
    safe_payload = _safe_projection(payload if isinstance(payload, Mapping) else {})
    decision_time = as_of or datetime.now()
    data_date = _payload_date(payload, str(row["created_at"] or ""))
    age_days = max(0, (decision_time.date() - data_date).days) if data_date else None
    stale = age_days is None or age_days > max_age_days
    if stale:
        return {
            "status": "stale",
            "source": "DSA analysis_history",
            "created_at": row["created_at"],
            "data_date": data_date.isoformat() if data_date else None,
            "decision_date": decision_time.date().isoformat(),
            "age_days": age_days,
            "usable_for_analysis": False,
            "reason": (
                f"DSA 大盘复盘日期为 {data_date.isoformat() if data_date else '未知'}，"
                f"距本次决策日 {decision_time.date().isoformat()} 超过 {max_age_days} 天"
            ),
            "data": {},
            "display_sections": [],
        }
    return {
        "status": "ready",
        "source": "DSA analysis_history",
        "created_at": row["created_at"],
        "data_date": data_date.isoformat() if data_date else None,
        "decision_date": decision_time.date().isoformat(),
        "age_days": age_days,
        "usable_for_analysis": True,
        "summary": str(row["analysis_summary"] or row["news_content"] or ""),
        "data": safe_payload,
        "display_sections": _display_sections(
            safe_payload,
            summary=str(row["analysis_summary"] or row["news_content"] or ""),
        ),
    }


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        result = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return result if isinstance(result, dict) else {}


def _payload_date(payload: Any, created_at: str) -> Any:
    value = payload.get("date") if isinstance(payload, Mapping) else None
    for candidate in (value, created_at):
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        try:
            return datetime.fromisoformat(candidate.strip().replace("Z", "+00:00")).date()
        except ValueError:
            continue
    return None


def _safe_projection(payload: Mapping[str, Any]) -> dict[str, Any]:
    allowed = (
        "version",
        "region",
        "date",
        "market_scope",
        "indices",
        "breadth",
        "sectors",
        "concepts",
        "market_light",
        "sections",
    )
    return {
        key: _without_sentiment_fields(payload[key])
        for key in allowed
        if key in payload and not _is_sentiment_key(key)
    }


def _resolve_database_path(database: Path | str) -> Path:
    """Accept either DSA's data folder or its SQLite database file."""
    path = Path(database).expanduser()
    database_suffixes = {".db", ".sqlite", ".sqlite3"}
    return path if path.suffix.casefold() in database_suffixes else path / "stock_analysis.db"


def _without_sentiment_fields(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _without_sentiment_fields(item)
            for key, item in value.items()
            if not _is_sentiment_key(key)
        }
    if isinstance(value, list | tuple):
        return [_without_sentiment_fields(item) for item in value]
    return value


def _is_sentiment_key(key: Any) -> bool:
    normalized = str(key).casefold().replace("_", "").replace("-", "")
    return any(
        marker in normalized
        for marker in ("sentiment", "emotion", "mood", "情绪")
    )


def _display_sections(
    payload: Mapping[str, Any], *, summary: str
) -> list[dict[str, Any]]:
    """Use DSA's narrative sections without repeating its structured preamble."""
    result = []
    if summary:
        result.append({"title": "市场结论", "content": summary})
    sections = payload.get("sections")
    if not isinstance(sections, list | tuple):
        return result
    for section in sections:
        if not isinstance(section, Mapping):
            continue
        if str(section.get("key") or "").casefold() == "overview":
            continue
        title = str(section.get("title") or section.get("key") or "").strip()
        content = section.get("markdown", section.get("content"))
        if title and content not in (None, "", [], {}):
            result.append({"title": title, "content": content})
    return result


__all__ = [
    "DEFAULT_DSA_DATABASE",
    "DESKTOP_DSA_DATABASE",
    "PROJECT_DSA_DATABASE",
    "load_latest_dsa_market_context",
]
