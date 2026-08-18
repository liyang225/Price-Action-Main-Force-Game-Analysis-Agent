"""
HMM 前向滤波器 — SecondOrderGame
===================================
实现 ADR-0001：只做前向滤波，永不训练。

配置源：config/hmm_prior.yaml
术语定义：CONTEXT.md

核心公式
--------
前向信念更新（每根 K 线）：
    b_t(z) ∝ C[ℓ_t | z] × Σ_{z'} b_{t-1}(z') × A[z' → z]

T+1 行为概率预测：
    P(behavior) = Σ_z b_t(z) × W[z, participant, behavior]

政策修正（乘法，乘后归一化）：
    W_eff[z, p, b] = W[z, p, b] × policy_multipliers[p][policy][b]
    → 再对每个 (z, p) 行归一化
"""

from __future__ import annotations

import math
from pathlib import Path
from numbers import Real
from typing import Mapping

import yaml  # pip install pyyaml

# ---------------------------------------------------------------------------
# 类型别名
# ---------------------------------------------------------------------------
CycleState = str   # 冰点 | 启动 | 发酵 | 高潮 | 退潮
Participant = str  # 操纵型主力 | 配置型主力 | 羊群散户
Behavior = str     # 建仓 | 震仓 | 拉升 | 出货 | 观望 | 狩猎止损
PolicyEnv = str    # 无干预 | 政策暖风 | 国家队托底中 | 政策打压

Belief = dict[CycleState, float]          # 情绪周期信念分布（归一化）
BehaviorDist = dict[Behavior, float]      # 行为概率分布（归一化）

# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------
_DEFAULT_CONFIG = (
    Path(__file__).parent.parent / "config" / "hmm_prior.yaml"
)


def load_config(path: Path | str = _DEFAULT_CONFIG) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _normalize(d: dict[str, float]) -> dict[str, float]:
    """对字典值归一化，返回新字典。零向量抛出 ValueError。"""
    total = sum(d.values())
    if total <= 0:
        raise ValueError(f"无法归一化：向量和为 {total}，keys={list(d)}")
    return {k: v / total for k, v in d.items()}


def _row_probs(row: dict) -> dict[str, float]:
    """从 YAML 行中提取 probs 子字典（去掉 alpha 等元字段）。"""
    return {k: v for k, v in row.items() if k != "alpha"}


# ---------------------------------------------------------------------------
# HMMFilter
# ---------------------------------------------------------------------------

