"""Live preview data built with the production HMM forward filter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from src.config_validator import validate
from src.hmm_filter import HMMFilter
from src.labeler_constants import CYCLE_STATES, PARTICIPANTS, behaviors_for
from src.probability.disclaimer import disclaimer_for_prior_weight


@dataclass(frozen=True, slots=True)
class HeatmapData:
    title: str
    row_labels: tuple[str, ...]
    column_labels: tuple[str, ...]
    values: tuple[tuple[float, ...], ...]


@dataclass(frozen=True, slots=True)
class PreviewData:
    config_version: int
    posterior_belief: dict[str, float]
    behavior_distributions: dict[str, dict[str, float]]
    heatmaps: dict[str, HeatmapData]
    # The preview starts with zero observations, so every A-class row is
    # explicitly prior-led.  Keeping this per participant/behavior prevents a
    # future calibrated row from inheriting a global page-level guess.
    prior_weights: dict[str, dict[str, float]] = field(default_factory=dict)
    disclaimers: dict[str, dict[str, str | None]] = field(default_factory=dict)


def build_preview(
    config: Mapping[str, Any],
    *,
    belief: Mapping[str, float] | None = None,
    observation: str = "发酵",
    policy: str = "无干预",
) -> PreviewData:
    """Validate a draft, run one production forward step, and shape UI data."""
    copied = _copy_mapping(config)
    validate(copied)
    engine = HMMFilter(copied, sector_name="参数预览")
    if belief is not None:
        engine.restore_belief(belief)
    posterior = engine.update(observation)
    behaviors = {
        participant: engine.predict_behaviors(participant, policy=policy)
        for participant in PARTICIPANTS
    }
    prior_weights = {
        participant: {behavior: 1.0 for behavior in distribution}
        for participant, distribution in behaviors.items()
    }
    disclaimers = {
        participant: {
            behavior: disclaimer_for_prior_weight(weight)
            for behavior, weight in weights.items()
        }
        for participant, weights in prior_weights.items()
    }
    return PreviewData(
        config_version=int(copied["version"]),
        posterior_belief=posterior,
        behavior_distributions=behaviors,
        heatmaps=_build_heatmaps(copied),
        prior_weights=prior_weights,
        disclaimers=disclaimers,
    )


def _build_heatmaps(config: dict[str, Any]) -> dict[str, HeatmapData]:
    states = tuple(CYCLE_STATES)
    transition = tuple(
        tuple(float(config["transition_matrix"][row][column]) for column in states)
        for row in states
    )
    confusion = tuple(
        tuple(
            float(config["confusion_matrix"][f"true_{row}"][f"llm_{column}"])
            for column in states
        )
        for row in states
    )
    heatmaps = {
        "A": HeatmapData("A · 周期转移", states, states, transition),
        "C": HeatmapData("C · 观测混淆", states, states, confusion),
    }
    for participant in PARTICIPANTS:
        behaviors = behaviors_for(participant)
        heatmaps[f"W · {participant}"] = HeatmapData(
            f"W · {participant}行为",
            states,
            behaviors,
            tuple(
                tuple(
                    float(config["behavior_mapping"][state][participant][behavior])
                    for behavior in behaviors
                )
                for state in states
            ),
        )
    return heatmaps


def _copy_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    from copy import deepcopy

    return deepcopy(dict(value))
