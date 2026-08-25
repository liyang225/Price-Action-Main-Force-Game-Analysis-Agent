from __future__ import annotations

import pandas as pd
import pytest
from pathlib import Path

from behavior_study.rules import (
    FROZEN_LABELS,
    evaluate_rule_masks,
    load_rule_config,
    resolve_fixed_priority,
)
from behavior_study.stats import overlap_statistics


def _features() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "return_1d": [-0.04, 0.04, 0.00, 0.00, -0.05, 0.00],
            "forward_excess_return": [0.05, 0.08, 0.00, -0.05, 0.05, 0.00],
            "volume_ratio_20": [1.1, 1.5, 0.8, 1.8, 1.3, 1.0],
            "close_position": [0.7, 0.9, 0.5, 0.2, 0.8, 0.5],
            "lower_shadow_ratio": [0.5, 0.0, 0.0, 0.0, 0.6, 0.0],
            "upper_shadow_ratio": [0.0, 0.0, 0.0, 0.6, 0.0, 0.0],
            "support_break_pct": [0.0, 0.0, 0.0, 0.0, -0.08, 0.0],
            "resistance_break_pct": [0.0, 0.02, 0.0, -0.01, -0.05, 0.0],
            "future_rebound_return": [0.05, 0.08, 0.0, -0.01, 0.05, 0.0],
        }
    )


def test_yaml_rules_produce_all_frozen_masks() -> None:
    config = load_rule_config(Path(__file__).resolve().parents[1] / "config" / "behavior_rules.yaml")
    masks = evaluate_rule_masks(_features(), config)

    assert tuple(masks.columns) == FROZEN_LABELS
    assert masks.dtypes.eq(bool).all()
    assert masks.loc[0, "震仓"]
    assert not masks.loc[0, "狩猎止损"]
    assert masks.loc[4, "狩猎止损"]
    assert not masks.loc[4, "震仓"]
    assert not (masks["震仓"] & masks["狩猎止损"]).any()
    assert masks.loc[1, "拉升"]


def test_forward_feature_alias_can_be_switched_without_code_changes(tmp_path) -> None:
    path = tmp_path / "rules.yaml"
    path.write_text(
        """
forward_return_feature: forward_stock_return
thresholds:
  min_forward: 0.03
rules:
  建仓:
    all:
      - {feature: forward_return, op: gte, threshold: min_forward}
  震仓: {all: []}
  拉升: {all: []}
  出货: {all: []}
  观望: {all: []}
  狩猎止损: {all: []}
priority: [狩猎止损, 震仓, 拉升, 出货, 建仓, 观望]
""",
        encoding="utf-8",
    )
    config = load_rule_config(path)
    features = _features().assign(forward_stock_return=[0.04, 0.01, 0.02, 0.03, -0.01, 0.0])

    masks = evaluate_rule_masks(features, config)

    assert masks["建仓"].tolist() == [True, False, False, True, False, False]


def test_overlap_statistics_and_fixed_priority_keep_unmatched_as_na() -> None:
    masks = pd.DataFrame(
        {
            "建仓": [True, False, True, False],
            "震仓": [True, True, False, False],
            "拉升": [False, False, False, False],
            "出货": [False, False, False, False],
            "观望": [False, False, False, False],
            "狩猎止损": [False, False, False, False],
        }
    )
    stats = overlap_statistics(masks)

    assert stats["overlap_count"] == 1
    assert stats["unmatched_count"] == 1
    assert stats["combination_counts"][("建仓", "震仓")] == 1
    resolved = resolve_fixed_priority(masks, ["狩猎止损", "震仓", "建仓", "拉升", "出货", "观望"])
    assert resolved.iloc[:3].tolist() == ["震仓", "震仓", "建仓"]
    assert pd.isna(resolved.iloc[3])


def test_invalid_priority_is_rejected() -> None:
    masks = pd.DataFrame({label: [False] for label in FROZEN_LABELS})
    with pytest.raises(ValueError, match="priority"):
        resolve_fixed_priority(masks, ["建仓"])


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ("accumulation_current_return_max: .nan", "must be numeric"),
        (
            "- {feature: return_1d, op: gte, threshold: accumulation_current_return_min, invert: 'false'}",
            "invert must be true or false",
        ),
        (
            "- {feature: return_1d, op: gte, threshold: accumulation_current_return_min, typo: true}",
            "unknown key",
        ),
    ],
)
def test_rule_config_rejects_silent_yaml_mistakes(tmp_path, replacement, message) -> None:
    source = Path(__file__).resolve().parents[1] / "config" / "behavior_rules.yaml"
    text = source.read_text(encoding="utf-8")
    if replacement.startswith("accumulation_current_return_max"):
        text = text.replace("accumulation_current_return_max: 0.02", replacement, 1)
    else:
        original = "- {feature: return_1d, op: gte, threshold: accumulation_current_return_min}"
        text = text.replace(original, replacement, 1)
    path = tmp_path / "rules.yaml"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_rule_config(path)
