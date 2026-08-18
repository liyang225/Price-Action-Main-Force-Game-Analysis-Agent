"""Persisted, per-sector HMM forward-filter orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from src.data.sentiment_ledger import BeliefCheckpoint, SentimentLedger
from src.hmm_filter import Belief, HMMFilter
from src.labeler_constants import CYCLE_STATES


class BeliefUpdaterError(RuntimeError):
    """A persisted HMM checkpoint cannot safely continue filtering."""


@dataclass(frozen=True, slots=True)
class K120MCloseEvent:
    """One sector observation associated with a K_120M close notification."""

    sector_code: str
    closed_at: datetime
    observed_cycle_state: str
    is_complete: bool
    interval: str = "K_120M"


@dataclass(frozen=True, slots=True)
class BeliefUpdate:
    """The durable result of one newly processed K_120M close event."""

    sector_code: str
    closed_at: datetime
    belief: Belief


class BeliefUpdater:
    """Advance independent sector filters only from completed K_120M bars.

    A checkpoint records both the belief and the close timestamp. This makes
    same-bar delivery idempotent across process restarts without introducing a
    training or parameter-estimation path.
    """

    def __init__(self, config: Mapping[str, object], ledger: SentimentLedger) -> None:
        self._config = dict(config)
        self._ledger = ledger
        self._filters: dict[str, HMMFilter] = {}
        try:
            version = self._config["version"]
            if isinstance(version, bool) or not isinstance(version, int):
                raise ValueError
            self._config_version = version
        except (KeyError, ValueError) as exc:
            raise ValueError("HMM configuration must contain an integer version") from exc

    def update(self, event: K120MCloseEvent) -> BeliefUpdate | None:
        """Process a completed event, returning ``None`` for ignored delivery.

        Incomplete bars and repeat delivery of an already checkpointed bar do
        not consume a forward-filter step. An event older than the latest
        checkpoint is rejected because replay requires an explicit rebuild.
        """
        _validate_event(event)
        if not event.is_complete:
            return None

        checkpoint = self._ledger.load_belief(event.sector_code)
        if checkpoint is not None:
            if checkpoint.config_version != self._config_version:
                raise BeliefUpdaterError(
                    f"sector {event.sector_code!r} checkpoint uses HMM config "
                    f"v{checkpoint.config_version}, current config is v{self._config_version}"
                )
            if event.closed_at == checkpoint.last_k120m_closed_at:
                return None
            if event.closed_at < checkpoint.last_k120m_closed_at:
                raise BeliefUpdaterError(
                    f"sector {event.sector_code!r} received an out-of-order K_120M close"
                )

        filter_ = self._filter_for(event.sector_code, checkpoint)
        belief = filter_.update(event.observed_cycle_state)
        self._ledger.save_belief(
            BeliefCheckpoint(
                sector_code=event.sector_code,
                config_version=self._config_version,
                belief=belief,
                last_k120m_closed_at=event.closed_at,
            )
        )
        return BeliefUpdate(event.sector_code, event.closed_at, belief)

    def belief_for(self, sector_code: str) -> Belief | None:
        """Return the current in-memory or durable belief without advancing it."""
        filter_ = self._filters.get(sector_code)
        if filter_ is not None:
            return filter_.belief
        checkpoint = self._ledger.load_belief(sector_code)
        if checkpoint is None:
            return None
        if checkpoint.config_version != self._config_version:
            raise BeliefUpdaterError(
                f"sector {sector_code!r} checkpoint uses HMM config "
                f"v{checkpoint.config_version}, current config is v{self._config_version}"
            )
        return dict(checkpoint.belief)

    def _filter_for(
        self, sector_code: str, checkpoint: BeliefCheckpoint | None
    ) -> HMMFilter:
        filter_ = self._filters.get(sector_code)
        if filter_ is not None:
            return filter_

        filter_ = HMMFilter(self._config, sector_name=sector_code)
        if checkpoint is not None:
            try:
                filter_.restore_belief(checkpoint.belief)
            except ValueError as exc:
                raise BeliefUpdaterError(
                    f"sector {sector_code!r} has an invalid persisted HMM belief"
                ) from exc
        self._filters[sector_code] = filter_
        return filter_


def _validate_event(event: K120MCloseEvent) -> None:
    if not isinstance(event, K120MCloseEvent):
        raise TypeError("event must be a K120MCloseEvent")
    if not isinstance(event.sector_code, str) or not event.sector_code.strip():
        raise ValueError("sector_code must be a non-empty string")
    if not isinstance(event.closed_at, datetime):
        raise ValueError("closed_at must be a datetime")
    if event.observed_cycle_state not in CYCLE_STATES:
        raise ValueError(
            f"observed_cycle_state must be one of {', '.join(CYCLE_STATES)}"
        )
    if not isinstance(event.is_complete, bool):
        raise ValueError("is_complete must be a boolean")
    if event.interval != "K_120M":
        raise ValueError("belief updates require K_120M close events")
