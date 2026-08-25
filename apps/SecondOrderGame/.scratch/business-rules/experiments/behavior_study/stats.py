"""Coverage, overlap and post-resolution statistics for candidate masks."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

import pandas as pd

from .rules import FROZEN_LABELS


def _validated_masks(masks: pd.DataFrame) -> pd.DataFrame:
    missing = set(FROZEN_LABELS).difference(masks.columns)
    if missing:
        raise ValueError(f"masks are missing frozen labels: {', '.join(sorted(missing))}")
    return masks.loc[:, list(FROZEN_LABELS)].fillna(False).astype(bool)


def overlap_statistics(masks: pd.DataFrame) -> dict[str, Any]:
    """Summarise independent hits, multi-hit combinations and empty rows."""

    clean = _validated_masks(masks)
    row_hits = clean.sum(axis=1)
    total = len(clean)
    combinations: Counter[tuple[str, ...]] = Counter()
    for _, row in clean.loc[row_hits > 0].iterrows():
        combinations[tuple(label for label in FROZEN_LABELS if bool(row[label]))] += 1
    per_label = {
        label: {
            "count": int(clean[label].sum()),
            "share": float(clean[label].sum() / total) if total else 0.0,
        }
        for label in FROZEN_LABELS
    }
    overlap_count = int((row_hits >= 2).sum())
    unmatched_count = int((row_hits == 0).sum())
    return {
        "row_count": total,
        "per_label": per_label,
        "overlap_count": overlap_count,
        "overlap_share": overlap_count / total if total else 0.0,
        "unmatched_count": unmatched_count,
        "unmatched_share": unmatched_count / total if total else 0.0,
        "combination_counts": dict(combinations),
        "combination_shares": {
            combination: count / total if total else 0.0
            for combination, count in combinations.items()
        },
    }


def resolved_distribution(labels: pd.Series, *, label_order: Sequence[str] = FROZEN_LABELS) -> dict[str, Any]:
    """Summarise a fixed-priority label series without hiding missing rows."""

    unknown = set(labels.dropna().unique()).difference(label_order)
    if unknown:
        raise ValueError(f"resolved labels contain unknown values: {', '.join(sorted(unknown))}")
    total = len(labels)
    counts = labels.value_counts(dropna=True)
    per_label = {
        label: {
            "count": int(counts.get(label, 0)),
            "share_of_all": float(counts.get(label, 0) / total) if total else 0.0,
        }
        for label in label_order
    }
    unmatched = int(labels.isna().sum())
    return {
        "row_count": total,
        "per_label": per_label,
        "unmatched_count": unmatched,
        "unmatched_share": unmatched / total if total else 0.0,
    }


__all__ = ["overlap_statistics", "resolved_distribution"]
