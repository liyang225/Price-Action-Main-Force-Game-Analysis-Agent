"""
测试套件 — SecondOrderGame HMM 引擎
======================================
覆盖：
  - ConfigValidator：正常通过、各类错误场景
  - HMMFilter：解析、前向滤波更新、行为预测、政策修正、重置
  - 集成：从真实配置文件加载并运行完整滤波序列
"""

from __future__ import annotations

import copy
import math
from pathlib import Path

import pytest
import yaml

from src.config_validator import ConfigError, validate, CYCLE_STATES, BEHAVIORS
from src.hmm_filter import HMMFilter, load_config
from src.labeler_constants import MAIN_FORCE_BEHAVIORS, RETAIL_BEHAVIORS


# ---------------------------------------------------------------------------
# 测试夹具
# ---------------------------------------------------------------------------

CONFIG_PATH = Path(__file__).parent.parent / "config" / "hmm_prior.yaml"


@pytest.fixture(scope="session")
def real_config() -> dict:
    """从磁盘加载真实配置（整个测试会话共享一次 IO）。"""
    return load_config(CONFIG_PATH)


@pytest.fixture
def valid_cfg() -> dict:
    """最小有效配置，用于构造各种坏数据的基准。"""
    return load_config(CONFIG_PATH)


def _mutate(cfg: dict, *path_value_pairs) -> dict:
    """深拷贝后按路径修改，返回修改后的副本。path 是 list[str | int]。"""
    c = copy.deepcopy(cfg)
    for path, value in path_value_pairs:
        node = c
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
    return c


# ---------------------------------------------------------------------------
# ConfigValidator — 正常通过
# ---------------------------------------------------------------------------

class TestValidatorHappyPath:

    def test_real_config_passes(self, real_config):
        validate(real_config)  # 不抛出即通过

    def test_row_sums_exactly_one(self, real_config):
        tm = real_config["transition_matrix"]
        for state in CYCLE_STATES:
            row = tm[state]
            total = sum(v for k, v in row.items() if k != "alpha")
            assert abs(total - 1.0) < 1e-3, (
                f"transition_matrix.{state} 行和 = {total}"
            )

    def test_confusion_column_sums(self, real_config):
        cm = real_config["confusion_matrix"]
        for state in CYCLE_STATES:
            col = cm[f"true_{state}"]
            total = sum(v for k, v in col.items() if k != "alpha")
            assert abs(total - 1.0) < 1e-3

    def test_behavior_row_sums(self, real_config):
        bm = real_config["behavior_mapping"]
        for cycle in CYCLE_STATES:
            for participant, row in bm[cycle].items():
                total = sum(v for k, v in row.items() if k != "alpha")
                assert abs(total - 1.0) < 1e-3, (
                    f"behavior_mapping.{cycle}.{participant} 行和 = {total}"
                )


# ---------------------------------------------------------------------------
# ConfigValidator — 错误场景
# ---------------------------------------------------------------------------

