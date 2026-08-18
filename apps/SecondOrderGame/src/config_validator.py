"""
配置校验器 — SecondOrderGame
==============================
校验 config/ 下各配置文件的结构完整性与数值合法性。

  validate / validate_file            hmm_prior.yaml（A / C / W 三表）
  validate_signals / validate_signals_file    signals.yaml（ADR-0016）
  validate_labeler / validate_labeler_file    labeler.yaml（ADR-0017）
  validate_sector_labeler / validate_sector_labeler_file  sector_labeler.yaml（ADR-0021）
  validate_all                        一次校验全部

hmm_prior.yaml 校验内容：
  1. 顶层键存在（version / transition_matrix / confusion_matrix /
     behavior_mapping / policy_multipliers / initial_belief）
  2. 状态空间一致：A / C / W / initial_belief 使用同一套状态名
  3. 每个概率行：所有值非负，行和在 [0.999, 1.001]
  4. alpha 存在且 ≥ 0.3
  5. confusion_matrix 列键格式为 llm_<state>，与状态空间对应
  6. behavior_mapping 参与者键完整，行为键跨所有行一致
  7. policy_multipliers 按参与者使用各自行为词表，值非负
  8. initial_belief 包含所有状态，和为 1

设计原则（ADR-0001）：
  永不返回带警告的半合法数字；校验失败直接抛出 ConfigError，
  包含足够诊断信息，不需要调试器。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# 域常量
# ---------------------------------------------------------------------------

POLICY_ENVS   = ["无干预", "政策暖风", "国家队托底中", "政策打压"]

# 从 labeler_constants 导入共享枚举，避免分叉（ADR-0018）
import sys
sys.path.insert(0, str(Path(__file__).parent))
from labeler_constants import BEHAVIORS, CYCLE_STATES, PARTICIPANTS, behaviors_for

_ALPHA_MIN    = 0.3
_PROB_TOL     = 1e-3   # 行和允许偏差

# signals.yaml
_MA_TYPES             = {"sma", "ema"}
_ZERO_RANGE_POLICIES  = {"insufficient_data", "epsilon", "carry_forward"}
# deviation 是标准差倍数。低于 0.1 基本只能是把「2%」误当成 0.02 填进来，
# 那会让带宽退化到 ±0.03% 量级。见 ADR-0016 二。
_NASH_DEVIATION_MIN   = 0.1

# labeler.yaml
_FORWARD_FEATURES = {"forward_excess_return", "forward_absolute_return"}
_SECTOR_FORWARD_FEATURE = "forward_absolute_return"
_INDEX_SOURCE = "futu_industry_weighted"

_SECTOR_THRESHOLD_KEYS = {
    CYCLE_STATES[0]: {
        "price_position_20_max", "consecutive_shrink_days_min",
        "recent_trend_5d_max", "forward_min", "volume_ratio_max",
    },
    CYCLE_STATES[1]: {
        "return_1d_min", "forward_min", "volume_ratio_min",
        "volume_ratio_max", "price_position_20_min",
        "consecutive_down_days_max", "recent_trend_5d_max",
    },
    CYCLE_STATES[2]: {
        "return_1d_min", "forward_min", "volume_ratio_min",
        "volume_ratio_max", "price_position_20_min",
        "consecutive_down_days_max", "recent_trend_5d_min",
    },
    CYCLE_STATES[3]: {
        "return_1d_min", "volume_ratio_min", "price_position_20_min",
        "forward_max", "recent_trend_5d_min",
    },
    CYCLE_STATES[4]: {
        "return_1d_max", "forward_max", "volume_ratio_min",
        "price_position_20_max", "consecutive_down_days_min",
    },
}


# ---------------------------------------------------------------------------
# 错误类型
# ---------------------------------------------------------------------------

class ConfigError(ValueError):
    """配置文件结构或数值错误。"""


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _config_dir() -> Path:
    """项目的 config/ 目录。"""
    return Path(__file__).parent.parent / "config"


def _load(path: Path | str) -> dict:
    """加载 YAML 并确认顶层是映射。"""
    source = Path(path)
    try:
        with open(source, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as error:
        raise ConfigError(f"[{source.resolve()}] cannot load YAML: {error}") from error
    if not isinstance(cfg, dict):
        raise ConfigError(
            f"[{Path(path).name}] 顶层结构应为映射，实际为 {type(cfg).__name__}"
        )
    return cfg


def _check_version(section: str, version: Any) -> None:
    """
    version 必须是正整数。

    改任何值之前必须递增 version，且分析记录必须存下所用 version —— 否则
    无法判断某条历史结论由哪版参数产生。
    """
    if not isinstance(version, int) or isinstance(version, bool):
        raise ConfigError(
            f"[{section}.version] 必须是整数，实际 {version!r}；"
            "改任何值之前必须递增 version"
        )
    if version < 1:
        raise ConfigError(f"[{section}.version] 必须 ≥ 1，实际 {version}")


def _check_keys(section: str, actual: set[str], expected: set[str]) -> None:
    missing = expected - actual
    extra   = actual   - expected
    if missing:
        raise ConfigError(
            f"[{section}] 缺少键：{sorted(missing)}"
        )
    if extra:
        raise ConfigError(
            f"[{section}] 存在未知键（可能是拼写错误）：{sorted(extra)}"
        )


def _check_prob_row(path: str, row: dict[str, Any], expected_keys: list[str]) -> None:
    """校验一行概率：键完整、值非负、行和为 1。"""
    actual_keys = {k for k in row if k != "alpha"}
    _check_keys(path, actual_keys, set(expected_keys))

    for key in expected_keys:
        v = row[key]
        if not isinstance(v, (int, float)):
            raise ConfigError(
                f"[{path}] 键 '{key}' 的值不是数字：{v!r}"
            )
        if v < 0:
            raise ConfigError(
                f"[{path}] 键 '{key}' 的概率为负数：{v}"
            )

    total = sum(float(row[k]) for k in expected_keys)
    if abs(total - 1.0) > _PROB_TOL:
        raise ConfigError(
            f"[{path}] 行和应为 1.0，实际为 {total:.6f}（偏差超过 {_PROB_TOL}）"
        )


def _check_alpha(path: str, row: dict[str, Any]) -> None:
    if "alpha" not in row:
        raise ConfigError(f"[{path}] 缺少 alpha 字段")
    a = row["alpha"]
    if not isinstance(a, (int, float)):
        raise ConfigError(f"[{path}] alpha 不是数字：{a!r}")
    if float(a) < _ALPHA_MIN:
        raise ConfigError(
            f"[{path}] alpha={a} 低于最小值 {_ALPHA_MIN}；"
            "过小的 alpha 会让单次异常彻底覆盖先验"
        )


def _check_mapping(path: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(
            f"[{path}] must be a mapping, got {type(value).__name__}"
        )
    return value


def _check_number(path: str, value: Any) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ConfigError(f"[{path}] must be a number, got {value!r}")
    return float(value)


def _check_positive_int(path: str, value: Any) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ConfigError(f"[{path}] must be an integer greater than or equal to 1, got {value!r}")


# ---------------------------------------------------------------------------
# 主校验函数
# ---------------------------------------------------------------------------

def validate(cfg: dict) -> None:
    """
    校验已加载的配置字典。校验通过静默返回，失败抛出 ConfigError。

    Parameters
    ----------
    cfg : dict
        yaml.safe_load() 返回的字典。
    """

    # ── 0. 顶层键 ──────────────────────────────────────────────────────────
    required_top = {
        "version",
        "transition_matrix",
        "confusion_matrix",
        "behavior_mapping",
        "policy_multipliers",
        "initial_belief",
    }
    _check_keys("root", set(cfg.keys()), required_top)

    # ── 1. transition_matrix ───────────────────────────────────────────────
    tm = cfg["transition_matrix"]
    _check_keys("transition_matrix", set(tm.keys()), set(CYCLE_STATES))

    for state in CYCLE_STATES:
        path = f"transition_matrix.{state}"
        row  = tm[state]
        _check_alpha(path, row)
        _check_prob_row(path, row, CYCLE_STATES)

    # ADR-0022: a strong opening can reach climax quickly, and high-level
    # distribution can reverse directly into decline. Neither path is optional.
    for from_state, to_state in (("启动", "高潮"), ("高潮", "退潮")):
        if float(tm[from_state][to_state]) <= 0:
            raise ConfigError(
                f"[transition_matrix.{from_state}.{to_state}] must stay positive; "
                "the sector cycle permits this direct transition"
            )

    # ── 2. confusion_matrix ────────────────────────────────────────────────
    cm = cfg["confusion_matrix"]
    expected_col_keys = {f"true_{s}" for s in CYCLE_STATES}
    _check_keys("confusion_matrix", set(cm.keys()), expected_col_keys)

    expected_llm_keys = [f"llm_{s}" for s in CYCLE_STATES]
    for true_state in CYCLE_STATES:
        path = f"confusion_matrix.true_{true_state}"
        col  = cm[f"true_{true_state}"]
        _check_alpha(path, col)
        _check_prob_row(path, col, expected_llm_keys)

    # ── 3. behavior_mapping ────────────────────────────────────────────────
    bm = cfg["behavior_mapping"]
    _check_keys("behavior_mapping", set(bm.keys()), set(CYCLE_STATES))

    for cycle in CYCLE_STATES:
        cycle_block = bm[cycle]
        _check_keys(
            f"behavior_mapping.{cycle}",
            set(cycle_block.keys()),
            set(PARTICIPANTS),
        )
        for participant in PARTICIPANTS:
            path = f"behavior_mapping.{cycle}.{participant}"
            row  = cycle_block[participant]
            _check_alpha(path, row)
            _check_prob_row(path, row, behaviors_for(participant))

    # ── 4. policy_multipliers ──────────────────────────────────────────────
    pm = cfg["policy_multipliers"]
    _check_keys("policy_multipliers", set(pm.keys()), set(PARTICIPANTS))

    for participant in PARTICIPANTS:
        participant_path = f"policy_multipliers.{participant}"
        policies = pm[participant]
        _check_keys(participant_path, set(policies.keys()), set(POLICY_ENVS))
        for env in POLICY_ENVS:
            path = f"{participant_path}.{env}"
            row = policies[env]
            vocabulary = behaviors_for(participant)
            _check_keys(path, set(row.keys()), set(vocabulary))
            for behavior in vocabulary:
                value = row[behavior]
                if not isinstance(value, (int, float)):
                    raise ConfigError(f"[{path}] '{behavior}' 的乘数不是数字：{value!r}")
                if float(value) < 0:
                    raise ConfigError(f"[{path}] '{behavior}' 的乘数为负数：{value}")

    # ── 5. initial_belief ──────────────────────────────────────────────────
    ib = cfg["initial_belief"]
    _check_keys("initial_belief", set(ib.keys()), set(CYCLE_STATES))

    total = sum(float(ib[s]) for s in CYCLE_STATES)
    if abs(total - 1.0) > _PROB_TOL:
        raise ConfigError(
            f"[initial_belief] 所有状态概率之和应为 1.0，实际为 {total:.6f}"
        )
    for s in CYCLE_STATES:
        if float(ib[s]) < 0:
            raise ConfigError(f"[initial_belief] 状态 '{s}' 的概率为负数：{ib[s]}")


def validate_file(path: Path | str = None) -> None:
    """
    从磁盘加载并校验配置文件。

    Parameters
    ----------
    path : Path | str, optional
        配置文件路径。默认为 config/hmm_prior.yaml（相对于项目根目录）。
    """
    if path is None:
        path = _config_dir() / "hmm_prior.yaml"
    validate(_load(path))


# ---------------------------------------------------------------------------
# signals.yaml 校验（ADR-0016）
# ---------------------------------------------------------------------------

def validate_signals(cfg: dict) -> None:
    """
    校验 signals.yaml。校验通过静默返回，失败抛出 ConfigError。

    重点不是"值是否正确"（那要靠回测），而是"值是否会让信号静默失效"。
    三类静默失效各有专门检查：
      - nash.deviation 被填成 0.02（带宽退化，见 ADR-0016 二）
      - 周期参数未按 K_120M 换算（语义漂移，见 ADR-0016 一）
      - 心理价位档位不覆盖实际价格区间（高价股永不触发，见 ADR-0016 三）
    """
    required_top = {
        "version",
        "period_semantics",
        "herd",
        "liquidity_trap",
        "smart_money",
        "institutional",
        "nash",
        "degenerate_bar",
        "usage",
        "composite_features",
        "not_applicable",
    }
    _check_keys("signals.root", set(cfg.keys()), required_top)
    _check_version("signals", cfg["version"])

    # ── period_semantics ───────────────────────────────────────────────────
    ps = cfg["period_semantics"]
    _check_keys(
        "signals.period_semantics",
        set(ps.keys()),
        {"source_unit", "target_unit", "bars_per_day", "scale_factor"},
    )
    if ps["bars_per_day"] != ps["scale_factor"]:
        raise ConfigError(
            f"[signals.period_semantics] bars_per_day={ps['bars_per_day']} 与 "
            f"scale_factor={ps['scale_factor']} 不一致；"
            "周期换算倍数必须等于每日 K 线根数，否则周期语义漂移"
        )
    if int(ps["bars_per_day"]) < 1:
        raise ConfigError(
            f"[signals.period_semantics] bars_per_day 必须 ≥ 1，实际 {ps['bars_per_day']}"
        )

    # ── 周期类参数：必须为正整数，且应是 scale_factor 的倍数 ──────────────
    scale = int(ps["scale_factor"])
    period_params = [
        ("signals.herd.rsi_length",              cfg["herd"]["rsi_length"]),
        ("signals.herd.volume_ma_length",        cfg["herd"]["volume_ma_length"]),
        ("signals.herd.momentum_lookback",       cfg["herd"]["momentum_lookback"]),
        ("signals.herd.momentum_ma_length",      cfg["herd"]["momentum_ma_length"]),
        ("signals.liquidity_trap.lookback",      cfg["liquidity_trap"]["lookback"]),
        ("signals.smart_money.ma_length",        cfg["smart_money"]["ma_length"]),
        ("signals.institutional.ad_ma_length",   cfg["institutional"]["ad_ma_length"]),
        ("signals.nash.period",                  cfg["nash"]["period"]),
    ]
    for path, value in period_params:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ConfigError(f"[{path}] 周期必须是整数 K 线根数，实际 {value!r}")
        if value < 1:
            raise ConfigError(f"[{path}] 周期必须 ≥ 1，实际 {value}")
        if scale > 1 and value % scale != 0:
            raise ConfigError(
                f"[{path}] 周期 {value} 不是 scale_factor={scale} 的倍数；"
                f"文档周期单位是交易日，本系统是 K_120M，所有周期须 ×{scale} 换算"
                "（若确实要用非整日周期，请在 ADR 中说明并放宽本检查）"
            )

    # ── nash.deviation：防退化 ─────────────────────────────────────────────
    nash = cfg["nash"]
    _check_keys("signals.nash", set(nash.keys()), {"period", "ma_type", "deviation"})
    dev = nash["deviation"]
    if not isinstance(dev, (int, float)) or isinstance(dev, bool):
        raise ConfigError(f"[signals.nash.deviation] 不是数字：{dev!r}")
    if float(dev) <= 0:
        raise ConfigError(f"[signals.nash.deviation] 必须为正，实际 {dev}")
    if float(dev) < _NASH_DEVIATION_MIN:
        raise ConfigError(
            f"[signals.nash.deviation] {dev} 低于 {_NASH_DEVIATION_MIN}，带宽会退化。"
            "deviation 是标准差倍数，不是百分比 —— 参考文档正文的「2% 偏差率」"
            "指的是带宽观测宽度，不是本变量的赋值。取 0.02 会让带宽变成 ±0.03%，"
            "价格几乎永远在带外，动量与回归信号每根 K 线都触发。见 ADR-0016 二"
        )
    if nash["ma_type"] not in _MA_TYPES:
        raise ConfigError(
            f"[signals.nash.ma_type] 未知均线类型 '{nash['ma_type']}'，"
            f"可选：{sorted(_MA_TYPES)}"
        )

    # ── 阈值类参数：不随周期换算，各有合法区间 ────────────────────────────
    herd = cfg["herd"]
    ob, os_ = herd["rsi_overbought"], herd["rsi_oversold"]
    for path, v in (("rsi_overbought", ob), ("rsi_oversold", os_)):
        if not 0 <= float(v) <= 100:
            raise ConfigError(f"[signals.herd.{path}] RSI 阈值须在 0~100，实际 {v}")
    if float(os_) >= float(ob):
        raise ConfigError(
            f"[signals.herd] rsi_oversold={os_} 必须小于 rsi_overbought={ob}，"
            "否则超买超卖区间重叠，羊群追涨与杀跌会同时成立"
        )

    for path, v in (
        ("signals.herd.volume_multiple",           herd["volume_multiple"]),
        ("signals.liquidity_trap.volume_multiple", cfg["liquidity_trap"]["volume_multiple"]),
        ("signals.institutional.volume_multiple",  cfg["institutional"]["volume_multiple"]),
    ):
        if float(v) < 1.0:
            raise ConfigError(
                f"[{path}] 成交量倍数 {v} 小于 1.0；"
                "小于 1 意味着「缩量即异常放量」，判定方向反了"
            )

    prox = cfg["liquidity_trap"]["psych_proximity"]
    if not 0 < float(prox) < 1:
        raise ConfigError(
            f"[signals.liquidity_trap.psych_proximity] 须在 (0, 1) 开区间，实际 {prox}；"
            "该值是相对 close 的比例，1% 应写 0.01 而不是 1"
        )

    zt = cfg["smart_money"].get("positive_z_threshold")
    if zt is not None and (
        isinstance(zt, bool) or not isinstance(zt, (int, float)) or float(zt) < 0
    ):
        raise ConfigError(
            f"[signals.smart_money.positive_z_threshold] 须为非负数或 null，实际 {zt!r}；"
            "null 沿用方向判定 value > MA，非负数是 z-score 超额阈值（ADR-0025）"
        )

    # ── 心理价位档位 ───────────────────────────────────────────────────────
    brackets = cfg["liquidity_trap"]["psych_level_brackets"]
    if not isinstance(brackets, list) or not brackets:
        raise ConfigError(
            "[signals.liquidity_trap.psych_level_brackets] 必须是非空列表"
        )
    prev_below = 0.0
    for i, br in enumerate(brackets):
        path = f"signals.liquidity_trap.psych_level_brackets[{i}]"
        _check_keys(path, set(br.keys()), {"below", "step"})
        if float(br["step"]) <= 0:
            raise ConfigError(f"[{path}] step 必须为正，实际 {br['step']}")
        below = br["below"]
        is_last = i == len(brackets) - 1
        if below is None:
            if not is_last:
                raise ConfigError(
                    f"[{path}] below: null 表示无上界，只能出现在最后一档"
                )
        else:
            if float(below) <= prev_below:
                raise ConfigError(
                    f"[{path}] below={below} 未大于前一档的 {prev_below}；"
                    "档位必须按价格升序且不重叠"
                )
            prev_below = float(below)
    if brackets[-1].get("below") is not None:
        raise ConfigError(
            "[signals.liquidity_trap.psych_level_brackets] 最后一档必须是 below: null"
            "（无上界）；否则高于最高档的价格找不到整数位，"
            "liquidity_trap 对高价股会静默失效。见 ADR-0016 三"
        )

    # ── degenerate_bar ─────────────────────────────────────────────────────
    db = cfg["degenerate_bar"]
    _check_keys(
        "signals.degenerate_bar",
        set(db.keys()),
        {"zero_range_policy", "affected_signals",
         "epsilon_fallback", "carry_forward_previous"},
    )
    if db["zero_range_policy"] not in _ZERO_RANGE_POLICIES:
        raise ConfigError(
            f"[signals.degenerate_bar.zero_range_policy] 未知策略 "
            f"'{db['zero_range_policy']}'，可选：{sorted(_ZERO_RANGE_POLICIES)}"
        )
    if db["zero_range_policy"] == "insufficient_data":
        if db["epsilon_fallback"] or db["carry_forward_previous"]:
            raise ConfigError(
                "[signals.degenerate_bar] zero_range_policy=insufficient_data 时"
                "不能同时开启 epsilon_fallback 或 carry_forward_previous；"
                "兜底会产出伪值，与「返回数据不足」的铁律冲突"
            )
    if not db["affected_signals"]:
        raise ConfigError(
            "[signals.degenerate_bar.affected_signals] 不能为空；"
            "至少 smart_money 与 institutional_ad 的分母含 (high - low)"
        )

    # ── usage ──────────────────────────────────────────────────────────────
    usage = cfg["usage"]
    _check_keys(
        "signals.usage",
        set(usage.keys()),
        {"role", "emit_composite_signal", "emit_position_size"},
    )
    if usage["role"] != "observation_feature":
        raise ConfigError(
            f"[signals.usage.role] 必须是 'observation_feature'，实际 "
            f"'{usage['role']}'。本项目的链路是「信号 → 喂给大模型」，"
            "不是「信号 → 下单」。见 ADR-0016 六"
        )
    if usage["emit_position_size"]:
        raise ConfigError(
            "[signals.usage.emit_position_size] 必须为 false；"
            "仓位归 PA_Agent，二阶博弈只输出布尔闸门与推演文本（ADR-0015）。"
            "开启会出现两个仓位决策权"
        )


def validate_signals_file(path: Path | str = None) -> None:
    """从磁盘加载并校验 signals.yaml。"""
    if path is None:
        path = _config_dir() / "signals.yaml"
    validate_signals(_load(path))


# ---------------------------------------------------------------------------
# labeler.yaml 校验（ADR-0017）
# ---------------------------------------------------------------------------

def validate_labeler(cfg: dict) -> None:
    """
    校验 labeler.yaml。校验通过静默返回，失败抛出 ConfigError。

    这个文件的每一处校验都对应一个会污染 W 计数的错误：
      - fallback_to_watch 被打开（观望列解释力归零，ADR-0017 五）
      - 标签基数不是 1 或允许小数计数（破坏 Dirichlet 语义，ADR-0017 四）
      - priority 缺标签（多命中时消解结果不确定）
      - 震仓/狩猎止损的支撑与成交量方向被改成同向（两者不再互斥）
      - 个股标注器被允许读板块标签（跨层泄漏，ADR-0017 二）
    """
    required_top = {
        "version",
        "forward_return",
        "missing_benchmark_policy",
        "independence",
        "features",
        "lookback",
        "zero_range_bar",
        "suspension_policy",
        "thresholds",
        "cardinality",
        "priority",
        "unlabeled",
        "coverage_monitor",
        "rule_hash",
    }
    _check_keys("labeler.root", set(cfg.keys()), required_top)
    _check_version("labeler", cfg["version"])

    # ── forward_return ─────────────────────────────────────────────────────
    fr = cfg["forward_return"]
    _check_keys(
        "labeler.forward_return",
        set(fr.keys()),
        {"feature", "benchmark", "window_bars", "per_behavior_window"},
    )
    if fr["feature"] not in _FORWARD_FEATURES:
        raise ConfigError(
            f"[labeler.forward_return.feature] 未知口径 '{fr['feature']}'，"
            f"可选：{sorted(_FORWARD_FEATURES)}"
        )
    if not isinstance(fr["window_bars"], int) or fr["window_bars"] < 1:
        raise ConfigError(
            f"[labeler.forward_return.window_bars] 必须是 ≥1 的整数，"
            f"实际 {fr['window_bars']!r}"
        )
    if fr["per_behavior_window"]:
        raise ConfigError(
            "[labeler.forward_return.per_behavior_window] 必须为 false；"
            "没有独立人工意图真值时，按行为区分窗口无法验证，"
            "只会把同一数据上的调参误当成验证。见 ADR-0017 三"
        )

    if cfg["missing_benchmark_policy"] != "no_label":
        raise ConfigError(
            f"[labeler.missing_benchmark_policy] 必须是 'no_label'，实际 "
            f"'{cfg['missing_benchmark_policy']}'。替换基准会让标签含义静默漂移"
        )
    if cfg["suspension_policy"] != "no_label":
        raise ConfigError(
            f"[labeler.suspension_policy] 必须是 'no_label'，实际 "
            f"'{cfg['suspension_policy']}'。延长窗口会让「五日」"
            "在不同样本上代表不同的真实时间跨度"
        )

    # ── independence ───────────────────────────────────────────────────────
    ind = cfg["independence"]
    _check_keys(
        "labeler.independence",
        set(ind.keys()),
        {"may_read_sector_ohlcv", "may_read_sector_labels",
         "may_read_sector_sentiment"},
    )
    for key in ("may_read_sector_labels", "may_read_sector_sentiment"):
        if ind[key]:
            raise ConfigError(
                f"[labeler.independence.{key}] 必须为 false；"
                "个股标注器读板块标签或情绪会造成跨层标签泄漏与自我强化。"
                "读原始板块 OHLCV 是允许的（may_read_sector_ohlcv）。见 ADR-0017 二"
            )

    # ── thresholds ─────────────────────────────────────────────────────────
    th = cfg["thresholds"]
    _check_keys("labeler.thresholds", set(th.keys()), set(BEHAVIORS))
    for behavior in BEHAVIORS:
        block = th[behavior]
        if not block:
            raise ConfigError(f"[labeler.thresholds.{behavior}] 阈值块为空")
        for key, v in block.items():
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                raise ConfigError(
                    f"[labeler.thresholds.{behavior}.{key}] 不是数字：{v!r}"
                )
        for lo_key, hi_key in (
            ("return_1d_min", "return_1d_max"),
        ):
            if lo_key in block and hi_key in block:
                if float(block[lo_key]) > float(block[hi_key]):
                    raise ConfigError(
                        f"[labeler.thresholds.{behavior}] {lo_key}="
                        f"{block[lo_key]} 大于 {hi_key}={block[hi_key]}，区间为空"
                    )

    _check_shakeout_vs_stophunt(th)

    # ── cardinality ────────────────────────────────────────────────────────
    card = cfg["cardinality"]
    _check_keys(
        "labeler.cardinality",
        set(card.keys()),
        {"labels_per_stock_day", "allow_fractional_counts"},
    )
    if card["labels_per_stock_day"] != 1:
        raise ConfigError(
            f"[labeler.cardinality.labels_per_stock_day] 必须为 1，实际 "
            f"{card['labels_per_stock_day']}。同一天进入两次会把同一份价格信息"
            "复制成两个样本，破坏 Dirichlet 的「每交易日一次观测」语义"
        )
    if card["allow_fractional_counts"]:
        raise ConfigError(
            "[labeler.cardinality.allow_fractional_counts] 必须为 false；"
            "把 1 天拆成 0.6/0.4 会引入非整数伪计数。这是数学约束，不是折中。"
            "见 ADR-0017 四"
        )

    # ── priority ───────────────────────────────────────────────────────────
    priority = cfg["priority"]
    if not isinstance(priority, list):
        raise ConfigError("[labeler.priority] 必须是列表")
    if len(priority) != len(set(priority)):
        dupes = sorted({x for x in priority if priority.count(x) > 1})
        raise ConfigError(f"[labeler.priority] 存在重复标签：{dupes}")
    _check_keys("labeler.priority", set(priority), set(BEHAVIORS))
    if priority[-1] != "观望":
        raise ConfigError(
            f"[labeler.priority] 最后一位必须是「观望」，实际 '{priority[-1]}'；"
            "观望是最广义的安静状态，不能覆盖任何更特异的活动信号"
        )

    # ── unlabeled ──────────────────────────────────────────────────────────
    unl = cfg["unlabeled"]
    _check_keys(
        "labeler.unlabeled",
        set(unl.keys()),
        {"fallback_to_watch", "update_w_counts", "persist_row_features"},
    )
    if unl["fallback_to_watch"]:
        raise ConfigError(
            "[labeler.unlabeled.fallback_to_watch] 必须为 false；"
            "实测兜底会让观望从 19,401 行涨到 88,459 行（4.56 倍），"
            "把「主力主动不动」和「规则不知道」混成一列。见 ADR-0017 五"
        )
    if unl["update_w_counts"]:
        raise ConfigError(
            "[labeler.unlabeled.update_w_counts] 必须为 false；"
            "无标签行没有标签可计数"
        )
    if not unl["persist_row_features"]:
        raise ConfigError(
            "[labeler.unlabeled.persist_row_features] 必须为 true；"
            "不存行级特征则新版本规则无法回溯重标，历史样本永久丢失"
        )

    # ── coverage_monitor ───────────────────────────────────────────────────
    cov = cfg["coverage_monitor"]
    _check_keys(
        "labeler.coverage_monitor",
        set(cov.keys()),
        {"window_trading_days", "pool", "alert_threshold",
         "consecutive_windows_to_escalate", "derived_from_pool_size",
         "on_alert"},
    )
    if cov["pool"] != "per_sector":
        raise ConfigError(
            f"[labeler.coverage_monitor.pool] 必须是 'per_sector'，实际 "
            f"'{cov['pool']}'。实测板块覆盖率在 19.64%~39.07% 之间（约两倍差异），"
            "只看全局均值会掩盖单板块退化"
        )
    if not 0 < float(cov["alert_threshold"]) < 1:
        raise ConfigError(
            f"[labeler.coverage_monitor.alert_threshold] 须在 (0,1) 开区间，"
            f"实际 {cov['alert_threshold']}"
        )
    for key in ("window_trading_days", "consecutive_windows_to_escalate",
                "derived_from_pool_size"):
        v = cov[key]
        if not isinstance(v, int) or isinstance(v, bool) or v < 1:
            raise ConfigError(
                f"[labeler.coverage_monitor.{key}] 必须是 ≥1 的整数，实际 {v!r}"
            )

    # ── rule_hash ──────────────────────────────────────────────────────────
    rh = cfg["rule_hash"]
    _check_keys(
        "labeler.rule_hash",
        set(rh.keys()),
        {"algorithm", "scope", "store_with_every_label"},
    )
    if not rh["store_with_every_label"]:
        raise ConfigError(
            "[labeler.rule_hash.store_with_every_label] 必须为 true；"
            "不存哈希则无法判断某条历史标签由哪版规则产生，审计链断裂"
        )


def _check_shakeout_vs_stophunt(th: dict) -> None:
    """
    震仓与狩猎止损必须在支撑边界和成交量方向上保持互斥。

    这是六条规则里唯一一处「两个标签靠方向相反的同名条件区分」，也是改阈值时
    最容易无声破坏的地方。破坏后两者会大量共同命中，而优先级会把全部交叉
    样本判给狩猎止损，震仓的计数被悄悄吃掉。
    """
    shake = th["震仓"]
    hunt  = th["狩猎止损"]

    s_break = shake.get("support_break_min")
    h_break = hunt.get("support_break_max")
    if s_break is None or h_break is None:
        raise ConfigError(
            "[labeler.thresholds] 震仓需要 support_break_min、"
            "狩猎止损需要 support_break_max，用于在支撑边界上区分两者"
        )
    if float(s_break) < float(h_break):
        raise ConfigError(
            f"[labeler.thresholds] 支撑边界重叠：震仓 support_break_min="
            f"{s_break}（要求 > 该值，未有效跌破），狩猎止损 support_break_max="
            f"{h_break}（要求 ≤ 该值，有效跌破）。前者必须 ≥ 后者，"
            "否则两个标签会大量共同命中，优先级会把交叉样本全判给狩猎止损"
        )

    s_vol = shake.get("volume_ratio_max")
    h_vol = hunt.get("volume_ratio_min")
    if s_vol is None or h_vol is None:
        raise ConfigError(
            "[labeler.thresholds] 震仓需要 volume_ratio_max（缩量）、"
            "狩猎止损需要 volume_ratio_min（放量），用于区分两者的成交量方向"
        )
    if float(s_vol) > float(h_vol):
        raise ConfigError(
            f"[labeler.thresholds] 成交量方向重叠：震仓 volume_ratio_max="
            f"{s_vol}（缩量），狩猎止损 volume_ratio_min={h_vol}（放量）。"
            "前者必须 ≤ 后者"
        )


def validate_labeler_file(path: Path | str = None) -> None:
    """从磁盘加载并校验 labeler.yaml。"""
    if path is None:
        path = _config_dir() / "labeler.yaml"
    validate_labeler(_load(path))


# ---------------------------------------------------------------------------
# 全量校验
# ---------------------------------------------------------------------------

def _canonical_sector_rule_hash(cfg: dict) -> str:
    canonical = json.loads(json.dumps(cfg, ensure_ascii=False))
    canonical["rule_hash"]["frozen_hash"] = None
    payload = yaml.safe_dump(canonical, allow_unicode=True, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_sector_labeler(cfg: dict) -> None:
    """Validate the sector cycle labeler and its draft/frozen boundary."""
    required = {
        "version", "forward_return", "missing_benchmark_policy", "independence", "state_metadata", "shadow_v2",
        "features", "lookback", "zero_range_bar", "suspension_policy",
        "thresholds", "priority", "cardinality", "unlabeled",
        "coverage_monitor", "rule_hash",
    }
    _check_keys("sector_labeler.root", set(cfg), required)
    version = cfg["version"]
    if not isinstance(version, int) or isinstance(version, bool) or version not in {0, 1}:
        raise ConfigError(f"[sector_labeler.version] must be 0 (draft) or 1 (frozen), got {version!r}")

    forward = _check_mapping("sector_labeler.forward_return", cfg["forward_return"])
    _check_keys("sector_labeler.forward_return", set(forward), {"feature", "window_bars"})
    if forward["feature"] != _SECTOR_FORWARD_FEATURE:
        raise ConfigError("[sector_labeler.forward_return.feature] must be 'forward_absolute_return'")
    _check_positive_int("sector_labeler.forward_return.window_bars", forward["window_bars"])
    if cfg["missing_benchmark_policy"] != "not_applicable":
        raise ConfigError("[sector_labeler.missing_benchmark_policy] must be 'not_applicable'")

    independence = _check_mapping("sector_labeler.independence", cfg["independence"])
    independence_keys = {"may_read_sector_sentiment_index", "may_read_stock_labels", "may_read_stock_ohlcv"}
    _check_keys("sector_labeler.independence", set(independence), independence_keys)
    for key in independence_keys:
        if independence[key] is not False:
            raise ConfigError(f"[sector_labeler.independence.{key}] must be false to prevent label leakage")

    metadata = _check_mapping("sector_labeler.state_metadata", cfg["state_metadata"])
    _check_keys("sector_labeler.state_metadata", set(metadata), set(CYCLE_STATES))
    for state in CYCLE_STATES:
        item = _check_mapping(f"sector_labeler.state_metadata.{state}", metadata[state])
        _check_keys(
            f"sector_labeler.state_metadata.{state}",
            set(item),
            {"evidence_mode", "expansion_verified"},
        )
        if item["evidence_mode"] not in {"ohlcv", "price_trend_proxy"}:
            raise ConfigError(f"[sector_labeler.state_metadata.{state}.evidence_mode] is invalid")
        if item["expansion_verified"] is not False:
            raise ConfigError(
                f"[sector_labeler.state_metadata.{state}.expansion_verified] must be false in OHLCV-only v1"
            )
    fermentation = metadata["发酵"]
    if fermentation["evidence_mode"] != "price_trend_proxy":
        raise ConfigError(
            "[sector_labeler.state_metadata.发酵] v1 must disclose the price_trend_proxy evidence mode"
        )

    shadow = _check_mapping("sector_labeler.shadow_v2", cfg["shadow_v2"])
    _check_keys(
        "sector_labeler.shadow_v2",
        set(shadow),
        {
            "enabled", "cutover_mode", "required_history_days_per_sector",
            "required_stable_trading_days",
            "relabel_history_on_cutover", "rebuild_independent_c_counts",
            "atomic_switch", "require_independent_rule_hash",
        },
    )
    if shadow["enabled"] is not True or shadow["cutover_mode"] != "automatic":
        raise ConfigError("[sector_labeler.shadow_v2] must be enabled with automatic cutover")
    _check_positive_int(
        "sector_labeler.shadow_v2.required_history_days_per_sector",
        shadow["required_history_days_per_sector"],
    )
    _check_positive_int(
        "sector_labeler.shadow_v2.required_stable_trading_days",
        shadow["required_stable_trading_days"],
    )
    for key in (
        "relabel_history_on_cutover", "rebuild_independent_c_counts",
        "atomic_switch", "require_independent_rule_hash",
    ):
        if shadow[key] is not True:
            raise ConfigError(f"[sector_labeler.shadow_v2.{key}] must be true")

    features = _check_mapping("sector_labeler.features", cfg["features"])
    expected_features = {
        "return_1d", "forward_return", "volume_ratio_20", "volatility_20",
        "recent_trend_5d", "consecutive_down_days", "consecutive_shrink_days",
        "price_position_20", "turnover_rate",
    }
    _check_keys("sector_labeler.features", set(features), expected_features)
    if any(not isinstance(value, str) or not value for value in features.values()):
        raise ConfigError("[sector_labeler.features] every value must be non-empty text")

    lookback = _check_mapping("sector_labeler.lookback", cfg["lookback"])
    _check_keys("sector_labeler.lookback", set(lookback), {"range_bars", "volume_median_bars", "volatility_bars"})
    for key, value in lookback.items():
        _check_positive_int(f"sector_labeler.lookback.{key}", value)

    zero_range = _check_mapping("sector_labeler.zero_range_bar", cfg["zero_range_bar"])
    _check_keys("sector_labeler.zero_range_bar", set(zero_range), {"treat_as", "skip_labeling"})
    if zero_range["treat_as"] != "neutral" or zero_range["skip_labeling"] is not True:
        raise ConfigError("[sector_labeler.zero_range_bar] must be neutral and skip labeling")
    if cfg["suspension_policy"] != "not_applicable":
        raise ConfigError("[sector_labeler.suspension_policy] must be 'not_applicable'")

    thresholds = _check_mapping("sector_labeler.thresholds", cfg["thresholds"])
    _check_keys("sector_labeler.thresholds", set(thresholds), set(CYCLE_STATES))
    for state, expected_keys in _SECTOR_THRESHOLD_KEYS.items():
        block = _check_mapping(f"sector_labeler.thresholds.{state}", thresholds[state])
        _check_keys(f"sector_labeler.thresholds.{state}", set(block), expected_keys)
        for key, value in block.items():
            _check_number(f"sector_labeler.thresholds.{state}.{key}", value)

    priority = cfg["priority"]
    if not isinstance(priority, list) or len(priority) != len(set(priority)):
        raise ConfigError("[sector_labeler.priority] must be a list of unique cycle states")
    _check_keys("sector_labeler.priority", set(priority), set(CYCLE_STATES))

    cardinality = _check_mapping("sector_labeler.cardinality", cfg["cardinality"])
    _check_keys("sector_labeler.cardinality", set(cardinality), {"labels_per_sector_day", "allow_fractional_counts"})
    if cardinality["labels_per_sector_day"] != 1 or cardinality["allow_fractional_counts"] is not False:
        raise ConfigError("[sector_labeler.cardinality] requires exactly one non-fractional label")

    unlabeled = _check_mapping("sector_labeler.unlabeled", cfg["unlabeled"])
    _check_keys("sector_labeler.unlabeled", set(unlabeled), {"fallback_to_neutral", "update_c_counts", "persist_row_features"})
    if (unlabeled["fallback_to_neutral"] is not False or unlabeled["update_c_counts"] is not False
            or unlabeled["persist_row_features"] is not True):
        raise ConfigError("[sector_labeler.unlabeled] must not fabricate labels or C counts and must retain features")

    coverage = _check_mapping("sector_labeler.coverage_monitor", cfg["coverage_monitor"])
    _check_keys("sector_labeler.coverage_monitor", set(coverage), {"window_trading_days", "pool", "alert_threshold", "consecutive_windows_to_escalate", "on_alert"})
    for key in ("window_trading_days", "consecutive_windows_to_escalate"):
        _check_positive_int(f"sector_labeler.coverage_monitor.{key}", coverage[key])
    if coverage["pool"] != "per_sector" or coverage["on_alert"] != "freeze_and_escalate":
        raise ConfigError("[sector_labeler.coverage_monitor] must monitor each sector and freeze on alert")
    if not 0 < _check_number("sector_labeler.coverage_monitor.alert_threshold", coverage["alert_threshold"]) < 1:
        raise ConfigError("[sector_labeler.coverage_monitor.alert_threshold] must be between 0 and 1")

    rule_hash = _check_mapping("sector_labeler.rule_hash", cfg["rule_hash"])
    _check_keys("sector_labeler.rule_hash", set(rule_hash), {"algorithm", "scope", "store_with_every_label", "frozen_hash"})
    if rule_hash["algorithm"] != "sha256" or rule_hash["scope"] != "this_file" or rule_hash["store_with_every_label"] is not True:
        raise ConfigError("[sector_labeler.rule_hash] must store a sha256 hash for every label")
    frozen_hash = rule_hash["frozen_hash"]
    if frozen_hash is not None and (
        not isinstance(frozen_hash, str) or re.fullmatch(r"[0-9a-f]{64}", frozen_hash) is None
    ):
        raise ConfigError(
            "[sector_labeler.rule_hash.frozen_hash] must be 64 lowercase hexadecimal characters or null"
        )
    if version == 0 and frozen_hash is not None:
        raise ConfigError("[sector_labeler.rule_hash.frozen_hash] must be null for version 0 draft rules")
    if version == 1:
        if frozen_hash is None:
            raise ConfigError("[sector_labeler.rule_hash.frozen_hash] is required for frozen version 1 rules")
        if frozen_hash != _canonical_sector_rule_hash(cfg):
            raise ConfigError(
                "[sector_labeler.rule_hash.frozen_hash] does not match the canonical configuration"
            )


def validate_sector_labeler_file(path: Path | str = None) -> None:
    validate_sector_labeler(_load(path or _config_dir() / "sector_labeler.yaml"))


def validate_sectors(cfg: dict) -> None:
    _check_keys("sectors.root", set(cfg), {"version", "capacity", "movers", "dedup", "refresh", "index_source"})
    _check_version("sectors", cfg["version"])
    capacity = _check_mapping("sectors.capacity", cfg["capacity"])
    _check_keys("sectors.capacity", set(capacity), {"cap", "watchlist_exempt_from_cap"})
    _check_positive_int("sectors.capacity.cap", capacity["cap"])
    if capacity["watchlist_exempt_from_cap"] is not True:
        raise ConfigError("[sectors.capacity.watchlist_exempt_from_cap] must be true")
    movers = _check_mapping("sectors.movers", cfg["movers"])
    _check_keys("sectors.movers", set(movers), {"top_gainers", "top_losers"})
    for key, value in movers.items():
        _check_positive_int(f"sectors.movers.{key}", value)
    dedup = _check_mapping("sectors.dedup", cfg["dedup"])
    _check_keys("sectors.dedup", set(dedup), {"key", "preserve_source_tags", "source_tags"})
    if dedup["key"] != "sector_code" or dedup["preserve_source_tags"] is not True:
        raise ConfigError("[sectors.dedup] must preserve source tags using sector_code")
    if not isinstance(dedup["source_tags"], list) or set(dedup["source_tags"]) != {"watchlist", "movers"}:
        raise ConfigError("[sectors.dedup.source_tags] must contain exactly 'watchlist' and 'movers'")
    refresh = _check_mapping("sectors.refresh", cfg["refresh"])
    _check_keys("sectors.refresh", set(refresh), {"schedule", "freeze_during_prefetch", "allow_intraday_mutation"})
    schedule = refresh["schedule"]
    if not isinstance(schedule, list) or not schedule or len(schedule) != len(set(schedule)):
        raise ConfigError("[sectors.refresh.schedule] must be a non-empty list of unique HH:MM times")
    for item in schedule:
        try:
            hour, minute = (int(part) for part in item.split(":"))
        except (AttributeError, ValueError) as error:
            raise ConfigError(f"[sectors.refresh.schedule] invalid time {item!r}; expected HH:MM") from error
        if len(item) != 5 or not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ConfigError(f"[sectors.refresh.schedule] invalid time {item!r}; expected HH:MM")
    if refresh["freeze_during_prefetch"] is not True or refresh["allow_intraday_mutation"] is not False:
        raise ConfigError("[sectors.refresh] must freeze prefetches and disallow intraday mutation")
    if cfg["index_source"] != _INDEX_SOURCE:
        raise ConfigError(f"[sectors.index_source] must be '{_INDEX_SOURCE}'")


def validate_sectors_file(path: Path | str = None) -> None:
    validate_sectors(_load(path or _config_dir() / "sectors.yaml"))


def validate_sentiment(cfg: dict) -> None:
    _check_keys("sentiment.root", set(cfg), {"version", "range", "quota", "news_scoring", "inertia", "major_move_suppression", "index_source"})
    _check_version("sentiment", cfg["version"])
    value_range = _check_mapping("sentiment.range", cfg["range"])
    _check_keys("sentiment.range", set(value_range), {"min", "baseline", "max"})
    lower, baseline, upper = (_check_number(f"sentiment.range.{key}", value_range[key]) for key in ("min", "baseline", "max"))
    if not lower < baseline < upper:
        raise ConfigError("[sentiment.range] must satisfy min < baseline < max")
    quota = _check_mapping("sentiment.quota", cfg["quota"])
    _check_keys("sentiment.quota", set(quota), {"daily_net", "news", "price_action", "single_news"})
    for key, value in quota.items():
        if _check_number(f"sentiment.quota.{key}", value) <= 0:
            raise ConfigError(f"[sentiment.quota.{key}] must be greater than zero")
    if quota["news"] > quota["daily_net"] or quota["price_action"] > quota["daily_net"] or quota["single_news"] > quota["news"]:
        raise ConfigError("[sentiment.quota] component quotas must fit within their parent quotas")
    news_scoring = _check_mapping("sentiment.news_scoring", cfg["news_scoring"])
    _check_keys(
        "sentiment.news_scoring",
        set(news_scoring),
        {
            "max_age_days", "half_life_days",
            "default_relevance", "unrelated_relevance",
            "default_source_credibility", "major_media_credibility",
            "authoritative_source_credibility",
        },
    )
    for key in ("max_age_days", "half_life_days"):
        if _check_number(f"sentiment.news_scoring.{key}", news_scoring[key]) <= 0:
            raise ConfigError(f"[sentiment.news_scoring.{key}] must be greater than zero")
    for key in (
        "default_relevance", "unrelated_relevance", "default_source_credibility",
        "major_media_credibility", "authoritative_source_credibility",
    ):
        value = _check_number(f"sentiment.news_scoring.{key}", news_scoring[key])
        if not 0 <= value <= 1:
            raise ConfigError(f"[sentiment.news_scoring.{key}] must be between zero and one")
    if news_scoring["unrelated_relevance"] > news_scoring["default_relevance"]:
        raise ConfigError("[sentiment.news_scoring] unrelated relevance must not exceed default relevance")
    if not (
        news_scoring["default_source_credibility"]
        <= news_scoring["major_media_credibility"]
        <= news_scoring["authoritative_source_credibility"]
    ):
        raise ConfigError("[sentiment.news_scoring] source credibility weights must be ordered")
    inertia = _check_mapping("sentiment.inertia", cfg["inertia"])
    _check_keys("sentiment.inertia", set(inertia), {"decay", "apply_decay_on_no_news_days", "apply_price_action_on_no_news_days"})
    if not 0 < _check_number("sentiment.inertia.decay", inertia["decay"]) < 1:
        raise ConfigError("[sentiment.inertia.decay] must be between 0 and 1")
    if inertia["apply_decay_on_no_news_days"] is not True or inertia["apply_price_action_on_no_news_days"] is not True:
        raise ConfigError("[sentiment.inertia] must update both decay and price action on no-news days")
    suppression = _check_mapping("sentiment.major_move_suppression", cfg["major_move_suppression"])
    _check_keys("sentiment.major_move_suppression", set(suppression), {"triggers", "news_quota_multiplier", "price_action_unaffected", "bidirectional"})
    triggers = _check_mapping("sentiment.major_move_suppression.triggers", suppression["triggers"])
    _check_keys("sentiment.major_move_suppression.triggers", set(triggers), {"single_day_drop", "two_day_cumulative"})
    single = _check_number("sentiment.major_move_suppression.triggers.single_day_drop", triggers["single_day_drop"])
    two_day = _check_number("sentiment.major_move_suppression.triggers.two_day_cumulative", triggers["two_day_cumulative"])
    if not two_day < single < 0:
        raise ConfigError("[sentiment.major_move_suppression.triggers] must satisfy two_day_cumulative < single_day_drop < 0")
    if not 0 < _check_number("sentiment.major_move_suppression.news_quota_multiplier", suppression["news_quota_multiplier"]) < 1:
        raise ConfigError("[sentiment.major_move_suppression.news_quota_multiplier] must be between 0 and 1")
    if suppression["price_action_unaffected"] is not True or suppression["bidirectional"] is not True:
        raise ConfigError("[sentiment.major_move_suppression] must preserve price action and be bidirectional")
    if cfg["index_source"] != _INDEX_SOURCE:
        raise ConfigError(f"[sentiment.index_source] must be '{_INDEX_SOURCE}'")


def validate_sentiment_file(path: Path | str = None) -> None:
    validate_sentiment(_load(path or _config_dir() / "sentiment.yaml"))


# ---------------------------------------------------------------------------
# prompt_routing.yaml 校验（ADR-0005 / ADR-0011）
# ---------------------------------------------------------------------------

def _validated_prompt_path(prompt_root: Path, raw_path: Any, path: str) -> Path:
    """Return a registered prompt path after enforcing prompt-root containment."""
    if not isinstance(raw_path, str) or not raw_path:
        raise ConfigError(f"[{path}] 必须是非空字符串相对路径")

    relative_path = Path(raw_path)
    if relative_path.is_absolute() or relative_path.root or relative_path.drive:
        raise ConfigError(f"[{path}] 必须是相对路径，不能使用绝对路径：{raw_path!r}")
    if ".." in relative_path.parts:
        raise ConfigError(f"[{path}] 不允许 '..' 目录穿越：{raw_path!r}")

    candidate = (prompt_root / relative_path).resolve()
    try:
        candidate.relative_to(prompt_root)
    except ValueError as error:
        raise ConfigError(
            f"[{path}] 解析后超出合法提示词根目录：{raw_path!r}"
        ) from error
    if not candidate.is_file():
        raise ConfigError(f"[{path}] 提示词文件不存在或不是普通文件：{raw_path!r}")
    return candidate


def validate_prompt_routing(cfg: dict, prompt_root: Path | str) -> None:
    """Validate the registered, two-participant prompt routing table.

    The table can only name existing regular files below ``prompt_root``. The
    check resolves symlinks before its containment comparison so links cannot
    provide an escape from the registered prompt directory.
    """
    root = Path(prompt_root)
    if not root.is_dir():
        raise ConfigError(f"[prompt_routing] 合法提示词根目录不存在或不是目录：{root}")
    root = root.resolve()

    _check_keys(
        "prompt_routing.root",
        set(cfg),
        {"version", "registry", "common", "routes"},
    )
    _check_version("prompt_routing", cfg["version"])
    registry = cfg["registry"]
    if not isinstance(registry, list) or not registry:
        raise ConfigError("[prompt_routing.registry] 必须是非空列表")
    if len(registry) != len(set(registry)):
        raise ConfigError("[prompt_routing.registry] 不允许重复文件")
    registered = {
        _validated_prompt_path(root, raw, f"prompt_routing.registry[{index}]")
        for index, raw in enumerate(registry)
    }
    actual = {path.resolve() for path in root.rglob("*.txt")}
    if registered != actual:
        missing = sorted(str(path.relative_to(root)) for path in actual - registered)
        extra = sorted(str(path.relative_to(root)) for path in registered - actual)
        raise ConfigError(
            f"[prompt_routing.registry] 与提示词目录不一致；未登记={missing}，多余={extra}"
        )
    manual_path = root.parent / "docs" / "MANUAL.md"
    if not manual_path.is_file():
        raise ConfigError("[prompt_routing.registry] 使用手册 docs/MANUAL.md 不存在")
    manual = manual_path.read_text(encoding="utf-8")
    missing_from_manual = sorted(
        str(path.relative_to(root)).replace("\\", "/")
        for path in registered
        if f"prompt_engine/{str(path.relative_to(root)).replace(chr(92), '/')}" not in manual
    )
    if missing_from_manual:
        raise ConfigError(
            f"[prompt_routing.registry] 使用手册缺少提示词：{missing_from_manual}"
        )
    common = _check_mapping("prompt_routing.common", cfg["common"])
    expected_common = {
        "参与者识别",
        "人设与思维方式",
        "主体目的分析",
        "情绪周期判断",
        "新闻情绪评分",
        "情景应对",
    }
    _check_keys("prompt_routing.common", set(common), expected_common)
    for name, raw_path in common.items():
        if _validated_prompt_path(root, raw_path, f"prompt_routing.common.{name}") not in registered:
            raise ConfigError(f"[prompt_routing.common.{name}] 文件未登记")
    routes = _check_mapping("prompt_routing.routes", cfg["routes"])
    _check_keys("prompt_routing.routes", set(routes), set(CYCLE_STATES))

    for cycle_state in CYCLE_STATES:
        route_row = _check_mapping(
            f"prompt_routing.routes.{cycle_state}", routes[cycle_state]
        )
        _check_keys(
            f"prompt_routing.routes.{cycle_state}", set(route_row), set(PARTICIPANTS)
        )
        for participant in PARTICIPANTS:
            route_path = _validated_prompt_path(
                root,
                route_row[participant],
                f"prompt_routing.routes.{cycle_state}.{participant}",
            )
            if route_path not in registered:
                raise ConfigError(
                    f"[prompt_routing.routes.{cycle_state}.{participant}] 文件未登记"
                )


def validate_prompt_routing_file(
    path: Path | str,
    prompt_root: Path | str,
) -> None:
    """Load and validate a prompt routing table without enabling production routing."""
    validate_prompt_routing(_load(path), prompt_root)


def validate_sector_labeler_v2(cfg: dict) -> None:
    _check_keys(
        "sector_labeler_v2.root", set(cfg),
        {"version", "required_fields", "cutover", "rule_hash"},
    )
    _check_version("sector_labeler_v2", cfg["version"])
    required = cfg["required_fields"]
    expected = {
        "limit_streak", "is_rise_limit", "is_fall_limit", "volume",
        "previous_five_day_average_volume",
    }
    if not isinstance(required, list) or set(required) != expected or len(required) != len(expected):
        raise ConfigError("[sector_labeler_v2.required_fields] 必需字段不完整或重复")
    cutover = _check_mapping("sector_labeler_v2.cutover", cfg["cutover"])
    _check_keys(
        "sector_labeler_v2.cutover", set(cutover),
        {"required_history_days_per_sector", "required_stable_trading_days"},
    )
    for key, value in cutover.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ConfigError(f"[sector_labeler_v2.cutover.{key}] 必须是正整数")
    rule_hash = _check_mapping("sector_labeler_v2.rule_hash", cfg["rule_hash"])
    _check_keys(
        "sector_labeler_v2.rule_hash", set(rule_hash),
        {"algorithm", "scope", "store_with_every_label"},
    )
    if rule_hash != {"algorithm": "sha256", "scope": "this_file", "store_with_every_label": True}:
        raise ConfigError("[sector_labeler_v2.rule_hash] 必须使用独立 sha256 规则哈希")


def validate_sector_labeler_v2_file(path: Path | str) -> None:
    validate_sector_labeler_v2(_load(path))


def validate_dragon_tiger_inference(cfg: dict) -> None:
    _check_keys("dragon_tiger_inference.root", set(cfg), {"version", "observation_states"})
    _check_version("dragon_tiger_inference", cfg["version"])
    observations = _check_mapping("dragon_tiger_inference.observation_states", cfg["observation_states"])
    expected = {
        "institution_net_buy", "institution_net_sell", "hot_money_net_buy", "hot_money_net_sell"
    }
    _check_keys("dragon_tiger_inference.observation_states", set(observations), expected)
    for evidence, state in observations.items():
        if state not in CYCLE_STATES:
            raise ConfigError(
                f"[dragon_tiger_inference.observation_states.{evidence}] 必须是合法周期状态"
            )


def validate_dragon_tiger_inference_file(path: Path | str) -> None:
    validate_dragon_tiger_inference(_load(path))


def validate_policy_detector_file(path: Path | str) -> None:
    """校验 policy_detector.yaml：复用检测器自身的严格加载器（含 dataclass 校验）。"""
    from src.reasoning.policy_detector import load_policy_detector_config

    try:
        load_policy_detector_config(path)
    except (ValueError, TypeError) as error:
        raise ConfigError(str(error)) from error


_CONFIG_VALIDATORS = {
    "hmm_prior.yaml": validate_file,
    "signals.yaml": validate_signals_file,
    "labeler.yaml": validate_labeler_file,
    "sectors.yaml": validate_sectors_file,
    "sentiment.yaml": validate_sentiment_file,
    "sector_labeler_v2.yaml": validate_sector_labeler_v2_file,
    "dragon_tiger_inference.yaml": validate_dragon_tiger_inference_file,
    "policy_detector.yaml": validate_policy_detector_file,
}


def validate_all(config_dir: Path | str = None) -> None:
    """
    校验 config/ 下全部已实现校验器的配置文件。

    校验顺序固定，先报出的错误优先修。任一文件失败即抛出。
    """
    base = Path(config_dir) if config_dir else _config_dir()
    for filename, validator in _CONFIG_VALIDATORS.items():
        path = base / filename
        try:
            validator(path)
        except ConfigError as error:
            raise ConfigError(f"[{path.resolve()}] {error}") from error
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise ConfigError(
                f"[{path.resolve()}] invalid configuration structure: {error}"
            ) from error
    routing_path = base / "prompt_routing.yaml"
    try:
        validate_prompt_routing_file(routing_path, base.parent / "prompt_engine")
    except ConfigError as error:
        raise ConfigError(f"[{routing_path.resolve()}] {error}") from error
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise ConfigError(
            f"[{routing_path.resolve()}] invalid configuration structure: {error}"
        ) from error


# ---------------------------------------------------------------------------
# CLI 入口（python -m src.config_validator）
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    # Windows 控制台默认 GBK，直接 print 会在 ✓ 和中文错误信息上抛
    # UnicodeEncodeError，把真正的校验结果盖掉。改用 UTF-8 且遇到
    # 不可编码字符替换而不中断。
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

    try:
        if len(sys.argv) > 1:
            target = Path(sys.argv[1])
            fn = _CONFIG_VALIDATORS.get(target.name)
            if fn is None:
                print(
                    f"✗ 没有针对 '{target.name}' 的校验器；"
                    f"已实现：{sorted(_CONFIG_VALIDATORS)}",
                    file=sys.stderr,
                )
                sys.exit(2)
            fn(target)
            print(f"✓ {target.name} 校验通过")
        else:
            validate_all()
            print("✓ 全部配置校验通过")
    except ConfigError as e:
        print(f"✗ 配置错误：{e}", file=sys.stderr)
        sys.exit(1)
