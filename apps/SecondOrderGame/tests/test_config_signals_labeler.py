"""
测试套件 — signals.yaml / labeler.yaml 校验器
==============================================
覆盖 ADR-0016 与 ADR-0017 冻结的每一条约束。

这些测试的重点不是"值对不对"（那要靠回测），而是"错误的值会不会被拦住"。
配置里的每一条约束都对应一种**静默失效**：不报错，但信号永不触发或标签
被悄悄污染。所以每条约束都必须有一个测试证明它真的会抛错。
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from src.config_validator import (
    BEHAVIORS,
    ConfigError,
    validate_all,
    validate_labeler,
    validate_labeler_file,
    validate_signals,
    validate_signals_file,
)


CONFIG_DIR    = Path(__file__).parent.parent / "config"
SIGNALS_PATH  = CONFIG_DIR / "signals.yaml"
LABELER_PATH  = CONFIG_DIR / "labeler.yaml"


def _load(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture
def signals_cfg() -> dict:
    return _load(SIGNALS_PATH)


@pytest.fixture
def labeler_cfg() -> dict:
    return _load(LABELER_PATH)


def _mutate(cfg: dict, path: list, value) -> dict:
    """深拷贝后按路径改一个值。path 元素可以是 dict 键或 list 下标。"""
    c = copy.deepcopy(cfg)
    node = c
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return c


def _drop(cfg: dict, path: list) -> dict:
    """深拷贝后删掉一个键。"""
    c = copy.deepcopy(cfg)
    node = c
    for key in path[:-1]:
        node = node[key]
    del node[path[-1]]
    return c


# ===========================================================================
# signals.yaml — 正常通过
# ===========================================================================

class TestSignalsHappyPath:

    def test_real_file_passes(self):
        validate_signals_file(SIGNALS_PATH)

    def test_default_path_passes(self):
        validate_signals_file()

    def test_loaded_dict_passes(self, signals_cfg):
        validate_signals(signals_cfg)

    def test_ema_is_accepted(self, signals_cfg):
        validate_signals(_mutate(signals_cfg, ["nash", "ma_type"], "ema"))


# ===========================================================================
# signals.yaml — nash.deviation 防退化（ADR-0016 二）
# ===========================================================================

class TestNashDeviation:
    """
    这是整个 signals.yaml 里最危险的一个数字。文档从未字面赋值，正文说「2%」
    但公式是标准差倍数。填 0.02 不报错、不崩溃，只是让带宽退化到 ±0.03%，
    信号每根 K 线都触发 —— 必须在校验层拦住。
    """

    def test_percent_misreading_rejected(self, signals_cfg):
        """0.02 是把「2%」误当成比例填进来的典型错误。"""
        with pytest.raises(ConfigError, match="带宽会退化"):
            validate_signals(_mutate(signals_cfg, ["nash", "deviation"], 0.02))

    def test_error_message_explains_semantics(self, signals_cfg):
        """错误信息必须说清 deviation 是倍数不是百分比，否则用户会再填错一次。"""
        with pytest.raises(ConfigError) as exc:
            validate_signals(_mutate(signals_cfg, ["nash", "deviation"], 0.02))
        msg = str(exc.value)
        assert "标准差倍数" in msg
        assert "ADR-0016" in msg

    def test_zero_rejected(self, signals_cfg):
        with pytest.raises(ConfigError, match="必须为正"):
            validate_signals(_mutate(signals_cfg, ["nash", "deviation"], 0))

    def test_negative_rejected(self, signals_cfg):
        with pytest.raises(ConfigError, match="必须为正"):
            validate_signals(_mutate(signals_cfg, ["nash", "deviation"], -2.0))

    def test_non_numeric_rejected(self, signals_cfg):
        with pytest.raises(ConfigError, match="不是数字"):
            validate_signals(_mutate(signals_cfg, ["nash", "deviation"], "2.0"))

    def test_frozen_value_is_one_point_five(self, signals_cfg):
        """ADR-0025 实测校准为 1.5（原 ADR-0016 拍板 2.0）。改值必须先改 ADR。"""
        assert signals_cfg["nash"]["deviation"] == 1.5

    def test_larger_multiple_accepted(self, signals_cfg):
        """2.5 或 3.0 是合理的调参方向，不该被拦。"""
        validate_signals(_mutate(signals_cfg, ["nash", "deviation"], 3.0))

    def test_unknown_ma_type_rejected(self, signals_cfg):
        with pytest.raises(ConfigError, match="未知均线类型"):
            validate_signals(_mutate(signals_cfg, ["nash", "ma_type"], "wma"))


# ===========================================================================
# signals.yaml — 周期换算（ADR-0016 一）
# ===========================================================================

class TestPeriodScaling:

    def test_odd_period_rejected(self, signals_cfg):
        """scale_factor=2 时奇数周期不可能是「整交易日 ×2」的结果。"""
        with pytest.raises(ConfigError, match="不是 scale_factor"):
            validate_signals(_mutate(signals_cfg, ["herd", "rsi_length"], 7))

    def test_odd_nash_period_rejected(self, signals_cfg):
        with pytest.raises(ConfigError, match="不是 scale_factor"):
            validate_signals(_mutate(signals_cfg, ["nash", "period"], 5))

    def test_even_unscaled_value_slips_through(self, signals_cfg):
        """
        ⚠ 已知局限，不是遗漏：整除检查只能抓奇数。文档原值 14 恰好是偶数，
        直接当根数填（= 7 个交易日，语义腰斩一半）不会被拦住。

        要真正封住这个口子需要在配置里记下每个参数的文档原值并逐一比对，
        但那会把「参数可调」变成「参数锁死」—— 这些值本来就等着用 A 股
        实测数据替换。所以这里选择接受局限，用 ADR-0016 一 的换算表
        作为人工复核依据，而不是加一层假的自动保障。
        """
        validate_signals(_mutate(signals_cfg, ["herd", "rsi_length"], 14))

    def test_all_period_params_are_even(self, signals_cfg):
        """scale_factor=2 时全部周期必须是偶数。"""
        scale = signals_cfg["period_semantics"]["scale_factor"]
        periods = [
            signals_cfg["herd"]["rsi_length"],
            signals_cfg["herd"]["volume_ma_length"],
            signals_cfg["herd"]["momentum_lookback"],
            signals_cfg["herd"]["momentum_ma_length"],
            signals_cfg["liquidity_trap"]["lookback"],
            signals_cfg["smart_money"]["ma_length"],
            signals_cfg["institutional"]["ad_ma_length"],
            signals_cfg["nash"]["period"],
        ]
        assert all(p % scale == 0 for p in periods)

    def test_float_period_rejected(self, signals_cfg):
        with pytest.raises(ConfigError, match="必须是整数 K 线根数"):
            validate_signals(_mutate(signals_cfg, ["nash", "period"], 8.0))

    def test_zero_period_rejected(self, signals_cfg):
        with pytest.raises(ConfigError, match="必须 ≥ 1"):
            validate_signals(_mutate(signals_cfg, ["herd", "rsi_length"], 0))

    def test_negative_period_rejected(self, signals_cfg):
        with pytest.raises(ConfigError, match="必须 ≥ 1"):
            validate_signals(_mutate(signals_cfg, ["nash", "period"], -8))

    def test_scale_factor_mismatch_rejected(self, signals_cfg):
        """bars_per_day 与 scale_factor 不一致意味着换算倍数错了。"""
        with pytest.raises(ConfigError, match="周期语义漂移"):
            validate_signals(
                _mutate(signals_cfg, ["period_semantics", "scale_factor"], 3)
            )

    def test_scale_one_allows_odd_periods(self, signals_cfg):
        """若将来改用日线（bars_per_day=1），奇数周期应被允许。"""
        c = copy.deepcopy(signals_cfg)
        c["period_semantics"]["bars_per_day"] = 1
        c["period_semantics"]["scale_factor"] = 1
        c["herd"]["rsi_length"] = 14
        c["nash"]["period"] = 4
        c["herd"]["volume_ma_length"] = 5
        c["herd"]["momentum_lookback"] = 5
        c["herd"]["momentum_ma_length"] = 5
        c["liquidity_trap"]["lookback"] = 5
        c["smart_money"]["ma_length"] = 5
        c["institutional"]["ad_ma_length"] = 5
        validate_signals(c)


# ===========================================================================
# signals.yaml — 心理价位档位（ADR-0016 三）
# ===========================================================================

class TestPsychLevelBrackets:
    """
    文档的 100/10 取整规则在 A 股会静默失效：120 元股票最近整百位距离 17%，
    1% 接近度永不满足，liquidity_trap_up 对高价股直接死掉且不报错。
    """

    def test_last_bracket_must_be_unbounded(self, signals_cfg):
        brackets = copy.deepcopy(signals_cfg["liquidity_trap"]["psych_level_brackets"])
        brackets[-1]["below"] = 1000.0
        with pytest.raises(ConfigError, match="最后一档必须是 below: null"):
            validate_signals(
                _mutate(signals_cfg, ["liquidity_trap", "psych_level_brackets"], brackets)
            )

    def test_null_below_only_in_last_position(self, signals_cfg):
        brackets = [
            {"below": None, "step": 0.5},
            {"below": 100.0, "step": 1.0},
        ]
        with pytest.raises(ConfigError, match="只能出现在最后一档"):
            validate_signals(
                _mutate(signals_cfg, ["liquidity_trap", "psych_level_brackets"], brackets)
            )

    def test_non_ascending_brackets_rejected(self, signals_cfg):
        brackets = [
            {"below": 100.0, "step": 1.0},
            {"below": 10.0,  "step": 0.5},
            {"below": None,  "step": 10.0},
        ]
        with pytest.raises(ConfigError, match="档位必须按价格升序"):
            validate_signals(
                _mutate(signals_cfg, ["liquidity_trap", "psych_level_brackets"], brackets)
            )

    def test_duplicate_boundary_rejected(self, signals_cfg):
        brackets = [
            {"below": 10.0, "step": 0.5},
            {"below": 10.0, "step": 1.0},
            {"below": None, "step": 10.0},
        ]
        with pytest.raises(ConfigError, match="档位必须按价格升序"):
            validate_signals(
                _mutate(signals_cfg, ["liquidity_trap", "psych_level_brackets"], brackets)
            )

    def test_zero_step_rejected(self, signals_cfg):
        brackets = copy.deepcopy(signals_cfg["liquidity_trap"]["psych_level_brackets"])
        brackets[0]["step"] = 0
        with pytest.raises(ConfigError, match="step 必须为正"):
            validate_signals(
                _mutate(signals_cfg, ["liquidity_trap", "psych_level_brackets"], brackets)
            )

    def test_empty_brackets_rejected(self, signals_cfg):
        with pytest.raises(ConfigError, match="必须是非空列表"):
            validate_signals(
                _mutate(signals_cfg, ["liquidity_trap", "psych_level_brackets"], [])
            )

    def test_frozen_brackets_cover_a_share_range(self, signals_cfg):
        """ADR-0016 拍板三档：<10 用 0.5 元、10~100 用 1 元、>100 用 10 元。"""
        brackets = signals_cfg["liquidity_trap"]["psych_level_brackets"]
        assert [b["step"] for b in brackets] == [0.5, 1.0, 10.0]
        assert brackets[-1]["below"] is None

    def test_high_priced_stock_finds_a_bracket(self, signals_cfg):
        """
        回归测试：1400 元的标的必须落在某一档里。文档原规则下它会落到
        「整百」档，最近整数位距离过远导致 1% 接近度永不满足。
        """
        brackets = signals_cfg["liquidity_trap"]["psych_level_brackets"]
        price = 1400.0
        step = next(
            b["step"] for b in brackets
            if b["below"] is None or price < b["below"]
        )
        nearest = round(price / step) * step
        assert abs(price - nearest) / price < signals_cfg["liquidity_trap"]["psych_proximity"]


# ===========================================================================
# signals.yaml — 阈值类参数
# ===========================================================================

class TestSignalThresholds:

    def test_rsi_bounds_rejected(self, signals_cfg):
        with pytest.raises(ConfigError, match="须在 0~100"):
            validate_signals(_mutate(signals_cfg, ["herd", "rsi_overbought"], 120))

    def test_inverted_rsi_thresholds_rejected(self, signals_cfg):
        """超卖 ≥ 超买时两个区间重叠，羊群追涨与杀跌会同时成立。"""
        with pytest.raises(ConfigError, match="超买超卖区间重叠"):
            validate_signals(_mutate(signals_cfg, ["herd", "rsi_oversold"], 75))

    def test_equal_rsi_thresholds_rejected(self, signals_cfg):
        with pytest.raises(ConfigError, match="超买超卖区间重叠"):
            validate_signals(_mutate(signals_cfg, ["herd", "rsi_oversold"], 70))

    def test_volume_multiple_below_one_rejected(self, signals_cfg):
        """< 1 意味着「缩量即放量」，判定方向反了。"""
        with pytest.raises(ConfigError, match="判定方向反了"):
            validate_signals(_mutate(signals_cfg, ["herd", "volume_multiple"], 0.8))

    def test_institutional_volume_multiple_checked(self, signals_cfg):
        with pytest.raises(ConfigError, match="判定方向反了"):
            validate_signals(
                _mutate(signals_cfg, ["institutional", "volume_multiple"], 0.5)
            )

    def test_psych_proximity_as_percent_rejected(self, signals_cfg):
        """写 1 而不是 0.01 是典型的百分比误填。"""
        with pytest.raises(ConfigError, match="1% 应写 0.01"):
            validate_signals(
                _mutate(signals_cfg, ["liquidity_trap", "psych_proximity"], 1)
            )

    def test_psych_proximity_zero_rejected(self, signals_cfg):
        with pytest.raises(ConfigError, match="开区间"):
            validate_signals(
                _mutate(signals_cfg, ["liquidity_trap", "psych_proximity"], 0)
            )

    def test_doc_values_preserved(self, signals_cfg):
        """阈值类参数不随周期换算，须保持文档原值（除 ADR-0025 实测校准项）。"""
        assert signals_cfg["herd"]["rsi_oversold"] == 30
        assert signals_cfg["herd"]["volume_multiple"] == 2.0
        assert signals_cfg["institutional"]["volume_multiple"] == 2.5

    def test_adr_0025_calibrated_thresholds(self, signals_cfg):
        """ADR-0025 实测校准：rsi_overbought 与 psych_proximity 偏离文档原值。"""
        assert signals_cfg["herd"]["rsi_overbought"] == 65
        assert signals_cfg["liquidity_trap"]["psych_proximity"] == 0.02

    def test_doc_void_params_stay_null(self, signals_cfg):
        """
        文档未给数值的参数必须保持 null。填上就是发明数值 ——
        这些参数会直接进生产配置。
        """
        assert signals_cfg["herd"]["momentum_deviation_min"] is None
        assert signals_cfg["liquidity_trap"]["retracement_min"] is None
        assert signals_cfg["liquidity_trap"]["breakout_margin_min"] is None
        assert signals_cfg["smart_money"]["absolute_threshold"] is None


# ===========================================================================
# signals.yaml — 零振幅 K 线（ADR-0016 四）
# ===========================================================================

class TestDegenerateBar:

    def test_insufficient_data_forbids_epsilon(self, signals_cfg):
        """两种策略同时开启是自相矛盾的配置。"""
        with pytest.raises(ConfigError, match="兜底会产出伪值"):
            validate_signals(
                _mutate(signals_cfg, ["degenerate_bar", "epsilon_fallback"], True)
            )

    def test_insufficient_data_forbids_carry_forward(self, signals_cfg):
        with pytest.raises(ConfigError, match="兜底会产出伪值"):
            validate_signals(
                _mutate(signals_cfg, ["degenerate_bar", "carry_forward_previous"], True)
            )

    def test_unknown_policy_rejected(self, signals_cfg):
        with pytest.raises(ConfigError, match="未知策略"):
            validate_signals(
                _mutate(signals_cfg, ["degenerate_bar", "zero_range_policy"], "ignore")
            )

    def test_empty_affected_signals_rejected(self, signals_cfg):
        with pytest.raises(ConfigError, match="不能为空"):
            validate_signals(
                _mutate(signals_cfg, ["degenerate_bar", "affected_signals"], [])
            )

    def test_denominator_signals_are_covered(self, signals_cfg):
        """分母含 (high - low) 的两个信号都必须在受影响列表里。"""
        affected = signals_cfg["degenerate_bar"]["affected_signals"]
        assert "smart_money" in affected
        assert "institutional_ad" in affected

    def test_frozen_policy_is_insufficient_data(self, signals_cfg):
        assert signals_cfg["degenerate_bar"]["zero_range_policy"] == "insufficient_data"


# ===========================================================================
# signals.yaml — 信号定位（ADR-0016 五、六）
# ===========================================================================

class TestSignalUsage:

    def test_trade_signal_role_rejected(self, signals_cfg):
        with pytest.raises(ConfigError, match="observation_feature"):
            validate_signals(_mutate(signals_cfg, ["usage", "role"], "trade_signal"))

    def test_position_size_emission_rejected(self, signals_cfg):
        """开启会出现两个仓位决策权（ADR-0015 已把仓位归给 PA）。"""
        with pytest.raises(ConfigError, match="两个仓位决策权"):
            validate_signals(
                _mutate(signals_cfg, ["usage", "emit_position_size"], True)
            )

    def test_frozen_usage(self, signals_cfg):
        usage = signals_cfg["usage"]
        assert usage["role"] == "observation_feature"
        assert usage["emit_position_size"] is False
        assert usage["emit_composite_signal"] is False

    def test_nash_reversion_keeps_doc_volume_condition(self, signals_cfg):
        """
        回归测试：回归信号的成交量条件是 1 倍（volume > volume_ma），
        不是羊群用的 2 倍。这是文档原文写法，不能当笔误"修正"。
        """
        feats = signals_cfg["composite_features"]
        for key in ("nash_reversion_buy", "nash_reversion_sell"):
            assert "volume > volume_ma" in feats[key]

    def test_take_profit_marked_not_applicable(self, signals_cfg):
        """文档挂空的止盈止损应显式标为「PA 负责」，而不是留空让人以为遗漏。"""
        na = signals_cfg["not_applicable"]
        assert "take_profit_pct" in na
        assert "stop_loss_pct" in na


# ===========================================================================
# signals.yaml — 结构与版本
# ===========================================================================

class TestSignalsStructure:

    def test_missing_top_key_rejected(self, signals_cfg):
        with pytest.raises(ConfigError, match="缺少键"):
            validate_signals(_drop(signals_cfg, ["nash"]))

    def test_unknown_top_key_rejected(self, signals_cfg):
        with pytest.raises(ConfigError, match="未知键"):
            validate_signals(_mutate(signals_cfg, ["nash_band"], {}))

    def test_missing_nested_key_rejected(self, signals_cfg):
        with pytest.raises(ConfigError, match="缺少键"):
            validate_signals(_drop(signals_cfg, ["nash", "ma_type"]))

    def test_version_must_be_int(self, signals_cfg):
        with pytest.raises(ConfigError, match="必须是整数"):
            validate_signals(_mutate(signals_cfg, ["version"], "1"))

    def test_version_must_be_positive(self, signals_cfg):
        with pytest.raises(ConfigError, match="必须 ≥ 1"):
            validate_signals(_mutate(signals_cfg, ["version"], 0))

    def test_version_bool_rejected(self, signals_cfg):
        """YAML 的 true 会被 Python 当成 int 1，必须显式排除。"""
        with pytest.raises(ConfigError, match="必须是整数"):
            validate_signals(_mutate(signals_cfg, ["version"], True))


# ===========================================================================
# labeler.yaml — 正常通过
# ===========================================================================

class TestLabelerHappyPath:

    def test_real_file_passes(self):
        validate_labeler_file(LABELER_PATH)

    def test_default_path_passes(self):
        validate_labeler_file()

    def test_loaded_dict_passes(self, labeler_cfg):
        validate_labeler(labeler_cfg)


# ===========================================================================
# labeler.yaml — 前向收益口径（ADR-0017 一）
# ===========================================================================

class TestForwardReturn:

    def test_frozen_feature_is_excess(self, labeler_cfg):
        """
        实测：绝对口径 25,027 个负向行里 13,970 行在相对口径下是中性。
        改回绝对口径会把行业共同冲击混进标签。
        """
        assert labeler_cfg["forward_return"]["feature"] == "forward_excess_return"

    def test_unknown_feature_rejected(self, labeler_cfg):
        with pytest.raises(ConfigError, match="未知口径"):
            validate_labeler(
                _mutate(labeler_cfg, ["forward_return", "feature"], "forward_alpha")
            )

    def test_per_behavior_window_rejected(self, labeler_cfg):
        with pytest.raises(ConfigError, match="无法验证"):
            validate_labeler(
                _mutate(labeler_cfg, ["forward_return", "per_behavior_window"], True)
            )

    def test_window_must_be_positive_int(self, labeler_cfg):
        with pytest.raises(ConfigError, match="≥1 的整数"):
            validate_labeler(
                _mutate(labeler_cfg, ["forward_return", "window_bars"], 0)
            )

    def test_frozen_window_is_five(self, labeler_cfg):
        assert labeler_cfg["forward_return"]["window_bars"] == 5

    def test_missing_benchmark_must_be_no_label(self, labeler_cfg):
        """替换基准会让标签含义静默漂移。"""
        with pytest.raises(ConfigError, match="替换基准"):
            validate_labeler(
                _mutate(labeler_cfg, ["missing_benchmark_policy"], "use_csi300")
            )

    def test_suspension_must_be_no_label(self, labeler_cfg):
        with pytest.raises(ConfigError, match="延长窗口"):
            validate_labeler(
                _mutate(labeler_cfg, ["suspension_policy"], "extend_window")
            )


# ===========================================================================
# labeler.yaml — 两层独立（ADR-0017 二）
# ===========================================================================

class TestIndependence:

    def test_reading_sector_labels_rejected(self, labeler_cfg):
        with pytest.raises(ConfigError, match="跨层标签泄漏"):
            validate_labeler(
                _mutate(labeler_cfg, ["independence", "may_read_sector_labels"], True)
            )

    def test_reading_sector_sentiment_rejected(self, labeler_cfg):
        with pytest.raises(ConfigError, match="跨层标签泄漏"):
            validate_labeler(
                _mutate(labeler_cfg, ["independence", "may_read_sector_sentiment"], True)
            )

    def test_reading_raw_sector_ohlcv_allowed(self, labeler_cfg):
        """
        原始板块 OHLCV 是不可变的公共行情事实，读它不构成标签泄漏 ——
        否则就只能用绝对收益，而绝对收益已被实测否决。
        """
        assert labeler_cfg["independence"]["may_read_sector_ohlcv"] is True
        validate_labeler(labeler_cfg)


# ===========================================================================
# labeler.yaml — 震仓 vs 狩猎止损互斥（ADR-0017 三）
# ===========================================================================

class TestShakeoutVsStopHunt:
    """
    六条规则里唯一一处「两个标签靠方向相反的同名条件区分」。破坏后两者会
    大量共同命中，而优先级会把全部交叉样本判给狩猎止损，震仓的计数被悄悄
    吃掉 —— 没有任何报错。
    """

    def test_support_boundary_overlap_rejected(self, labeler_cfg):
        """震仓的「未跌破」下界低于狩猎的「已跌破」上界时，两者不再互斥。"""
        with pytest.raises(ConfigError, match="支撑边界重叠"):
            validate_labeler(
                _mutate(labeler_cfg, ["thresholds", "震仓", "support_break_min"], -0.02)
            )

    def test_volume_direction_overlap_rejected(self, labeler_cfg):
        with pytest.raises(ConfigError, match="成交量方向重叠"):
            validate_labeler(
                _mutate(labeler_cfg, ["thresholds", "震仓", "volume_ratio_max"], 2.0)
            )

    def test_missing_support_threshold_rejected(self, labeler_cfg):
        with pytest.raises(ConfigError, match="用于在支撑边界上区分"):
            validate_labeler(
                _drop(labeler_cfg, ["thresholds", "震仓", "support_break_min"])
            )

    def test_missing_volume_threshold_rejected(self, labeler_cfg):
        with pytest.raises(ConfigError, match="成交量方向"):
            validate_labeler(
                _drop(labeler_cfg, ["thresholds", "狩猎止损", "volume_ratio_min"])
            )

    def test_frozen_boundaries_are_mutually_exclusive(self, labeler_cfg):
        """实测共同命中为 0，靠的就是这两组阈值。"""
        shake = labeler_cfg["thresholds"]["震仓"]
        hunt  = labeler_cfg["thresholds"]["狩猎止损"]
        assert shake["support_break_min"] >= hunt["support_break_max"]
        assert shake["volume_ratio_max"]  <= hunt["volume_ratio_min"]

    def test_equal_boundaries_allowed(self, labeler_cfg):
        """边界相等仍互斥（一边取 >，一边取 ≤）。"""
        c = _mutate(labeler_cfg, ["thresholds", "震仓", "support_break_min"], -0.005)
        validate_labeler(c)


# ===========================================================================
# labeler.yaml — 阈值结构
# ===========================================================================

class TestLabelerThresholds:

    def test_all_six_behaviors_present(self, labeler_cfg):
        assert set(labeler_cfg["thresholds"].keys()) == set(BEHAVIORS)

    def test_missing_behavior_rejected(self, labeler_cfg):
        with pytest.raises(ConfigError, match="缺少键"):
            validate_labeler(_drop(labeler_cfg, ["thresholds", "拉升"]))

    def test_unknown_behavior_rejected(self, labeler_cfg):
        """
        「点火」「派发」「砸盘」是 ADR-0007 原文的旧枚举。它们混进来会让
        标注器输出喂不进 W 矩阵。
        """
        with pytest.raises(ConfigError, match="未知键"):
            validate_labeler(
                _mutate(labeler_cfg, ["thresholds", "派发"], {"return_1d_max": 0.0})
            )

    def test_non_numeric_threshold_rejected(self, labeler_cfg):
        with pytest.raises(ConfigError, match="不是数字"):
            validate_labeler(
                _mutate(labeler_cfg, ["thresholds", "拉升", "forward_min"], "5%")
            )

    def test_empty_threshold_block_rejected(self, labeler_cfg):
        with pytest.raises(ConfigError, match="阈值块为空"):
            validate_labeler(_mutate(labeler_cfg, ["thresholds", "观望"], {}))

    def test_inverted_return_range_rejected(self, labeler_cfg):
        """min > max 时区间为空，该标签永不命中且不报错。"""
        with pytest.raises(ConfigError, match="区间为空"):
            validate_labeler(
                _mutate(labeler_cfg, ["thresholds", "建仓", "return_1d_min"], 0.05)
            )


# ===========================================================================
# labeler.yaml — 标签基数与优先级（ADR-0017 四）
# ===========================================================================

class TestCardinalityAndPriority:

    def test_multiple_labels_per_day_rejected(self, labeler_cfg):
        with pytest.raises(ConfigError, match="Dirichlet"):
            validate_labeler(
                _mutate(labeler_cfg, ["cardinality", "labels_per_stock_day"], 2)
            )

    def test_fractional_counts_rejected(self, labeler_cfg):
        with pytest.raises(ConfigError, match="非整数伪计数"):
            validate_labeler(
                _mutate(labeler_cfg, ["cardinality", "allow_fractional_counts"], True)
            )

    def test_priority_covers_all_behaviors(self, labeler_cfg):
        assert set(labeler_cfg["priority"]) == set(BEHAVIORS)

    def test_priority_missing_label_rejected(self, labeler_cfg):
        short = [b for b in labeler_cfg["priority"] if b != "出货"]
        with pytest.raises(ConfigError, match="缺少键"):
            validate_labeler(_mutate(labeler_cfg, ["priority"], short))

    def test_duplicate_priority_rejected(self, labeler_cfg):
        dupe = labeler_cfg["priority"] + ["观望"]
        with pytest.raises(ConfigError, match="重复标签"):
            validate_labeler(_mutate(labeler_cfg, ["priority"], dupe))

    def test_watch_must_be_last(self, labeler_cfg):
        """观望排前面会吃掉所有更特异的活动信号。"""
        reordered = ["观望"] + [b for b in labeler_cfg["priority"] if b != "观望"]
        with pytest.raises(ConfigError, match="最后一位必须是"):
            validate_labeler(_mutate(labeler_cfg, ["priority"], reordered))

    def test_frozen_priority_order(self, labeler_cfg):
        assert labeler_cfg["priority"] == [
            "狩猎止损", "震仓", "拉升", "出货", "建仓", "观望"
        ]


# ===========================================================================
# labeler.yaml — 无标签日（ADR-0017 五）
# ===========================================================================

class TestUnlabeledPolicy:

    def test_fallback_to_watch_rejected(self, labeler_cfg):
        """实测兜底会让观望涨 4.56 倍，观望列的解释力直接归零。"""
        with pytest.raises(ConfigError, match="4.56 倍"):
            validate_labeler(
                _mutate(labeler_cfg, ["unlabeled", "fallback_to_watch"], True)
            )

    def test_updating_w_counts_rejected(self, labeler_cfg):
        with pytest.raises(ConfigError, match="没有标签可计数"):
            validate_labeler(
                _mutate(labeler_cfg, ["unlabeled", "update_w_counts"], True)
            )

    def test_not_persisting_features_rejected(self, labeler_cfg):
        """不存特征则新版本规则无法回溯重标，73% 的样本永久丢失。"""
        with pytest.raises(ConfigError, match="无法回溯重标"):
            validate_labeler(
                _mutate(labeler_cfg, ["unlabeled", "persist_row_features"], False)
            )

    def test_frozen_unlabeled_policy(self, labeler_cfg):
        unl = labeler_cfg["unlabeled"]
        assert unl["fallback_to_watch"] is False
        assert unl["update_w_counts"] is False
        assert unl["persist_row_features"] is True


# ===========================================================================
# labeler.yaml — 覆盖率监控（ADR-0017 六）
# ===========================================================================

class TestCoverageMonitor:

    def test_global_pool_rejected(self, labeler_cfg):
        """板块覆盖率 19.64%~39.07%，只看全局均值会掩盖单板块退化。"""
        with pytest.raises(ConfigError, match="掩盖单板块退化"):
            validate_labeler(
                _mutate(labeler_cfg, ["coverage_monitor", "pool"], "global")
            )

    def test_threshold_as_percent_rejected(self, labeler_cfg):
        with pytest.raises(ConfigError, match="开区间"):
            validate_labeler(
                _mutate(labeler_cfg, ["coverage_monitor", "alert_threshold"], 20)
            )

    def test_non_int_window_rejected(self, labeler_cfg):
        with pytest.raises(ConfigError, match="≥1 的整数"):
            validate_labeler(
                _mutate(labeler_cfg, ["coverage_monitor", "window_trading_days"], 60.5)
            )

    def test_frozen_monitor_settings(self, labeler_cfg):
        cov = labeler_cfg["coverage_monitor"]
        assert cov["window_trading_days"] == 60
        assert cov["alert_threshold"] == 0.20
        assert cov["consecutive_windows_to_escalate"] == 2
        assert cov["pool"] == "per_sector"

    def test_pool_size_recorded(self, labeler_cfg):
        """
        20% 阈值绑定「每板块约 5 只股票」。生产池变化后必须重新推导，
        所以这个数字必须显式记在配置里而不是只写在报告中。
        """
        assert labeler_cfg["coverage_monitor"]["derived_from_pool_size"] == 5


# ===========================================================================
# labeler.yaml — 规则哈希与版本
# ===========================================================================

class TestRuleHashAndVersion:

    def test_hash_must_be_stored_with_labels(self, labeler_cfg):
        with pytest.raises(ConfigError, match="审计链断裂"):
            validate_labeler(
                _mutate(labeler_cfg, ["rule_hash", "store_with_every_label"], False)
            )

    def test_version_must_be_int(self, labeler_cfg):
        with pytest.raises(ConfigError, match="必须是整数"):
            validate_labeler(_mutate(labeler_cfg, ["version"], "1"))

    def test_missing_top_key_rejected(self, labeler_cfg):
        with pytest.raises(ConfigError, match="缺少键"):
            validate_labeler(_drop(labeler_cfg, ["priority"]))

    def test_unknown_top_key_rejected(self, labeler_cfg):
        with pytest.raises(ConfigError, match="未知键"):
            validate_labeler(_mutate(labeler_cfg, ["extra_rules"], {}))


# ===========================================================================
# 跨文件一致性
# ===========================================================================

class TestCrossConfigConsistency:

    def test_validate_all_passes(self):
        validate_all()

    def test_validate_all_accepts_explicit_dir(self):
        validate_all(CONFIG_DIR)

    def test_labeler_behaviors_match_hmm_w_matrix(self, labeler_cfg):
        """
        标注器的标签必须与 hmm_prior.yaml 的 W 矩阵行为列完全一致，
        否则标注器的输出喂不进 W。这是 ADR-0007 旧枚举埋下的坑。
        ADR-0018 后参与者从三方压缩为二方，取任一参与者的行为键即可。
        """
        hmm = _load(CONFIG_DIR / "hmm_prior.yaml")
        w_behaviors = {
            k for k in hmm["behavior_mapping"]["冰点"]["主力"]
            if k != "alpha"
        }
        assert set(labeler_cfg["thresholds"].keys()) == w_behaviors
        assert set(labeler_cfg["priority"]) == w_behaviors

    def test_signals_and_labeler_versions_are_ints(self, signals_cfg, labeler_cfg):
        assert isinstance(signals_cfg["version"], int)
        assert isinstance(labeler_cfg["version"], int)
