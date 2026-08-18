"""Shared display policy for expert-prior probability results."""

from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any


DISCLAIMER_TEXT = "专家先验推演，非统计估计"
PRIOR_DISCLAIMER_THRESHOLD = 0.20


def disclaimer_for_prior_weight(
    prior_weight: float,
    *,
    threshold: float = PRIOR_DISCLAIMER_THRESHOLD,
) -> str | None:
    """Return the mandatory label while this particular row is prior-led."""
    _require_probability(prior_weight, "prior_weight")
    _require_probability(threshold, "threshold")
    return DISCLAIMER_TEXT if prior_weight >= threshold else None


def annotate_probability_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Add the shared UI label without changing the probability value."""
    if "prior_weight" not in row:
        raise ValueError("probability row must contain prior_weight")
    result = dict(row)
    label = disclaimer_for_prior_weight(float(row["prior_weight"]))
    if label is not None:
        result["disclaimer"] = label
    else:
        result.pop("disclaimer", None)
    return result


def _require_probability(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a probability")
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{name} must be between 0 and 1")


__all__ = [
    "DISCLAIMER_TEXT",
    "PRIOR_DISCLAIMER_THRESHOLD",
    "annotate_probability_row",
    "disclaimer_for_prior_weight",
]
