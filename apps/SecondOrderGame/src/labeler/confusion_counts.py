"""C-matrix count backfill: pair post-hoc sector labels with live LLM labels.

The HMM treats the LLM cycle classifier as a noisy sensor: ``C[true_z][llm]``
is the probability that the model emits ``llm`` when the true cycle is ``z``
(ARCHITECTURE.md §4.1).  The sector labeler produces the *true* cycle with a
look-ahead window; the production chain produces the live LLM observation.

This module persists both streams and reconciles them into confusion counts:

1. ``record_llm_observation`` stores each live model label per sector/day.
2. ``reconcile`` pairs a labeled sector day (true z, from :class:`LabelLedger`)
   with the stored LLM observation for the same day, then increments
   ``C[true_z][llm]`` exactly once per (sector, trading_date).
3. ``posterior`` fuses the Dirichlet prior (``alpha`` rows from
   ``hmm_prior.yaml``) with the accumulated counts to produce the calibrated
   C matrix used by HMMFilter.

Rule-hash isolation is preserved: counts keyed on ``rule_hash`` so a labeler
rule change never mixes old and new confusion evidence.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from src.labeler.ledger import LabelLedger


class ConfusionCountStore:
    """SQLite persistence for LLM observations and C confusion counts."""

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
            CREATE TABLE IF NOT EXISTS llm_observations (
                sector_code TEXT NOT NULL,
                trading_date TEXT NOT NULL,
                llm_label TEXT NOT NULL,
                reconciled INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                PRIMARY KEY (sector_code, trading_date)
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS confusion_counts (
                rule_hash TEXT NOT NULL,
                true_state TEXT NOT NULL,
                llm_state TEXT NOT NULL,
                count INTEGER NOT NULL,
                PRIMARY KEY (rule_hash, true_state, llm_state)
            )
            """
        )
        self._connection.commit()

    def close(self) -> None:
        if self._owns_connection:
            self._connection.close()

    def __enter__(self) -> "ConfusionCountStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # LLM observation stream (production writes)
    # ------------------------------------------------------------------

    def record_llm_observation(
        self, sector_code: str, trading_date: str, llm_label: str
    ) -> None:
        """Store one live cycle label for a sector/day (idempotent overwrite)."""
        if not sector_code or not trading_date or not llm_label:
            raise ValueError("sector_code, trading_date and llm_label are required")
        self._connection.execute(
            """
            INSERT INTO llm_observations
            (sector_code, trading_date, llm_label, reconciled, created_at)
            VALUES (?, ?, ?, 0, ?)
            ON CONFLICT(sector_code, trading_date) DO UPDATE SET
                llm_label = excluded.llm_label,
                reconciled = 0
            """,
            (sector_code, trading_date, llm_label, datetime.now().isoformat(timespec="seconds")),
        )
        self._connection.commit()

    def unreconciled_observations(self) -> tuple[Mapping[str, Any], ...]:
        rows = self._connection.execute(
            "SELECT sector_code, trading_date, llm_label FROM llm_observations "
            "WHERE reconciled = 0 ORDER BY trading_date"
        )
        return tuple(dict(row) for row in rows)

    # ------------------------------------------------------------------
    # Reconcile: true label (ledger) x LLM label -> C counts
    # ------------------------------------------------------------------

    def reconcile(
        self,
        ledger: LabelLedger,
        *,
        rule_hash: str,
        trading_date: str | None = None,
    ) -> dict[str, int]:
        """Pair unreconciled LLM observations with labeled sector days.

        Returns the number of count increments performed.  A day is counted
        only when the sector labeler produced a concrete label (``labeled``
        status) for the same sector and trading date.
        """
        observations = self.unreconciled_observations()
        if not observations:
            return {}
        increments: dict[str, int] = {}
        for observation in observations:
            sector = str(observation["sector_code"])
            date_value = str(observation["trading_date"])
            if trading_date is not None and date_value != trading_date:
                continue
            true_labels = ledger.sector_labels(
                sector_code=sector, rule_hash=rule_hash, start=date_value, end=date_value, status="labeled"
            )
            if not true_labels:
                continue
            true_state = true_labels[0].label
            if true_state is None:
                continue
            llm_state = str(observation["llm_label"])
            key = (rule_hash, true_state, llm_state)
            self._connection.execute(
                """
                INSERT INTO confusion_counts (rule_hash, true_state, llm_state, count)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(rule_hash, true_state, llm_state) DO UPDATE SET
                    count = count + 1
                """,
                key,
            )
            increments[key] = increments.get(key, 0) + 1
            self._connection.execute(
                "UPDATE llm_observations SET reconciled = 1 WHERE sector_code = ? AND trading_date = ?",
                (sector, date_value),
            )
        self._connection.commit()
        return increments

    # ------------------------------------------------------------------
    # Counts and posterior
    # ------------------------------------------------------------------

    def counts(
        self,
        *,
        rule_hash: str,
        true_state: str | None = None,
    ) -> dict[tuple[str, str], int]:
        conditions = ["rule_hash = ?"]
        parameters: list[Any] = [rule_hash]
        if true_state is not None:
            conditions.append("true_state = ?")
            parameters.append(true_state)
        rows = self._connection.execute(
            f"SELECT true_state, llm_state, count FROM confusion_counts "
            f"WHERE {' AND '.join(conditions)}",
            parameters,
        )
        return {(str(row["true_state"]), str(row["llm_state"])): int(row["count"]) for row in rows}

    def posterior(
        self,
        *,
        rule_hash: str,
        prior: Mapping[str, Mapping[str, float]],
        alpha: Mapping[str, float] | None = None,
    ) -> dict[str, dict[str, float]]:
        """Fuse Dirichlet priors with accumulated counts into a C matrix.

        ``prior[true_state][llm_state]`` are the hand-written probabilities
        (config ``confusion_matrix``); ``alpha[true_state]`` is the prior
        strength.  The posterior row is ``(alpha*p_prior + count) / (alpha + n)``.
        States present only in one side are carried through with their prior or
        zero count so no state silently disappears.
        """
        counts = self.counts(rule_hash=rule_hash)
        alpha = alpha or {}
        states = _all_states(prior)
        result: dict[str, dict[str, float]] = {}
        for true_state in states:
            prior_row = prior.get(true_state, {})
            row: dict[str, float] = {}
            a = float(alpha.get(true_state, 1.0))
            total_count = 0
            for llm_state in states:
                count = counts.get((true_state, llm_state), 0)
                total_count += count
            for llm_state in states:
                prior_p = float(prior_row.get(llm_state, 0.0))
                count = counts.get((true_state, llm_state), 0)
                denominator = a + total_count
                value = (a * prior_p + count) / denominator if denominator > 0 else prior_p
                row[llm_state] = value
            total = sum(row.values())
            if total > 0:
                row = {key: value / total for key, value in row.items()}
            result[true_state] = row
        return result


def _all_states(prior: Mapping[str, Mapping[str, float]]) -> tuple[str, ...]:
    states: list[str] = []
    seen: set[str] = set()
    for true_state, row in prior.items():
        if true_state not in seen:
            states.append(true_state)
            seen.add(true_state)
        for llm_state in row:
            if llm_state not in seen:
                states.append(llm_state)
                seen.add(llm_state)
    return tuple(states)


def build_llm_observation_sink(
    database: Path | str,
) -> Callable[[str, str, str], None]:
    """Return a production-friendly observation recorder.

    The returned callable opens a short-lived :class:`ConfusionCountStore`
    per invocation so the orchestrator never holds a long-lived SQLite
    connection.  Failures are swallowed (observations are optional
    calibration material; they must never break a decision).
    """
    path = Path(database)

    def record(sector_code: str, trading_date: str, llm_label: str) -> None:
        if sector_code == "unknown" or trading_date == "unknown":
            return
        try:
            with ConfusionCountStore(path) as store:
                store.record_llm_observation(sector_code, trading_date, llm_label)
        except Exception:  # noqa: BLE001 — optional material must not break the decision
            return

    return record


__all__ = ["ConfusionCountStore", "build_llm_observation_sink"]
