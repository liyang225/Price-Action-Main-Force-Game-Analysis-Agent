"""W behavior-matrix count backfill: pair stock labels with sector labels.

The HMM behavior mapping ``W[z][participant][behavior]`` starts as hand-written
priors (config ``behavior_mapping``).  The post-hoc labelers produce the *data*
that lets the prior drift toward reality:

- the sector labeler produces the true cycle ``z`` for a sector/day;
- the stock (main-force) labeler produces the main-force ``behavior`` for a
  constituent stock/day.

Pairing a labeled sector day with the labeled behavior of its constituents on
the same trading day increments ``W[z][主力][behavior]``.

The retail W row is deliberately NOT backfilled here: it needs the retail
labeler (``RetailLabeler``), which is a separate draft.  Until that exists the
retail row keeps its prior (ADR-0018: never copy main-force labels into the
retail row).

Rule-hash isolation is preserved: counts are keyed on both the sector rule
hash (for ``z``) and the stock rule hash (for ``behavior``), so a labeler rule
change never mixes old and new evidence.
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from src.labeler.ledger import LabelLedger


class BehaviorCountStore:
    """SQLite persistence for W behavior counts and their posterior fusion."""

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

    def _initialize(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS behavior_counts (
                cycle_rule_hash TEXT NOT NULL,
                behavior_rule_hash TEXT NOT NULL,
                cycle_state TEXT NOT NULL,
                participant TEXT NOT NULL,
                behavior TEXT NOT NULL,
                count INTEGER NOT NULL,
                PRIMARY KEY (
                    cycle_rule_hash, behavior_rule_hash,
                    cycle_state, participant, behavior
                )
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS behavior_reconciled (
                sector_code TEXT NOT NULL,
                trading_date TEXT NOT NULL,
                PRIMARY KEY (sector_code, trading_date)
            )
            """
        )
        self._connection.commit()

    def close(self) -> None:
        if self._owns_connection:
            self._connection.close()

    def __enter__(self) -> "BehaviorCountStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # reconcile: (sector cycle z) x (constituent main-force behavior)
    # ------------------------------------------------------------------

    def reconcile(
        self,
        ledger: LabelLedger,
        *,
        sector_codes: Mapping[str, str] | None = None,
        cycle_rule_hash: str,
        behavior_rule_hash: str,
    ) -> dict[str, int]:
        """Pair labeled sector days with labeled constituent behaviors.

        ``sector_codes`` maps a stock code to its primary sector code.  When
        omitted, only stock labels carrying a ``sector_code`` feature are
        matched (see :meth:`LabelLedger.record_stock_labels`).  Only
        ``participant == "主力"`` stock rows are counted into the main-force W
        row; retail rows are skipped until the retail labeler exists.

        The current rule-hash pair is rebuilt from the immutable label ledger
        on every pass.  This is intentionally deterministic: stock labels and
        capital-flow participant evidence can arrive after the sector label,
        so a one-shot ``sector/day reconciled`` marker would permanently lose
        those late observations.

        Returns per-(cycle, behavior) count deltas.  An unchanged replay
        returns an empty mapping.
        """
        desired: dict[tuple[str, str, str], int] = {}
        sector_rows = ledger.sector_labels(rule_hash=cycle_rule_hash, status="labeled")
        for sector_row in sector_rows:
            cycle_state = sector_row.label
            if cycle_state is None:
                continue
            date_value = sector_row.trading_date
            sector_code = sector_row.entity
            # Constituent stock labels on the same day.
            if sector_codes is not None:
                stock_codes = [
                    code for code, sc in sector_codes.items() if sc == sector_code
                ]
                stock_rows = [
                    row
                    for code in stock_codes
                    for row in ledger.stock_labels(
                        code, rule_hash=behavior_rule_hash,
                        start=date_value, end=date_value, status="labeled",
                    )
                ]
            else:
                stock_rows = self._stock_rows_for_day(
                    ledger, sector_code, date_value, behavior_rule_hash
                )
            for stock_row in stock_rows:
                if stock_row.label is None:
                    continue
                participant = self._participant_of(stock_row)
                if participant != "主力":
                    continue
                key = (cycle_state, participant, stock_row.label)
                desired[key] = desired.get(key, 0) + 1

        previous = self.counts(
            cycle_rule_hash=cycle_rule_hash,
            behavior_rule_hash=behavior_rule_hash,
        )
        if previous == desired:
            return {}

        with self._connection:
            self._connection.execute(
                "DELETE FROM behavior_counts "
                "WHERE cycle_rule_hash = ? AND behavior_rule_hash = ?",
                (cycle_rule_hash, behavior_rule_hash),
            )
            self._connection.executemany(
                """
                INSERT INTO behavior_counts
                (cycle_rule_hash, behavior_rule_hash, cycle_state, participant, behavior, count)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        cycle_rule_hash,
                        behavior_rule_hash,
                        cycle_state,
                        participant,
                        behavior,
                        count,
                    )
                    for (cycle_state, participant, behavior), count in desired.items()
                ),
            )
        changed_keys = set(previous) | set(desired)
        return {
            str((cycle_rule_hash, behavior_rule_hash, *key)): (
                desired.get(key, 0) - previous.get(key, 0)
            )
            for key in changed_keys
            if desired.get(key, 0) != previous.get(key, 0)
        }

    def _stock_rows_for_day(
        self, ledger: LabelLedger, sector_code: str, date_value: str, behavior_rule_hash: str
    ) -> list[Any]:
        """Stock labels on a day whose feature_json carries the sector_code."""
        rows = []
        # No direct index; scan stock labels for the day and filter by feature.
        for stock_row in ledger.stock_labels(
            rule_hash=behavior_rule_hash, start=date_value, end=date_value, status="labeled"
        ):
            if self._sector_code_of(stock_row) == sector_code:
                rows.append(stock_row)
        return rows

    @staticmethod
    def _participant_of(stock_row: Any) -> str | None:
        """Recover the participant from the stored feature_json."""
        try:
            import json as _json

            feature = _json.loads(stock_row.feature_json or "{}")
            participant = feature.get("participant")
            return str(participant) if participant else None
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _sector_code_of(stock_row: Any) -> str | None:
        try:
            import json as _json

            feature = _json.loads(stock_row.feature_json or "{}")
            sector_code = feature.get("sector_code")
            return str(sector_code) if sector_code else None
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------
    # counts and posterior
    # ------------------------------------------------------------------

    def counts(
        self,
        *,
        cycle_rule_hash: str,
        behavior_rule_hash: str,
    ) -> dict[tuple[str, str, str], int]:
        rows = self._connection.execute(
            "SELECT cycle_state, participant, behavior, count FROM behavior_counts "
            "WHERE cycle_rule_hash = ? AND behavior_rule_hash = ?",
            (cycle_rule_hash, behavior_rule_hash),
        )
        return {
            (str(row["cycle_state"]), str(row["participant"]), str(row["behavior"])): int(row["count"])
            for row in rows
        }

    def posterior(
        self,
        *,
        cycle_rule_hash: str,
        behavior_rule_hash: str,
        prior: Mapping[str, Mapping[str, Mapping[str, float]]],
        alpha: Mapping[str, Mapping[str, float]] | None = None,
    ) -> dict[str, dict[str, dict[str, float]]]:
        """Fuse Dirichlet priors with counts into the W matrix.

        ``prior[cycle][participant][behavior]`` mirrors ``behavior_mapping``;
        ``alpha[cycle][participant]`` is the prior strength.  Each
        ``(cycle, participant)`` row is normalized independently.
        """
        counts = self.counts(
            cycle_rule_hash=cycle_rule_hash, behavior_rule_hash=behavior_rule_hash
        )
        alpha = alpha or {}
        result: dict[str, dict[str, dict[str, float]]] = {}
        for cycle, participants in prior.items():
            result[cycle] = {}
            for participant, prior_row in participants.items():
                if participant == "alpha":
                    continue
                row_alpha = float(
                    (alpha.get(cycle, {}) or {}).get(participant, 1.0)
                )
                total_count = sum(
                    count
                    for (c, p, _), count in counts.items()
                    if c == cycle and p == participant
                )
                fused: dict[str, float] = {}
                for behavior, prior_p in prior_row.items():
                    if behavior == "alpha":
                        continue
                    count = counts.get((cycle, participant, behavior), 0)
                    denominator = row_alpha + total_count
                    value = (
                        (row_alpha * float(prior_p) + count) / denominator
                        if denominator > 0
                        else float(prior_p)
                    )
                    fused[behavior] = value
                total = sum(fused.values())
                if total > 0:
                    fused = {k: v / total for k, v in fused.items()}
                result[cycle][participant] = fused
        return result


__all__ = ["BehaviorCountStore"]
