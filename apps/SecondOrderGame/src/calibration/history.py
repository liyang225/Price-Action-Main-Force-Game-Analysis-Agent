"""Production lifecycle for probability snapshots stored with analysis history.

The generic :mod:`src.calibration.tracker` deliberately knows nothing about
market bars.  This bridge owns the production-specific seams: it records the
canonical B-class distribution once per completed history item, resolves that
distribution from the next complete K_120M period, and exposes read-only
reports for the PA history tab.  It never mutates probability or HMM config.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sqlite3
from typing import Any

import yaml

from src.calibration.tracker import CalibrationTracker
from src.probability.models import DecisionPoint, ProbabilityResult, ProbabilityType


_SHANGHAI = timezone(timedelta(hours=8))
_ROOT = Path(__file__).resolve().parents[2]
_HISTORICAL_PROBABILITY_CONFIG = _ROOT / "config" / "probability_history.yaml"


class HistoryCalibrationStore:
    """Attach auditable forecast/actual pairs to an analysis-history database."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        minimum_sample_count: int = 30,
    ) -> None:
        self._connection = connection
        self._minimum_sample_count = minimum_sample_count
        self._tracker = CalibrationTracker(
            connection, min_samples=minimum_sample_count
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS history_calibration_links (
                prediction_id TEXT PRIMARY KEY,
                history_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                resolution_kind TEXT NOT NULL,
                resolution_json TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS history_calibration_symbol_index "
            "ON history_calibration_links (symbol, history_id)"
        )
        self._connection.commit()

    def record(self, history_id: int, payload: Mapping[str, Any]) -> int:
        """Persist the canonical B-class distribution from one analysis."""
        input_ = payload.get("input")
        input_ = input_ if isinstance(input_, Mapping) else {}
        materials = input_.get("materials")
        materials = materials if isinstance(materials, Mapping) else {}
        chain = materials.get("probability_chain")
        chain = chain if isinstance(chain, Mapping) else {}
        rows = chain.get("opening_distribution")
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            return 0

        from src.integration.production_context import load_production_probability_config

        opening_config = load_production_probability_config().opening
        symbol = str(input_.get("symbol") or "").strip()
        completed_at = str(payload.get("completed_at") or "").strip()
        if not symbol or not completed_at:
            return 0
        recorded_at = _aware_datetime(completed_at)

        written = 0
        seen: set[tuple[str, str, str, int]] = set()
        for value in rows:
            if not isinstance(value, dict) or value.get("status") != "available":
                continue
            try:
                prediction = ProbabilityResult.from_dict(value)
            except (KeyError, TypeError, ValueError):
                continue
            if prediction.probability_type is not ProbabilityType.OPENING_RANGE:
                continue
            ranges = _ranges_for_version(
                prediction.config_version, current_config=opening_config
            )
            if ranges is None:
                # Historical range boundaries are versioned. Never resolve a
                # snapshot when that exact version's contract is unavailable.
                continue
            identity = (
                prediction.probability_type.value,
                prediction.outcome,
                prediction.decision_point.value,
                prediction.config_version,
            )
            if identity in seen:
                continue
            seen.add(identity)
            prediction_id = (
                f"history:{history_id}:{prediction.probability_type.value}:"
                f"{prediction.decision_point.value}:{prediction.outcome}"
            )
            self._tracker.record_prediction(
                prediction_id, prediction, recorded_at=recorded_at
            )
            self._connection.execute(
                """
                INSERT OR IGNORE INTO history_calibration_links
                (prediction_id, history_id, symbol, completed_at,
                 resolution_kind, resolution_json)
                VALUES (?, ?, ?, ?, 'opening_range', ?)
                """,
                (
                    prediction_id,
                    history_id,
                    symbol,
                    completed_at,
                    json.dumps(ranges, ensure_ascii=False, sort_keys=True),
                ),
            )
            written += 1
        self._connection.commit()
        return written

    def reconcile(self, source: Any, *, as_of: str | date) -> int:
        """Backfill actual B outcomes that now have a complete target period."""
        end = as_of.isoformat() if isinstance(as_of, date) else str(as_of)[:10]
        rows = self._connection.execute(
            """
            SELECT l.history_id, l.prediction_id, l.symbol, l.completed_at,
                   l.resolution_json, p.decision_point
            FROM history_calibration_links AS l
            JOIN calibration_predictions AS p USING (prediction_id)
            WHERE l.resolution_kind = 'opening_range'
              AND p.actual_kind IS NULL
            ORDER BY l.history_id, l.prediction_id
            """
        ).fetchall()
        grouped: dict[int, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(int(row["history_id"]), []).append(row)

        resolved = 0
        for event_rows in grouped.values():
            first = event_rows[0]
            start = str(first["completed_at"])[:10]
            try:
                bars = source.get_kline(
                    str(first["symbol"]), "K_120M", start, end
                )
                period_return = _realized_opening_return(
                    bars,
                    start,
                    DecisionPoint(str(first["decision_point"])),
                )
                ranges = json.loads(str(first["resolution_json"]))
                actual = _outcome_for_return(period_return, ranges)
            except Exception:  # noqa: BLE001 — one unavailable symbol must not block others
                continue
            if actual is None:
                continue
            for row in event_rows:
                if self._tracker.record_actual_result(
                    str(row["prediction_id"]), actual
                ):
                    resolved += 1
        return resolved

    def summary(self, *, symbol: str | None = None) -> dict[str, Any]:
        """Return version-isolated Brier reports for the PA history display."""
        condition = "WHERE l.symbol = ?" if symbol else ""
        parameters: tuple[Any, ...] = (symbol,) if symbol else ()
        totals = self._connection.execute(
            f"""
            SELECT COUNT(*) AS predictions,
                   SUM(CASE WHEN p.actual_kind IS NOT NULL THEN 1 ELSE 0 END) AS resolved
            FROM history_calibration_links AS l
            JOIN calibration_predictions AS p USING (prediction_id)
            {condition}
            """,
            parameters,
        ).fetchone()
        group_condition = (
            "WHERE p.actual_kind IS NOT NULL AND l.symbol = ?"
            if symbol
            else "WHERE p.actual_kind IS NOT NULL"
        )
        rows = self._connection.execute(
            f"""
            SELECT p.probability_type, p.outcome, p.decision_point,
                   p.config_version, p.probability, p.prior_weight,
                   p.actual_kind, p.actual_boolean, p.actual_outcome
            FROM history_calibration_links AS l
            JOIN calibration_predictions AS p USING (prediction_id)
            {group_condition}
            ORDER BY p.probability_type, p.outcome, p.decision_point,
                     p.config_version, p.recorded_at
            """,
            parameters,
        ).fetchall()
        grouped: dict[tuple[str, str, str, int], list[sqlite3.Row]] = {}
        for row in rows:
            key = (
                str(row["probability_type"]),
                str(row["outcome"]),
                str(row["decision_point"]),
                int(row["config_version"]),
            )
            grouped.setdefault(key, []).append(row)

        reports = [self._report(key, values) for key, values in grouped.items()]
        prediction_count = int(totals["predictions"] or 0)
        resolved_count = int(totals["resolved"] or 0)
        available = any(item["status"] == "available" for item in reports)
        return {
            "status": (
                "available"
                if available
                else "insufficient_data"
                if prediction_count
                else "no_data"
            ),
            "minimum_sample_count": self._minimum_sample_count,
            "prediction_count": prediction_count,
            "resolved_prediction_count": resolved_count,
            "reports": reports,
        }

    def delete_history(self, history_id: int) -> None:
        prediction_ids = tuple(
            str(row["prediction_id"])
            for row in self._connection.execute(
                "SELECT prediction_id FROM history_calibration_links WHERE history_id = ?",
                (history_id,),
            )
        )
        with self._connection:
            self._connection.execute(
                "DELETE FROM history_calibration_links WHERE history_id = ?",
                (history_id,),
            )
            self._connection.executemany(
                "DELETE FROM calibration_predictions WHERE prediction_id = ?",
                ((prediction_id,) for prediction_id in prediction_ids),
            )

    def _report(
        self,
        key: tuple[str, str, str, int],
        rows: list[sqlite3.Row],
    ) -> dict[str, Any]:
        probability_type, outcome, decision_point, config_version = key
        sample_count = len(rows)
        base = {
            "probability_type": probability_type,
            "outcome": outcome,
            "decision_point": decision_point,
            "config_version": config_version,
            "sample_count": sample_count,
            "minimum_sample_count": self._minimum_sample_count,
        }
        if sample_count < self._minimum_sample_count:
            return {"status": "insufficient_data", **base}
        probabilities = [float(row["probability"]) for row in rows]
        actuals = [
            float(row["actual_boolean"])
            if row["actual_kind"] == "boolean"
            else float(str(row["actual_outcome"]) == outcome)
            for row in rows
        ]
        mean_probability = math.fsum(probabilities) / sample_count
        observed_frequency = math.fsum(actuals) / sample_count
        if math.isclose(mean_probability, observed_frequency, abs_tol=1e-12):
            direction = "hold"
        elif observed_frequency > mean_probability:
            direction = "increase"
        else:
            direction = "decrease"
        return {
            "status": "available",
            **base,
            "brier_score": math.fsum(
                (probability - actual) ** 2
                for probability, actual in zip(probabilities, actuals, strict=True)
            )
            / sample_count,
            "mean_predicted_probability": mean_probability,
            "observed_frequency": observed_frequency,
            "mean_prior_weight": math.fsum(
                float(row["prior_weight"]) for row in rows
            )
            / sample_count,
            "prior_adjustment_direction": direction,
        }


def _aware_datetime(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    return result if result.tzinfo is not None else result.replace(tzinfo=_SHANGHAI)


def _ranges_for_version(version: int, *, current_config: Any) -> list[dict[str, Any]] | None:
    if version == current_config.config_version:
        return [
            {
                "outcome": item.outcome,
                "lower_bound": item.lower_bound,
                "upper_bound": item.upper_bound,
            }
            for item in current_config.ranges
        ]
    try:
        raw = yaml.safe_load(
            _HISTORICAL_PROBABILITY_CONFIG.read_text(encoding="utf-8")
        )
        versions = raw.get("versions") if isinstance(raw, Mapping) else None
        config = versions.get(version) if isinstance(versions, Mapping) else None
        opening = config.get("opening_distribution") if isinstance(config, Mapping) else None
        ranges = opening.get("ranges") if isinstance(opening, Mapping) else None
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(ranges, list) or not ranges:
        return None
    return [dict(item) for item in ranges if isinstance(item, Mapping)] or None


def _realized_opening_return(
    bars: Sequence[Any], prediction_date: str, decision_point: DecisionPoint
) -> float | None:
    grouped: dict[str, list[tuple[datetime, Any]]] = {}
    for bar in bars:
        timestamp = datetime.fromisoformat(str(bar.time_key))
        grouped.setdefault(timestamp.date().isoformat(), []).append((timestamp, bar))
    complete = {
        day: tuple(bar for _, bar in sorted(values, key=lambda item: item[0]))
        for day, values in grouped.items()
        if len(values) == 2
    }
    current = complete.get(prediction_date)
    if current is None:
        return None
    if decision_point is DecisionPoint.MIDDAY:
        reference, realized = float(current[0].close), float(current[1].close)
    else:
        next_days = sorted(day for day in complete if day > prediction_date)
        if not next_days:
            return None
        reference = float(current[1].close)
        realized = float(complete[next_days[0]][0].close)
    if not math.isfinite(reference) or not math.isfinite(realized) or reference <= 0:
        return None
    return realized / reference - 1.0


def _outcome_for_return(
    period_return: float | None, ranges: Any
) -> str | None:
    if period_return is None or not isinstance(ranges, list):
        return None
    for item in ranges:
        if not isinstance(item, Mapping):
            continue
        lower = item.get("lower_bound")
        upper = item.get("upper_bound")
        if (lower is None or period_return >= float(lower)) and (
            upper is None or period_return < float(upper)
        ):
            outcome = str(item.get("outcome") or "").strip()
            return outcome or None
    return None


__all__ = ["HistoryCalibrationStore"]