class TestValidatorErrors:

    def test_missing_top_level_key(self, valid_cfg):
        cfg = copy.deepcopy(valid_cfg)
        del cfg["transition_matrix"]
        with pytest.raises(ConfigError, match="transition_matrix"):
            validate(cfg)

    def test_unknown_top_level_key(self, valid_cfg):
        cfg = copy.deepcopy(valid_cfg)
        cfg["typo_key"] = {}
        with pytest.raises(ConfigError, match="typo_key"):
            validate(cfg)

    def test_transition_row_sum_not_one(self, valid_cfg):
        cfg = _mutate(valid_cfg, (["transition_matrix", "冰点", "启动"], 0.99))
        with pytest.raises(ConfigError, match="行和"):
            validate(cfg)

    def test_negative_probability(self, valid_cfg):
        cfg = _mutate(valid_cfg, (["transition_matrix", "冰点", "退潮"], -0.01))
        with pytest.raises(ConfigError, match="负数"):
            validate(cfg)

    @pytest.mark.parametrize(
        ("from_state", "to_state"),
        (("启动", "高潮"), ("高潮", "退潮")),
    )
    def test_fast_cycle_transitions_cannot_be_disabled(self, valid_cfg, from_state, to_state):
        cfg = copy.deepcopy(valid_cfg)
        row = cfg["transition_matrix"][from_state]
        removed_probability = row[to_state]
        row[to_state] = 0.0
        row[from_state] += removed_probability

        with pytest.raises(ConfigError, match="direct transition"):
            validate(cfg)

    def test_alpha_below_minimum(self, valid_cfg):
        cfg = _mutate(valid_cfg, (["transition_matrix", "冰点", "alpha"], 0.1))
        with pytest.raises(ConfigError, match="alpha"):
            validate(cfg)

    def test_missing_state_in_transition(self, valid_cfg):
        cfg = copy.deepcopy(valid_cfg)
        del cfg["transition_matrix"]["冰点"]["退潮"]
        with pytest.raises(ConfigError, match="缺少键"):
            validate(cfg)

    def test_wrong_confusion_llm_key(self, valid_cfg):
        cfg = copy.deepcopy(valid_cfg)
        col = cfg["confusion_matrix"]["true_冰点"]
        col["llm_错误状态"] = col.pop("llm_冰点")
        with pytest.raises(ConfigError):
            validate(cfg)

    def test_behavior_mapping_missing_participant(self, valid_cfg):
        cfg = copy.deepcopy(valid_cfg)
        del cfg["behavior_mapping"]["高潮"]["散户"]
        with pytest.raises(ConfigError, match="散户"):
            validate(cfg)

    def test_negative_policy_multiplier(self, valid_cfg):
        cfg = _mutate(
            valid_cfg,
            (["policy_multipliers", "主力", "政策暖风", "建仓"], -0.5),
        )
        with pytest.raises(ConfigError, match="负数"):
            validate(cfg)

    def test_retail_policy_multiplier_must_use_retail_vocabulary(self, valid_cfg):
        cfg = copy.deepcopy(valid_cfg)
        cfg["policy_multipliers"]["散户"]["政策暖风"]["建仓"] = 1.2
        with pytest.raises(ConfigError, match="建仓"):
            validate(cfg)

    def test_initial_belief_wrong_sum(self, valid_cfg):
        cfg = _mutate(valid_cfg, (["initial_belief", "冰点"], 0.5))
        with pytest.raises(ConfigError, match="initial_belief"):
            validate(cfg)

    def test_non_numeric_probability(self, valid_cfg):
        cfg = _mutate(
            valid_cfg,
            (["transition_matrix", "冰点", "启动"], "high"),
        )
        with pytest.raises(ConfigError, match="数字"):
            validate(cfg)


# ---------------------------------------------------------------------------
# HMMFilter — 解析与初始化
# ---------------------------------------------------------------------------

class TestHMMFilterInit:

    def test_loads_without_error(self, real_config):
        f = HMMFilter(real_config, "测试板块")
        assert f.sector == "测试板块"

    def test_initial_belief_normalized(self, real_config):
        f = HMMFilter(real_config)
        total = sum(f.belief.values())
        assert abs(total - 1.0) < 1e-9

    def test_initial_belief_has_all_states(self, real_config):
        f = HMMFilter(real_config)
        assert set(f.belief.keys()) == set(CYCLE_STATES)

    def test_config_version_stored(self, real_config):
        f = HMMFilter(real_config)
        assert f.config_version == real_config["version"]


# ---------------------------------------------------------------------------
# HMMFilter — 前向滤波更新
# ---------------------------------------------------------------------------

class TestHMMFilterUpdate:

    def test_belief_remains_normalized_after_update(self, real_config):
        f = HMMFilter(real_config)
        f.update("启动")
        total = sum(f.belief.values())
        assert abs(total - 1.0) < 1e-9

    def test_all_states_present_after_update(self, real_config):
        f = HMMFilter(real_config)
        f.update("高潮")
        assert set(f.belief.keys()) == set(CYCLE_STATES)

    def test_repeated_same_label_shifts_belief(self, real_config):
        """连续输入「高潮」后，高潮的后验概率应高于初始均匀分布。"""
        f = HMMFilter(real_config)
        for _ in range(5):
            f.update("高潮")
        assert f.belief["高潮"] > 0.20  # 初始均匀值

    def test_contradictory_labels_do_not_diverge(self, real_config):
        """交替输入「冰点」和「高潮」后，信念不应无穷大或为零。"""
        f = HMMFilter(real_config)
        for label in ["冰点", "高潮"] * 10:
            f.update(label)
        for v in f.belief.values():
            assert math.isfinite(v)
            assert v >= 0.0

    def test_unknown_label_raises_value_error(self, real_config):
        """未知标签应触发 ValueError（归一化时分母为零）。"""
        f = HMMFilter(real_config)
        with pytest.raises((ValueError, KeyError)):
            f.update("不存在的标签")

    def test_update_returns_new_belief(self, real_config):
        f = HMMFilter(real_config)
        returned = f.update("发酵")
        assert returned == f.belief


