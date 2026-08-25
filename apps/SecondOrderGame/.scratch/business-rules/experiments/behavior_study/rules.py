"""YAML-driven candidate behavior rules and deterministic resolution."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


FROZEN_LABELS = ("建仓", "震仓", "拉升", "出货", "观望", "狩猎止损")
_ROOT_KEYS = {"version", "forward_return_feature", "thresholds", "rules", "priority"}
_RULE_KEYS = {"all", "any"}
_CLAUSE_KEYS = {"feature", "op", "threshold", "value", "invert"}
_OPERATORS = {
    "gt", "gte", "lt", "lte", "eq", "ne", "abs_gt", "abs_gte", "abs_lt", "abs_lte"
}


@dataclass(frozen=True)
class RuleConfig:
    version: str
    forward_return_feature: str
    thresholds: Mapping[str, float]
    rules: Mapping[str, Mapping[str, Sequence[Mapping[str, Any]]]]
    priority: tuple[str, ...]


def _validate_frozen_labels(labels: Sequence[str], *, field: str) -> None:
    if (
        isinstance(labels, (str, bytes))
        or len(labels) != len(set(labels))
        or set(labels) != set(FROZEN_LABELS)
    ):
        raise ValueError(f"{field} must contain each frozen behavior label exactly once")


def load_rule_config(path: str | Path) -> RuleConfig:
    """Load and validate a candidate rule set without executing it."""

    source = Path(path)
    with source.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, Mapping):
        raise ValueError("rule configuration must be a mapping")
    unknown_root = set(raw) - _ROOT_KEYS
    if unknown_root:
        names = ", ".join(sorted(map(str, unknown_root)))
        raise ValueError(f"rule configuration contains unknown key(s): {names}")
    rules = raw.get("rules")
    if not isinstance(rules, Mapping):
        raise ValueError("rule configuration must contain a rules mapping")
    _validate_frozen_labels(tuple(rules.keys()), field="rules")
    priority_raw = raw.get("priority", ())
    if not isinstance(priority_raw, Sequence) or isinstance(priority_raw, (str, bytes)):
        raise ValueError("priority must be a sequence")
    priority = tuple(priority_raw)
    _validate_frozen_labels(priority, field="priority")
    thresholds_raw = raw.get("thresholds", {})
    if not isinstance(thresholds_raw, Mapping):
        raise ValueError("thresholds must be a mapping")
    thresholds: dict[str, float] = {}
    for name, value in thresholds_raw.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("threshold names must be non-empty strings")
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise ValueError(f"threshold {name!r} must be numeric")
        thresholds[name.strip()] = float(value)
    version_raw = raw.get("version", "unversioned")
    if not isinstance(version_raw, str) or not version_raw.strip():
        raise ValueError("version must be a non-empty string")
    forward_raw = raw.get("forward_return_feature", "forward_excess_return")
    if not isinstance(forward_raw, str) or not forward_raw.strip():
        raise ValueError("forward_return_feature must be a non-empty string")
    validated_rules: dict[str, Mapping[str, Sequence[Mapping[str, Any]]]] = {}
    for label in FROZEN_LABELS:
        rule = rules[label]
        if not isinstance(rule, Mapping):
            raise ValueError(f"rules.{label} must be a mapping")
        unknown = set(rule) - _RULE_KEYS
        if unknown:
            names = ", ".join(sorted(map(str, unknown)))
            raise ValueError(f"rules.{label} contains unknown key(s): {names}")
        for group in _RULE_KEYS:
            clauses = rule.get(group, ())
            if not isinstance(clauses, Sequence) or isinstance(clauses, (str, bytes)):
                raise ValueError(f"rules.{label}.{group} must be a sequence")
            for index, clause in enumerate(clauses):
                _validate_clause(clause, f"rules.{label}.{group}[{index}]", thresholds)
        validated_rules[label] = rule
    return RuleConfig(
        version=version_raw.strip(),
        forward_return_feature=forward_raw.strip(),
        thresholds=thresholds,
        rules=validated_rules,
        priority=priority,
    )


def _validate_clause(clause: Any, path: str, thresholds: Mapping[str, float]) -> None:
    if not isinstance(clause, Mapping):
        raise ValueError(f"{path} must be a mapping")
    unknown = set(clause) - _CLAUSE_KEYS
    if unknown:
        raise ValueError(f"{path} contains unknown key(s): {', '.join(map(str, sorted(unknown)))}")
    feature = clause.get("feature")
    operation = clause.get("op")
    if not isinstance(feature, str) or not feature.strip():
        raise ValueError(f"{path}.feature must be a non-empty string")
    if not isinstance(operation, str) or operation not in _OPERATORS:
        raise ValueError(f"{path}.op has unsupported operator: {operation!r}")
    if ("threshold" in clause) == ("value" in clause):
        raise ValueError(f"{path} must define exactly one of threshold or value")
    if "threshold" in clause:
        threshold = clause["threshold"]
        if not isinstance(threshold, str) or threshold not in thresholds:
            raise ValueError(f"{path}.threshold references an unknown threshold")
    if "invert" in clause and not isinstance(clause["invert"], bool):
        raise ValueError(f"{path}.invert must be true or false")


def _operand(clause: Mapping[str, Any], config: RuleConfig) -> Any:
    has_threshold = "threshold" in clause
    has_value = "value" in clause
    if has_threshold == has_value:
        raise ValueError("each rule clause must define exactly one of threshold or value")
    if has_threshold:
        threshold_name = clause["threshold"]
        if threshold_name not in config.thresholds:
            raise ValueError(f"unknown threshold {threshold_name!r}")
        return config.thresholds[threshold_name]
    return clause["value"]


def _evaluate_clause(features: pd.DataFrame, clause: Mapping[str, Any], config: RuleConfig) -> pd.Series:
    if not isinstance(clause, Mapping):
        raise ValueError("rule clause must be a mapping")
    feature = clause.get("feature", "")
    if feature == "forward_return":
        feature = config.forward_return_feature
    if feature not in features:
        raise ValueError(f"rule references unavailable feature {feature!r}")
    operation = clause.get("op", "")
    operand = _operand(clause, config)
    values = features[feature]
    operations = {
        "gt": lambda: values > operand,
        "gte": lambda: values >= operand,
        "lt": lambda: values < operand,
        "lte": lambda: values <= operand,
        "eq": lambda: values == operand,
        "ne": lambda: values != operand,
        "abs_gt": lambda: values.abs() > operand,
        "abs_gte": lambda: values.abs() >= operand,
        "abs_lt": lambda: values.abs() < operand,
        "abs_lte": lambda: values.abs() <= operand,
    }
    if operation not in operations:
        raise ValueError(f"unsupported rule operator {operation!r}")
    mask = (operations[operation]() & values.notna()).fillna(False).astype(bool)
    invert = clause.get("invert", False)
    if not isinstance(invert, bool):
        raise ValueError("rule clause invert must be boolean")
    if invert:
        mask = ~mask
    return mask


def _evaluate_rule(features: pd.DataFrame, rule: Mapping[str, Any], config: RuleConfig) -> pd.Series:
    all_clauses = rule.get("all", ())
    any_clauses = rule.get("any", ())
    if not isinstance(all_clauses, Sequence) or isinstance(all_clauses, (str, bytes)):
        raise ValueError("rule all group must be a sequence")
    if not isinstance(any_clauses, Sequence) or isinstance(any_clauses, (str, bytes)):
        raise ValueError("rule any group must be a sequence")
    if not all_clauses and not any_clauses:
        return pd.Series(False, index=features.index, dtype=bool)
    result = pd.Series(True, index=features.index, dtype=bool)
    for clause in all_clauses:
        result &= _evaluate_clause(features, clause, config)
    if any_clauses:
        any_result = pd.Series(False, index=features.index, dtype=bool)
        for clause in any_clauses:
            any_result |= _evaluate_clause(features, clause, config)
        result &= any_result
    return result


def evaluate_rule_masks(features: pd.DataFrame, config: RuleConfig) -> pd.DataFrame:
    """Return one independent boolean candidate mask per frozen label."""

    masks = {
        label: _evaluate_rule(features, config.rules[label], config)
        for label in FROZEN_LABELS
    }
    return pd.DataFrame(masks, index=features.index).astype(bool)


def resolve_fixed_priority(masks: pd.DataFrame, priority: Sequence[str]) -> pd.Series:
    """Resolve overlaps to one label and leave truly unmatched rows missing."""

    missing = set(FROZEN_LABELS).difference(masks.columns)
    if missing:
        raise ValueError(f"masks are missing frozen labels: {', '.join(sorted(missing))}")
    _validate_frozen_labels(tuple(priority), field="priority")
    boolean_masks = masks.loc[:, list(FROZEN_LABELS)].fillna(False).astype(bool)
    resolved = pd.Series(pd.NA, index=masks.index, dtype="string", name="behavior_label")
    for label in priority:
        select = resolved.isna() & boolean_masks[label]
        resolved.loc[select] = label
    return resolved


__all__ = [
    "FROZEN_LABELS",
    "RuleConfig",
    "evaluate_rule_masks",
    "load_rule_config",
    "resolve_fixed_priority",
]
