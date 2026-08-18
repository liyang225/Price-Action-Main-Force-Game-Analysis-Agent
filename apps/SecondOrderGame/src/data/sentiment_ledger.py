"""Persistent, per-sector sentiment index state."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
import json
import math
from numbers import Real
from pathlib import Path
import sqlite3


class SentimentLedgerError(RuntimeError):
    """The persisted sentiment-ledger state cannot safely be used."""


@dataclass(frozen=True, slots=True)
class SentimentState:
    """The independently persistent continuous sentiment state of one sector."""

    sector_code: str
    sentiment_index: float
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class BeliefCheckpoint:
    """A durable HMM checkpoint for one sector's latest closed K_120M bar."""

    sector_code: str
    config_version: int
    belief: Mapping[str, float]
    last_k120m_closed_at: datetime


class SentimentLedger:
    """Store and recover independent, cross-day sector state.

    It holds the continuous sentiment index and HMM checkpoints. Analysis
    reports, intraday caches, and prediction histories own separate seams.
    """

    _REQUIRED_COLUMNS = {"sector_code", "sentiment_index", "updated_at"}
    _BELIEF_REQUIRED_COLUMNS = {
        "sector_code",
        "config_version",
        "belief_json",
        "last_k120m_closed_at",
    }
    _DAILY_BASE_REQUIRED_COLUMNS = {"sector_code", "trading_date", "base_index"}

    def __init__(self, database: str | Path | sqlite3.Connection) -> None:
        self._owns_connection = not isinstance(database, sqlite3.Connection)
        if self._owns_connection:
            database_path = Path(database)
            database_path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(database_path)
        else:
            self._connection = database
        self._connection.row_factory = sqlite3.Row
        self._initialize()

    def close(self) -> None:
        if self._owns_connection:
            self._connection.close()

    def __enter__(self) -> "SentimentLedger":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def save(self, state: SentimentState) -> SentimentState:
        """Persist the latest state for exactly one sector."""

        _validate_state(state)
        self._connection.execute(
            """
            INSERT INTO sector_sentiment_ledger (sector_code, sentiment_index, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(sector_code) DO UPDATE SET
                sentiment_index = excluded.sentiment_index,
                updated_at = excluded.updated_at
            """,
            (state.sector_code, state.sentiment_index, state.updated_at.isoformat()),
        )
        self._connection.commit()
        return state

    def load(self, sector_code: str) -> SentimentState | None:
        """Return a sector's last persistent state, if it has one."""

        _validate_sector_code(sector_code)
        row = self._connection.execute(
            "SELECT sector_code, sentiment_index, updated_at "
            "FROM sector_sentiment_ledger WHERE sector_code = ?",
            (sector_code,),
        ).fetchone()
        if row is None:
            return None
        try:
            state = SentimentState(
                sector_code=row["sector_code"],
                sentiment_index=float(row["sentiment_index"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
            _validate_state(state)
        except (KeyError, TypeError, ValueError) as exc:
            raise SentimentLedgerError(
                f"persisted sentiment state for sector {sector_code!r} is invalid"
            ) from exc
        return state

    def list_states(self) -> tuple[SentimentState, ...]:
        """Return every persisted sector state ordered by canonical code."""
        rows = self._connection.execute(
            "SELECT sector_code, sentiment_index, updated_at "
            "FROM sector_sentiment_ledger ORDER BY sector_code"
        ).fetchall()
        states: list[SentimentState] = []
        for row in rows:
            try:
                state = SentimentState(
                    sector_code=row["sector_code"],
                    sentiment_index=float(row["sentiment_index"]),
                    updated_at=datetime.fromisoformat(row["updated_at"]),
                )
                _validate_state(state)
            except (KeyError, TypeError, ValueError) as exc:
                raise SentimentLedgerError("persisted sentiment state is invalid") from exc
            states.append(state)
        return tuple(states)

    def daily_base(self, sector_code: str, trading_date: str, fallback: float) -> float:
        """Return the fixed prior-day base used for every recalculation that day."""
        _validate_sector_code(sector_code)
        value = float(fallback)
        if not math.isfinite(value) or not 0 <= value <= 100:
            raise ValueError("fallback must be between 0 and 100")
        row = self._connection.execute(
            "SELECT trading_date, base_index FROM sector_sentiment_daily_base WHERE sector_code = ?",
            (sector_code,),
        ).fetchone()
        if row is not None and row["trading_date"] == trading_date:
            return float(row["base_index"])
        self._connection.execute(
            """
            INSERT INTO sector_sentiment_daily_base (sector_code, trading_date, base_index)
            VALUES (?, ?, ?)
            ON CONFLICT(sector_code) DO UPDATE SET
                trading_date = excluded.trading_date,
                base_index = excluded.base_index
            """,
            (sector_code, trading_date, value),
        )
        self._connection.commit()
        return value

    def save_belief(self, checkpoint: BeliefCheckpoint) -> BeliefCheckpoint:
        """Persist the latest HMM belief for exactly one sector."""

        _validate_belief_checkpoint(checkpoint)
        belief = {state: float(value) for state, value in checkpoint.belief.items()}
        self._connection.execute(
            """
            INSERT INTO sector_hmm_belief (
                sector_code, config_version, belief_json, last_k120m_closed_at
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(sector_code) DO UPDATE SET
                config_version = excluded.config_version,
                belief_json = excluded.belief_json,
                last_k120m_closed_at = excluded.last_k120m_closed_at
            """,
            (
                checkpoint.sector_code,
                checkpoint.config_version,
                json.dumps(belief, ensure_ascii=False, sort_keys=True),
                checkpoint.last_k120m_closed_at.isoformat(),
            ),
        )
        self._connection.commit()
        return BeliefCheckpoint(
            sector_code=checkpoint.sector_code,
            config_version=checkpoint.config_version,
            belief=belief,
            last_k120m_closed_at=checkpoint.last_k120m_closed_at,
        )

    def load_belief(self, sector_code: str) -> BeliefCheckpoint | None:
        """Return a sector's HMM checkpoint, if one has been persisted."""

        _validate_sector_code(sector_code)
        row = self._connection.execute(
            "SELECT sector_code, config_version, belief_json, last_k120m_closed_at "
            "FROM sector_hmm_belief WHERE sector_code = ?",
            (sector_code,),
        ).fetchone()
        if row is None:
            return None
        try:
            decoded_belief = json.loads(row["belief_json"])
            if not isinstance(decoded_belief, dict):
                raise ValueError("belief_json must contain an object")
            checkpoint = BeliefCheckpoint(
                sector_code=row["sector_code"],
                config_version=row["config_version"],
                belief=decoded_belief,
                last_k120m_closed_at=datetime.fromisoformat(row["last_k120m_closed_at"]),
            )
            _validate_belief_checkpoint(checkpoint)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SentimentLedgerError(
                f"persisted HMM belief for sector {sector_code!r} is invalid"
            ) from exc
        return BeliefCheckpoint(
            sector_code=checkpoint.sector_code,
            config_version=checkpoint.config_version,
            belief=dict(checkpoint.belief),
            last_k120m_closed_at=checkpoint.last_k120m_closed_at,
        )

    def _initialize(self) -> None:
        existing_columns = self._table_columns("sector_sentiment_ledger")
        if existing_columns and existing_columns != self._REQUIRED_COLUMNS:
            raise SentimentLedgerError(
                "incompatible sentiment-ledger schema; migration is required"
            )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sector_sentiment_ledger (
                sector_code TEXT PRIMARY KEY,
                sentiment_index REAL NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        belief_columns = self._table_columns("sector_hmm_belief")
        if belief_columns and belief_columns != self._BELIEF_REQUIRED_COLUMNS:
            raise SentimentLedgerError(
                "incompatible HMM-belief schema; migration is required"
            )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sector_hmm_belief (
                sector_code TEXT PRIMARY KEY,
                config_version INTEGER NOT NULL,
                belief_json TEXT NOT NULL,
                last_k120m_closed_at TEXT NOT NULL
            )
            """
        )
        daily_base_columns = self._table_columns("sector_sentiment_daily_base")
        if daily_base_columns and daily_base_columns != self._DAILY_BASE_REQUIRED_COLUMNS:
            raise SentimentLedgerError(
                "incompatible sentiment daily-base schema; migration is required"
            )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sector_sentiment_daily_base (
                sector_code TEXT PRIMARY KEY,
                trading_date TEXT NOT NULL,
                base_index REAL NOT NULL
            )
            """
        )
        self._connection.commit()

    def _table_columns(self, table_name: str) -> set[str]:
        return {
            row["name"]
            for row in self._connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        }


def _validate_state(state: SentimentState) -> None:
    if not isinstance(state, SentimentState):
        raise TypeError("state must be a SentimentState")
    _validate_sector_code(state.sector_code)
    value = state.sentiment_index
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise ValueError("sentiment_index must be a finite number between 0 and 100")
    if not 0 <= value <= 100:
        raise ValueError("sentiment_index must be between 0 and 100")
    if not isinstance(state.updated_at, datetime):
        raise ValueError("updated_at must be a datetime")


def _validate_sector_code(sector_code: str) -> None:
    if not isinstance(sector_code, str) or not sector_code.strip():
        raise ValueError("sector_code must be a non-empty string")


def _validate_belief_checkpoint(checkpoint: BeliefCheckpoint) -> None:
    if not isinstance(checkpoint, BeliefCheckpoint):
        raise TypeError("checkpoint must be a BeliefCheckpoint")
    _validate_sector_code(checkpoint.sector_code)
    if (
        isinstance(checkpoint.config_version, bool)
        or not isinstance(checkpoint.config_version, int)
        or checkpoint.config_version < 1
    ):
        raise ValueError("config_version must be a positive integer")
    if not isinstance(checkpoint.last_k120m_closed_at, datetime):
        raise ValueError("last_k120m_closed_at must be a datetime")
    if not isinstance(checkpoint.belief, Mapping) or not checkpoint.belief:
        raise ValueError("belief must be a non-empty mapping")

    total = 0.0
    for state, value in checkpoint.belief.items():
        if not isinstance(state, str) or not state:
            raise ValueError("belief state names must be non-empty strings")
        if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
            raise ValueError("belief values must be finite numbers")
        if value < 0:
            raise ValueError("belief values must not be negative")
        total += float(value)
    if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("belief must sum to 1.0")
