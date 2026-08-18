"""Persistent post-hoc label flow for sector and stock layers.

The labelers are frozen definitions (ADR-0007): each run emits one label per
sector/stock day.  This ledger is the durable home for that label flow so the
nightly sweep can accumulate history day by day without losing anything when
the process restarts.  Labels are keyed by ``(scope, entity, trading_date,
rule_hash)`` so a rule change never mixes old and new counts silently.

The ledger is deliberately dumb: it stores rows as produced, applies no
business logic, and never mutates a label after it is recorded.  Counters and
C/W rebuilds are separate consumers.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class StoredLabel:
    """One normalized label row shared by sector and stock layers."""

    scope: str                      # "sector" | "stock"
    entity: str                     # sector_code for sector scope; stock code otherwise
    trading_date: str               # ISO date of the labeled day
    label: str | None               # cycle_position / behavior; None when unlabeled
    status: str                     # labeled | unlabeled | data_insufficient | unavailable
    reason: str | None
    rule_hash: str
    config_version: int
    feature_json: str
    created_at: str


class LabelLedger:
    """SQLite persistence for the frozen label flow."""

    def __init__(self, database: Path | str | sqlite3.Connection) -> None:
        self._owns_connection = not isinstance(database, sqlite3.Connection)
        if self._owns_connection:
            path = Path(database)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(path)
        else:
            self._connection = database
        self._connection.row_factory = sqlite3.Row
        self._initialize()

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def _initialize(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS posthoc_labels (
                scope TEXT NOT NULL,
                entity TEXT NOT NULL,
                trading_date TEXT NOT NULL,
                label TEXT,
                status TEXT NOT NULL,
                reason TEXT,
                rule_hash TEXT NOT NULL,
                config_version INTEGER NOT NULL,
                feature_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (scope, entity, trading_date, rule_hash)
            )
            """
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_labels_scope_date ON posthoc_labels (scope, trading_date)"
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_labels_entity ON posthoc_labels (entity)"
        )
        self._connection.commit()

    def close(self) -> None:
        if self._owns_connection:
            self._connection.close()

    def __enter__(self) -> "LabelLedger":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------

    def record_sector_labels(
        self,
        sector_code: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        feature_rows: Sequence[Mapping[str, Any]] | None = None,
    ) -> int:
        """Insert sector-labeler rows idempotently; returns rows written."""
        return self._record_scope("sector", sector_code, rows, feature_rows=feature_rows)

    def record_stock_labels(
        self,
        code: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        feature_rows: Sequence[Mapping[str, Any]] | None = None,
        sector_code: str | None = None,
    ) -> int:
        """Insert stock-labeler rows idempotently; returns rows written.

        ``sector_code`` anchors the stock to its primary sector so downstream
        W-matrix backfill can pair the stock's behavior with the sector's cycle
        label on the same trading day.  It is persisted inside ``feature_json``
        so it never changes the row identity key.
        """
        return self._record_scope(
            "stock", code, rows, feature_rows=feature_rows, sector_code=sector_code
        )

    def _record_scope(
        self,
        scope: str,
        entity: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        feature_rows: Sequence[Mapping[str, Any]] | None,
        sector_code: str | None = None,
    ) -> int:
        feature_by_date: dict[str, Mapping[str, Any]] = {}
        if feature_rows is not None:
            for row in feature_rows:
                date_value = row.get("date")
                if date_value is not None:
                    feature_by_date[str(date_value)] = row
        written = 0
        now = datetime.now().isoformat(timespec="seconds")
        for row in rows:
            date_value = row.get("date")
            if date_value is None:
                continue
            trading_date = _iso_date(date_value)
            rule_hash = str(row.get("rule_hash") or "")
            if not rule_hash:
                raise ValueError(f"{scope} label row for {trading_date} lacks rule_hash")
            label_value = _nullable(
                row.get("label")
                or row.get("cycle_position")
                or row.get("behavior")
            )
            # Persist participant + sector_code alongside the feature snapshot
            # so W-matrix backfill can recover them without changing the key.
            feature_snapshot = dict(feature_by_date.get(trading_date, {}))
            participant = _nullable(row.get("participant"))
            if participant is not None:
                feature_snapshot["participant"] = participant
            if sector_code:
                feature_snapshot["sector_code"] = sector_code
            feature_json = json.dumps(
                _plain(feature_snapshot),
                ensure_ascii=False,
                sort_keys=True,
            )
            cursor = self._connection.execute(
                """
                INSERT INTO posthoc_labels
                (scope, entity, trading_date, label, status, reason,
                 rule_hash, config_version, feature_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope, entity, trading_date, rule_hash) DO UPDATE SET
                    label = excluded.label,
                    status = excluded.status,
                    reason = excluded.reason,
                    config_version = excluded.config_version,
                    feature_json = excluded.feature_json
                """,
                (
                    scope,
                    entity,
                    trading_date,
                    label_value,
                    str(row.get("status") or "unlabeled"),
                    _nullable(row.get("reason")),
                    rule_hash,
                    int(row.get("config_version") or 0),
                    feature_json,
                    now,
                ),
            )
            written += max(cursor.rowcount, 0)
        self._connection.commit()
        return written

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------

    def sector_labels(
        self,
        sector_code: str | None = None,
        *,
        rule_hash: str | None = None,
        start: str | None = None,
        end: str | None = None,
        status: str | None = None,
        limit: int | None = None,
    ) -> tuple[StoredLabel, ...]:
        return self._query(
            scope="sector",
            entity=sector_code,
            rule_hash=rule_hash,
            start=start,
            end=end,
            status=status,
            limit=limit,
        )

    def stock_labels(
        self,
        code: str | None = None,
        *,
        sector_code: str | None = None,
        rule_hash: str | None = None,
        start: str | None = None,
        end: str | None = None,
        status: str | None = None,
        limit: int | None = None,
    ) -> tuple[StoredLabel, ...]:
        return self._query(
            scope="stock",
            entity=code,
            rule_hash=rule_hash,
            start=start,
            end=end,
            status=status,
            limit=limit,
        )

    def _query(
        self,
        *,
        scope: str,
        entity: str | None,
        rule_hash: str | None,
        start: str | None,
        end: str | None,
        status: str | None,
        limit: int | None,
    ) -> tuple[StoredLabel, ...]:
        conditions: list[str] = ["scope = ?"]
        parameters: list[Any] = [scope]
        if entity is not None:
            conditions.append("entity = ?")
            parameters.append(entity)
        if rule_hash is not None:
            conditions.append("rule_hash = ?")
            parameters.append(rule_hash)
        if start is not None:
            conditions.append("trading_date >= ?")
            parameters.append(start)
        if end is not None:
            conditions.append("trading_date <= ?")
            parameters.append(end)
        if status is not None:
            conditions.append("status = ?")
            parameters.append(status)
        sql = (
            "SELECT scope, entity, trading_date, label, status, reason, "
            "rule_hash, config_version, feature_json, created_at "
            f"FROM posthoc_labels WHERE {' AND '.join(conditions)} "
            "ORDER BY trading_date"
        )
        if limit is not None:
            sql += " LIMIT ?"
            parameters.append(limit)
        return tuple(
            StoredLabel(
                scope=str(row["scope"]),
                entity=str(row["entity"]),
                trading_date=str(row["trading_date"]),
                label=row["label"],
                status=str(row["status"]),
                reason=row["reason"],
                rule_hash=str(row["rule_hash"]),
                config_version=int(row["config_version"]),
                feature_json=str(row["feature_json"]),
                created_at=str(row["created_at"]),
            )
            for row in self._connection.execute(sql, parameters)
        )

    def labeled_dates(
        self, scope: str, entity: str, *, rule_hash: str
    ) -> tuple[str, ...]:
        rows = self._connection.execute(
            """
            SELECT trading_date FROM posthoc_labels
            WHERE scope = ? AND entity = ? AND rule_hash = ? AND label IS NOT NULL
            ORDER BY trading_date
            """,
            (scope, entity, rule_hash),
        )
        return tuple(str(row["trading_date"]) for row in rows)

    def latest_labeled_date(
        self, scope: str, entity: str, *, rule_hash: str
    ) -> str | None:
        """Return the most recent labeled trading date for an entity, or None."""
        rows = self._connection.execute(
            """
            SELECT MAX(trading_date) AS latest FROM posthoc_labels
            WHERE scope = ? AND entity = ? AND rule_hash = ? AND label IS NOT NULL
            """,
            (scope, entity, rule_hash),
        ).fetchone()
        value = rows["latest"] if rows is not None else None
        return str(value) if value is not None else None

    def latest_labeled_dates(
        self, scope: str, *, rule_hash: str, entities: Iterable[str]
    ) -> dict[str, str | None]:
        """Map each entity to its latest labeled date under one rule hash."""
        return {
            entity: self.latest_labeled_date(scope, entity, rule_hash=rule_hash)
            for entity in entities
        }

    # ------------------------------------------------------------------
    # consumers
    # ------------------------------------------------------------------

    def label_counts(
        self,
        scope: str,
        *,
        rule_hash: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> dict[str, int]:
        """Count labels by value, ignoring unlabeled/data_insufficient rows."""
        conditions: list[str] = ["scope = ?", "label IS NOT NULL"]
        parameters: list[Any] = [scope]
        if rule_hash is not None:
            conditions.append("rule_hash = ?")
            parameters.append(rule_hash)
        if start is not None:
            conditions.append("trading_date >= ?")
            parameters.append(start)
        if end is not None:
            conditions.append("trading_date <= ?")
            parameters.append(end)
        rows = self._connection.execute(
            f"SELECT label, COUNT(*) AS n FROM posthoc_labels "
            f"WHERE {' AND '.join(conditions)} GROUP BY label",
            parameters,
        )
        return {str(row["label"]): int(row["n"]) for row in rows}

    def coverage(
        self,
        scope: str,
        *,
        rule_hash: str,
        start: str | None = None,
        end: str | None = None,
    ) -> float | None:
        """Labeled / eligible ratio over the window; None when no eligible rows."""
        conditions: list[str] = ["scope = ?", "rule_hash = ?", "status != 'data_insufficient'", "status != 'unavailable'"]
        parameters: list[Any] = [scope, rule_hash]
        if start is not None:
            conditions.append("trading_date >= ?")
            parameters.append(start)
        if end is not None:
            conditions.append("trading_date <= ?")
            parameters.append(end)
        row = self._connection.execute(
            f"SELECT COUNT(*) AS total, "
            f"SUM(CASE WHEN label IS NOT NULL THEN 1 ELSE 0 END) AS labeled "
            f"FROM posthoc_labels WHERE {' AND '.join(conditions)}",
            parameters,
        ).fetchone()
        total = int(row["total"] or 0)
        if total == 0:
            return None
        return float(row["labeled"] or 0) / total


def _iso_date(value: Any) -> str:
    if isinstance(value, str):
        return value[:10]
    text = str(value)
    return text[:10]


def _nullable(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text and text != "<NA>" and text != "nan" else None


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if hasattr(value, "item") and not isinstance(value, (str, int, float, bool)):
        try:
            return value.item()
        except Exception:
            return str(value)
    return value


__all__ = ["LabelLedger", "StoredLabel"]