class HMMFilter:
    """
    单板块前向滤波器。

    每个板块独立实例化一个 HMMFilter；板块情绪台账由外层管理。

    Parameters
    ----------
    config : dict
        已加载的 hmm_prior.yaml 内容。
    sector_name : str
        板块名称，仅用于日志。
    """

    def __init__(self, config: dict, sector_name: str = "unknown"):
        self._cfg = config
        self.sector = sector_name
        self._version: int = config["version"]

        # 解析转移矩阵 A
        self._A: dict[CycleState, dict[CycleState, float]] = {
            state: _normalize(_row_probs(row))
            for state, row in config["transition_matrix"].items()
        }

        # 解析混淆矩阵 C：true_state → llm_label → prob（列归一化）
        # YAML 结构：confusion_matrix.true_X.llm_Y = p
        self._C: dict[CycleState, dict[str, float]] = {}
        for col_key, col in config["confusion_matrix"].items():
            true_state = col_key.removeprefix("true_")
            self._C[true_state] = _normalize(_row_probs(col))

        # 解析行为映射 W
        self._W: dict[CycleState, dict[Participant, dict[Behavior, float]]] = {}
        for cycle, participants in config["behavior_mapping"].items():
            self._W[cycle] = {}
            for participant, row in participants.items():
                if participant == "alpha":
                    continue
                self._W[cycle][participant] = _normalize(_row_probs(row))

        # 解析政策修正因子
        self._policy_mult: dict[Participant, dict[PolicyEnv, dict[Behavior, float]]] = {
            participant: {env: dict(row) for env, row in policies.items()}
            for participant, policies in config["policy_multipliers"].items()
        }

        # 初始信念
        self._belief: Belief = _normalize(
            dict(config["initial_belief"])
        )

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    @property
    def belief(self) -> Belief:
        """当前信念分布（只读副本）。"""
        return dict(self._belief)

    @property
    def config_version(self) -> int:
        return self._version

    def restore_belief(self, belief: Mapping[CycleState, float]) -> Belief:
        """Replace the current belief with a validated persisted checkpoint.

        Restoring is deliberately explicit: the caller owns compatibility of
        the checkpoint's configuration version with this filter instance.
        """
        states = set(self._A)
        if set(belief) != states:
            raise ValueError("persisted belief states do not match the HMM configuration")

        restored: Belief = {}
        for state, value in belief.items():
            if isinstance(value, bool) or not isinstance(value, Real):
                raise ValueError(f"persisted belief for {state!r} is not numeric")
            numeric = float(value)
            if not math.isfinite(numeric) or numeric < 0:
                raise ValueError(f"persisted belief for {state!r} is invalid")
            restored[state] = numeric

        total = sum(restored.values())
        if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError(f"persisted belief must sum to 1.0, got {total}")

        self._belief = restored
        return dict(self._belief)

    def update(self, llm_label: str) -> Belief:
        """
        根据大模型输出的情绪周期位置标签更新信念。

        Parameters
        ----------
        llm_label : str
            大模型本 K 线输出的情绪周期标签（须在 YAML 定义范围内）。
            格式应与混淆矩阵列键一致，例如「启动」而非「llm_启动」。

        Returns
        -------
        Belief
            更新后的信念分布。
        """
        llm_key = f"llm_{llm_label}"
        states = list(self._A.keys())

        # 预测步：b_pred(z) = Σ_{z'} b_{t-1}(z') × A[z' → z]
        b_pred: dict[str, float] = {z: 0.0 for z in states}
        for z_prev, p_prev in self._belief.items():
            for z_next, p_trans in self._A[z_prev].items():
                b_pred[z_next] += p_prev * p_trans

        # 更新步：b_t(z) ∝ C[llm_label | z] × b_pred(z)
        b_updated: dict[str, float] = {}
        for z in states:
            # C 列 = true_state；C 行 = llm_said
            p_obs = self._C[z].get(llm_key, 0.0)
            b_updated[z] = p_obs * b_pred[z]

        self._belief = _normalize(b_updated)
        return dict(self._belief)

    def predict_behaviors(
        self,
        participant: Participant,
        policy: PolicyEnv = "无干预",
    ) -> BehaviorDist:
        """
        预测当前信念下某类主力的行为概率分布。

        P(behavior) = Σ_z b_t(z) × W_eff[z, participant, behavior]

        Parameters
        ----------
        participant : Participant
            参与者类型（操纵型主力 / 配置型主力 / 羊群散户）。
        policy : PolicyEnv
            当前政策环境，用于修正 W。

        Returns
        -------
        BehaviorDist
            归一化的行为概率分布。
        """
        mult = self._policy_mult.get(participant, {}).get(policy, {})
        behaviors: dict[str, float] = {}

        for z, p_belief in self._belief.items():
            w_row = self._W[z].get(participant, {})

            # 政策修正：逐行乘以因子，之后归一化
            w_eff = {b: w * mult.get(b, 1.0) for b, w in w_row.items()}
            w_eff = _normalize(w_eff)

            for behavior, p_w in w_eff.items():
                behaviors[behavior] = behaviors.get(behavior, 0.0) + p_belief * p_w

        return _normalize(behaviors)

    def reset(self) -> None:
        """重置信念到初始均匀分布（用于新板块或新交易日）。"""
        states = list(self._A.keys())
        n = len(states)
        self._belief = {z: 1.0 / n for z in states}

    # ------------------------------------------------------------------
    # 辅助：打印当前信念摘要
    # ------------------------------------------------------------------

    def belief_summary(self) -> str:
        lines = [f"[{self.sector}] 情绪信念 (cfg v{self._version})"]
        for state, prob in sorted(self._belief.items(),
                                  key=lambda x: -x[1]):
            bar = "█" * int(prob * 20)
            lines.append(f"  {state:4s} {prob:.3f} {bar}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 快速验证（python -m src.hmm_filter）
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cfg = load_config()
    f = HMMFilter(cfg, sector_name="半导体")

    print("=== 初始信念 ===")
    print(f.belief_summary())

    # 模拟三根 K 线：大模型连续判断为「启动」
    for i, label in enumerate(["启动", "启动", "发酵"], 1):
        f.update(label)
        print(f"\n=== K 线 {i}，大模型输出：{label} ===")
        print(f.belief_summary())

    print("\n=== 操纵型主力行为预测（政策暖风）===")
    dist = f.predict_behaviors("操纵型主力", policy="政策暖风")
    for behavior, prob in sorted(dist.items(), key=lambda x: -x[1]):
        bar = "▓" * int(prob * 30)
        print(f"  {behavior:5s} {prob:.3f} {bar}")