# ---------------------------------------------------------------------------
# HMMFilter — 行为概率预测
# ---------------------------------------------------------------------------

class TestHMMFilterBehaviorPrediction:

    def test_behavior_dist_normalized(self, real_config):
        f = HMMFilter(real_config)
        dist = f.predict_behaviors("主力")
        total = sum(dist.values())
        assert abs(total - 1.0) < 1e-9

    def test_behavior_dist_has_all_behaviors(self, real_config):
        f = HMMFilter(real_config)
        dist = f.predict_behaviors("主力")
        assert set(dist.keys()) == set(MAIN_FORCE_BEHAVIORS)

    def test_retail_behavior_distribution_uses_retail_vocabulary(self, real_config):
        f = HMMFilter(real_config)

        dist = f.predict_behaviors("散户")

        assert tuple(dist) == RETAIL_BEHAVIORS
        assert set(dist).isdisjoint({"震仓", "拉升", "出货", "狩猎止损"})

    def test_all_participants_produce_valid_dist(self, real_config):
        f = HMMFilter(real_config)
        f.update("启动")
        participants = ["主力", "散户"]
        for p in participants:
            dist = f.predict_behaviors(p)
            total = sum(dist.values())
            assert abs(total - 1.0) < 1e-9, f"{p} 行为分布和 = {total}"

    def test_policy_changes_distribution(self, real_config):
        """政策暖风与政策打压下，建仓概率应有可见差异。"""
        f = HMMFilter(real_config)
        dist_warm  = f.predict_behaviors("主力", policy="政策暖风")
        dist_crack = f.predict_behaviors("主力", policy="政策打压")
        assert dist_warm["建仓"] > dist_crack["建仓"]

    def test_retail_policy_uses_retail_behavior_multipliers(self, real_config):
        f = HMMFilter(real_config)
        warm = f.predict_behaviors("散户", policy="政策暖风")
        crackdown = f.predict_behaviors("散户", policy="政策打压")

        assert set(warm) == set(RETAIL_BEHAVIORS)
        assert warm["底部建仓"] > crackdown["底部建仓"]
        assert crackdown["恐慌割肉"] > warm["恐慌割肉"]

    def test_no_intervention_policy_same_as_all_ones(self, real_config):
        """政策无干预时行为概率应与不传 policy 参数一致。"""
        f = HMMFilter(real_config)
        dist_default = f.predict_behaviors("散户")
        dist_none    = f.predict_behaviors("散户", policy="无干预")
        for b in RETAIL_BEHAVIORS:
            assert abs(dist_default[b] - dist_none[b]) < 1e-9


# ---------------------------------------------------------------------------
# HMMFilter — 重置
# ---------------------------------------------------------------------------

class TestHMMFilterReset:

    def test_reset_returns_to_uniform(self, real_config):
        f = HMMFilter(real_config)
        for label in ["高潮", "高潮", "高潮"]:
            f.update(label)
        f.reset()
        for v in f.belief.values():
            assert abs(v - 1.0 / len(CYCLE_STATES)) < 1e-9


# ---------------------------------------------------------------------------
# 集成测试：完整滤波序列
# ---------------------------------------------------------------------------

class TestIntegration:

    def test_full_sequence_stays_valid(self, real_config):
        """
        模拟一个板块 20 根 K 线的完整流程：
        启动期 → 发酵期 → 高潮期 → 退潮期
        每步校验信念归一化，最终预测行为。
        """
        labels = (
            ["启动"] * 5
            + ["发酵"] * 5
            + ["高潮"] * 5
            + ["退潮"] * 5
        )
        f = HMMFilter(real_config, sector_name="集成测试板块")
        for label in labels:
            belief = f.update(label)
            assert abs(sum(belief.values()) - 1.0) < 1e-9

        # 经历退潮序列后退潮概率应最高
        dist = f.predict_behaviors("主力", policy="无干预")
        assert abs(sum(dist.values()) - 1.0) < 1e-9

    def test_config_version_survives_updates(self, real_config):
        """更新信念不应改变 config_version。"""
        f = HMMFilter(real_config)
        ver = f.config_version
        for label in CYCLE_STATES:
            f.update(label)
        assert f.config_version == ver
