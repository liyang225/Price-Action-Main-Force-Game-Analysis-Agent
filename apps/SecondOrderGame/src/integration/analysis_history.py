"""Small append-only history store for completed embedded analyses."""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
HISTORY_DIR = ROOT / "analysis_history"
DEFAULT_HISTORY_DB = HISTORY_DIR / "second_order_history.db"
LEGACY_HISTORY_DB = ROOT / "runtime" / "second_order_history.db"


def _default_history_database() -> Path:
    """Migrate the original runtime database without discarding existing rows."""
    if not DEFAULT_HISTORY_DB.exists() and LEGACY_HISTORY_DB.is_file():
        DEFAULT_HISTORY_DB.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(LEGACY_HISTORY_DB, DEFAULT_HISTORY_DB)
    return DEFAULT_HISTORY_DB


class AnalysisHistoryStore:
    def __init__(self, database: Path | str | None = None) -> None:
        path = Path(database if database is not None else _default_history_database())
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS second_order_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                sector_name TEXT NOT NULL,
                decision_point TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                actual_result TEXT,
                resolved_at TEXT
            )
            """
        )
        columns = {
            row["name"]
            for row in self._connection.execute(
                "PRAGMA table_info(second_order_history)"
            ).fetchall()
        }
        if "actual_result" not in columns:
            self._connection.execute(
                "ALTER TABLE second_order_history ADD COLUMN actual_result TEXT"
            )
        if "resolved_at" not in columns:
            self._connection.execute(
                "ALTER TABLE second_order_history ADD COLUMN resolved_at TEXT"
            )
        self._connection.commit()

    def append(self, payload: Mapping[str, Any]) -> int:
        input_ = payload.get("input") if isinstance(payload, Mapping) else None
        input_ = input_ if isinstance(input_, Mapping) else {}
        materials = input_.get("materials")
        materials = materials if isinstance(materials, Mapping) else {}
        sector = materials.get("sector_analysis")
        sector = sector if isinstance(sector, Mapping) else {}
        symbol = str(input_.get("symbol") or "").strip()
        decision_point = str(input_.get("decision_point") or "").strip()
        if not symbol:
            raise ValueError("history payload must contain a symbol")
        if decision_point not in {"midday", "close"}:
            raise ValueError("history payload must contain a valid decision point")
        cursor = self._connection.execute(
            """
            INSERT INTO second_order_history
            (symbol, sector_name, decision_point, completed_at, payload_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                symbol,
                str(sector.get("sector_name") or symbol),
                decision_point,
                str(payload.get("completed_at") or datetime.now().isoformat()),
                json.dumps(payload, ensure_ascii=False, default=str),
            ),
        )
        self._connection.commit()
        return int(cursor.lastrowid)

    def list_recent(self, *, symbol: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer between 1 and 500")
        if symbol:
            rows = self._connection.execute(
                """SELECT id, symbol, sector_name, decision_point, completed_at, payload_json,
                          actual_result, resolved_at
                   FROM second_order_history WHERE symbol = ? ORDER BY id DESC LIMIT ?""",
                (symbol, limit),
            ).fetchall()
        else:
            rows = self._connection.execute(
                """SELECT id, symbol, sector_name, decision_point, completed_at, payload_json,
                          actual_result, resolved_at
                   FROM second_order_history ORDER BY id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "symbol": row["symbol"],
                "sector_name": row["sector_name"],
                "decision_point": row["decision_point"],
                "completed_at": row["completed_at"],
                "actual_result": row["actual_result"],
                "resolved_at": row["resolved_at"],
                "result": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def resolve(self, record_id: int, actual_result: str) -> bool:
        """Attach one immutable settlement outcome to a completed analysis."""
        if isinstance(record_id, bool) or not isinstance(record_id, int) or record_id < 1:
            raise ValueError("record_id must be a positive integer")
        outcome = str(actual_result or "").strip().lower()
        if outcome not in {"win", "loss", "neutral"}:
            raise ValueError("actual_result must be win, loss, or neutral")
        resolved_at = datetime.now().isoformat()
        row = self._connection.execute(
            "SELECT actual_result FROM second_order_history WHERE id = ?", (record_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown history record: {record_id}")
        if row["actual_result"] is not None:
            if row["actual_result"] == outcome:
                return False
            raise ValueError(f"history record {record_id} already has another outcome")
        self._connection.execute(
            "UPDATE second_order_history SET actual_result = ?, resolved_at = ? WHERE id = ?",
            (outcome, resolved_at, record_id),
        )
        self._connection.commit()
        return True

    def delete(self, record_id: int) -> bool:
        """Delete one history record by id. Returns True when a row was removed."""
        if isinstance(record_id, bool) or not isinstance(record_id, int) or record_id < 1:
            raise ValueError("record_id must be a positive integer")
        cursor = self._connection.execute(
            "DELETE FROM second_order_history WHERE id = ?", (record_id,)
        )
        self._connection.commit()
        return cursor.rowcount > 0

    def summary(self, *, symbol: str | None = None) -> dict[str, Any]:
        where = "WHERE symbol = ?" if symbol else ""
        args = (symbol,) if symbol else ()
        row = self._connection.execute(
            f"""SELECT COUNT(*) AS total,
                       SUM(CASE WHEN actual_result IS NOT NULL THEN 1 ELSE 0 END) AS resolved,
                       SUM(CASE WHEN actual_result = 'win' THEN 1 ELSE 0 END) AS wins,
                       SUM(CASE WHEN actual_result = 'loss' THEN 1 ELSE 0 END) AS losses,
                       SUM(CASE WHEN actual_result = 'neutral' THEN 1 ELSE 0 END) AS neutral
                FROM second_order_history {where}""",
            args,
        ).fetchone()
        resolved = int(row["resolved"] or 0)
        return {
            "status": "available" if resolved >= 30 else "insufficient_data",
            "minimum_sample_count": 30,
            "total": int(row["total"] or 0),
            "resolved": resolved,
            "wins": int(row["wins"] or 0),
            "losses": int(row["losses"] or 0),
            "neutral": int(row["neutral"] or 0),
            "win_rate": (
                int(row["wins"] or 0) / resolved if resolved >= 30 else None
            ),
        }

    def close(self) -> None:
        self._connection.close()


__all__ = ["AnalysisHistoryStore", "DEFAULT_HISTORY_DB"]
