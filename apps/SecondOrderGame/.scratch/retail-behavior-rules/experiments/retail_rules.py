"""Retail-layer behavior rule study — candidate rules and deterministic masks.

DRAFT STUDY — NOT FROZEN (ADR-0007 requires independent freeze with hash).

The retail labeler describes the *herd* participant (羊群散户) whose behavior
is emotion and follow driven: FOMO追高 / 恐慌割肉 / 观望 / 理性跟随 / 底部建仓 /
高位减仓.  Unlike the main-force labeler (price/chip control), retail behavior
is identified by:

- price position (高位追 vs 低位接),
- volume anomaly (放量情绪 vs 缩量观望),
- small-order flow (小单 = 散户资金: sml_in_flow + mid_in_flow),
- forward excess return (what the herd gets after acting).

Small-order flow is the distinguishing retail evidence.  When the flow column
is absent (historical data gap), the rules degrade to OHLCV-only evidence and
the row is flagged ``flow_unavailable`` instead of silently pretending the
flow was neutral.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


RETAIL_LABELS = ("FOMO追高", "恐慌割肉", "观望", "理性跟随", "底部建仓", "高位减仓")
_ROOT_KEYS = {
    "version", "draft", "evidence", "forward_return_feature",
    "features", "thresholds", "rules", "priority",
    # Production-config metadata (ignored by the study loader):
    "forward_return", "lookback", "zero_range_bar", "suspension_policy",
    "unlabeled", "rule_hash",
}
_RULE_KEYS = {"all", "any"}
_CLAUSE_KEYS = {"feature", "op", "threshold", "value", "invert"}
_OPERATORS = {
    "gt", "gte", "lt", "lte", "eq", "ne", "abs_gt", "abs_gte", "abs_lt", "abs_lte"
}


@dataclass(frozen=True)
class RetailRuleConfig:
    version: str
    draft: bool
    forward_return_feature: str
    evidence: Mapping[str, Any]
    thresholds: Mapping[str, float]
    rules: Mapping[str, Mapping[str, Sequence[Mapping[str, Any]]]]
    priority: tuple[str, ...]


def _validate_retail_labels(labels: Sequence[str], *, field: str) -> None:
    if (
        isinstance(labels, (str, bytes))
        or len(labels) != len(set(labels))
        or set(labels) != set(RETAIL_LABELS)
    ):
        raise ValueError(f"{field} must contain each retail label exactly once")


def load_retail_rule_config(path: str | Path) -> RetailRuleConfig:
    """Load and validate a candidate retail rule set without executing it."""

    source = Path(path)
    with source.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, Mapping):
        raise ValueError("retail rule configuration must be a mapping")
    unknown_root = set(raw) - _ROOT_KEYS
    if unknown_root:
        raise ValueError(f"unknown root keys: {', '.join(sorted(unknown_root))}")
    version = raw.get("version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("retail rule configuration must declare a version")
    draft = bool(raw.get("draft", False))
    forward_feature = raw.get("forward_return_feature")
    if forward_feature not in {"forward_excess_return", "forward_stock_return"}:
        raise ValueError("forward_return_feature must be forward_excess_return or forward_stock_return")
    evidence = raw.get("evidence")
    if not isinstance(evidence, Mapping):
        raise ValueError("retail rule configuration must declare evidence")
    thresholds = raw.get("thresholds")
    rules = raw.get("rules")
    priority = raw.get("priority")
    if not isinstance(thresholds, Mapping) or not thresholds:
        raise ValueError("thresholds must be a non-empty mapping")
    if not isinstance(rules, Mapping) or not rules:
        raise ValueError("rules must be a non-empty mapping")
    if not isinstance(priority, (list, tuple)) or len(priority) != len(RETAIL_LABELS):
        raise ValueError("priority must contain each retail label exactly once")
    _validate_retail_labels(priority, field="priority")
    _validate_retail_labels(tuple(rules), field="rules")
    for label, clauses in rules.items():
        if not isinstance(clauses, Mapping) or not clauses:
            raise ValueError(f"rule {label} must contain non-empty clauses")
        unknown_rule_key = set(clauses) - _RULE_KEYS
        if unknown_rule_key:
            raise ValueError(f"rule {label} has unknown keys: {', '.join(sorted(unknown_rule_key))}")
        for mode, items in clauses.items():
            if not isinstance(items, (list, tuple)) or not items:
                raise ValueError(f"rule {label}.{mode} must be a non-empty list")
            for clause in items:
                _validate_clause(clause, label)
    return RetailRuleConfig(
        version=version,
        draft=draft,
        forward_return_feature=str(forward_feature),
        evidence=dict(evidence),
        thresholds={str(key): float(value) for key, value in thresholds.items()},
        rules=rules,
        priority=tuple(priority),
    )


def _validate_clause(clause: object, label: str) -> None:
    if not isinstance(clause, Mapping):
        raise ValueError(f"rule {label} contains a non-mapping clause")
    unknown = set(clause) - _CLAUSE_KEYS
    if unknown:
        raise ValueError(f"rule {label} clause has unknown keys: {', '.join(sorted(unknown))}")
    feature = clause.get("feature")
    op = clause.get("op")
    if not isinstance(feature, str) or not feature.strip():
        raise ValueError(f"rule {label} clause must declare a feature")
    if op not in _OPERATORS:
        raise ValueError(f"rule {label} clause has unsupported op {op!r}")
    has_threshold = "threshold" in clause
    has_value = "value" in clause
    if has_threshold == has_value:
        raise ValueError(f"rule {label} clause must declare exactly one of threshold/value")
    if has_threshold and not isinstance(clause["threshold"], (int, float, str)):
        raise ValueError(f"rule {label} threshold must be numeric or a named threshold")
    if has_value and not isinstance(clause["value"], (str, bool, int, float)):
        raise ValueError(f"rule {label} value must be a scalar")


def _feature(frame: pd.DataFrame, name: str) -> pd.Series:
    if name not in frame:
        return pd.Series(pd.NA, index=frame.index, dtype="float64")
    return pd.to_numeric(frame[name], errors="coerce")


def evaluate_rule_masks(
    frame: pd.DataFrame, config: RetailRuleConfig
) -> pd.DataFrame:
    """Evaluate every rule against the feature frame; returns boolean masks."""

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a pandas DataFrame")
    result = pd.DataFrame(index=frame.index)
    for label in config.priority:
        clauses = config.rules[label]
        all_items = clauses.get("all", ())
        any_items = clauses.get("any", ())
        all_mask = pd.Series(True, index=frame.index)
        for clause in all_items:
            all_mask &= _evaluate_clause(frame, clause, config)
        any_mask = pd.Series(False, index=frame.index)
        for clause in any_items:
            any_mask |= _evaluate_clause(frame, clause, config)
        combined = all_mask
        if any_items:
            combined &= any_mask
        result[label] = combined.fillna(False).astype(bool)
    return result


def _evaluate_clause(
    frame: pd.DataFrame, clause: Mapping[str, Any], config: RetailRuleConfig
) -> pd.Series:
    feature = str(clause["feature"])
    op = str(clause["op"])
    if "threshold" in clause:
        raw = clause["threshold"]
        value = (
            float(config.thresholds[str(raw)])
            if isinstance(raw, str)
            else float(raw)
        )
    else:
        value = clause["value"]
    series = _feature(frame, feature)
    invert = bool(clause.get("invert", False))
    mask = _apply_op(series, op, value)
    return (~mask) if invert else mask


def _apply_op(series: pd.Series, op: str, value: Any) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    if op == "gt":
        return numeric > value
    if op == "gte":
        return numeric >= value
    if op == "lt":
        return numeric < value
    if op == "lte":
        return numeric <= value
    if op == "eq":
        return numeric == value
    if op == "ne":
        return numeric != value
    if op == "abs_gt":
        return numeric.abs() > value
    if op == "abs_gte":
        return numeric.abs() >= value
    if op == "abs_lt":
        return numeric.abs() < value
    if op == "abs_lte":
        return numeric.abs() <= value
    raise ValueError(f"unsupported operator {op!r}")


def resolve_fixed_priority(
    masks: pd.DataFrame, priority: Sequence[str]
) -> pd.Series:
    """Resolve overlapping masks by fixed priority; unmatched rows stay NA."""

    if not isinstance(masks, pd.DataFrame):
        raise TypeError("masks must be a pandas DataFrame")
    labels = pd.Series(pd.NA, index=masks.index, dtype="string")
    for label in priority:
        labels.loc[labels.isna() & masks[label].fillna(False).astype(bool)] = label
    return labels


__all__ = [
    "RETAIL_LABELS",
    "RetailRuleConfig",
    "evaluate_rule_masks",
    "load_retail_rule_config",
    "resolve_fixed_priority",
]
