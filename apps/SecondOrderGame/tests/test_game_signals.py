from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from statistics import fmean, pstdev

import pytest
import yaml

from src.data.fake_client import FakeMarketDataSource
from src.data.models import Bar
from src.probability.models import DecisionPoint, InsufficientData, ProbabilityType
from src.signals.game_signals import (
    GameSignalCalculator,
    GameSignalConfig,
    GameSignalRequest,
    GameSignalSnapshot,
    load_game_signal_config,
)


CONFIG_PATH = Path(__file__).parent.parent / "config" / "signals.yaml"
CODE = "SZ.000001"
START = "2026-06-01"
END = "2026-08-10"


def _bar(
    index: int,
    *,
    close: float,
    volume: int = 100,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
) -> Bar:
    timestamp = datetime(2026, 6, 1, 11, 30) + timedelta(hours=2 * index)
    return Bar(
        time_key=timestamp.isoformat(sep=" "),
        open=close - 0.1 if open_ is None else open_,
        high=close + 0.5 if high is None else high,
        low=close - 0.5 if low is None else low,
        close=close,
        volume=volume,
        turnover=close * volume,
    )


def _calculate(
    bars: list[Bar],
    config: GameSignalConfig | None = None,
):
    source = FakeMarketDataSource(
        kline_data={(CODE, "K_120M", START, END): bars}
    )
    calculator = GameSignalCalculator(
        source,
        config or load_game_signal_config(CONFIG_PATH),
    )
    return calculator.calculate(
        GameSignalRequest(
            code=CODE,
            start=START,
            end=END,
            decision_point=DecisionPoint.CLOSE,
        )
    )


def test_nash_band_uses_configured_bar_period_and_standard_deviation_multiplier() -> None:
    bars = [_bar(index, close=float(index + 1)) for index in range(36)]
    config = load_game_signal_config(CONFIG_PATH)

    result = _calculate(bars, config)

    assert isinstance(result, GameSignalSnapshot)
    period = config.values["nash"]["period"]
    deviation = config.values["nash"]["deviation"]
    configured_closes = [bar.close for bar in bars[-period:]]
    expected_center = fmean(configured_closes)
    expected_half_width = pstdev(configured_closes) * deviation
    assert result.nash.center == pytest.approx(expected_center)
    assert result.nash.upper == pytest.approx(expected_center + expected_half_width)
    assert result.nash.lower == pytest.approx(expected_center - expected_half_width)
    if result.nash.position == "inside":
        assert result.nash.lower <= bars[-1].close <= result.nash.upper
    elif result.nash.position == "above":
        assert bars[-1].close > result.nash.upper
    else:
        assert bars[-1].close < result.nash.lower


def test_herd_and_institutional_observations_use_configured_volume_and_momentum_rules() -> None:
    bars = [_bar(index, close=float(index + 1)) for index in range(39)]
    bars.append(
        _bar(
            39,
            close=50.0,
            open_=49.8,
            high=50.1,
            low=49.1,
            volume=1_000,
        )
    )

    result = _calculate(bars)

    assert isinstance(result, GameSignalSnapshot)
    assert result.herd.rsi == pytest.approx(100.0)
    assert result.herd.volume_moving_average == pytest.approx(190.0)
    assert result.herd.momentum == pytest.approx(20.0)
    assert result.herd.momentum_moving_average == pytest.approx(11.0)
    assert result.herd.volume_spike is True
    assert result.herd.buying is True
    assert result.herd.selling is False
    assert result.smart_money.value == pytest.approx(200.0)
    assert result.smart_money.moving_average == pytest.approx(29.0)
    assert result.smart_money.positive is True
    assert result.institutional_flow.institutional_volume is True
    assert result.institutional_flow.accumulation is True
    assert result.institutional_flow.distribution is False


def test_upper_liquidity_trap_uses_high_price_psychological_bracket_and_null_thresholds() -> None:
    bars = [
        _bar(
            index,
            close=110.0,
            open_=110.0,
            high=119.99,
            low=109.5,
        )
        for index in range(39)
    ]
    bars.append(
        _bar(
            39,
            close=119.98,
            open_=119.97,
            high=120.0001,
            low=119.0,
            volume=1_000,
        )
    )

    result = _calculate(bars)

    assert isinstance(result, GameSignalSnapshot)
    assert result.liquidity_trap.recent_high == pytest.approx(119.99)
    assert result.liquidity_trap.upper_psychological_level == pytest.approx(120.0)
    assert result.liquidity_trap.upper is True
    assert result.liquidity_trap.lower is False
    assert result.features.contrarian_sell is True


def test_configured_window_shortage_returns_explicit_insufficient_data() -> None:
    result = _calculate([_bar(index, close=100.0) for index in range(28)])

    assert isinstance(result, InsufficientData)
    assert result.probability_type is ProbabilityType.BEHAVIOR
    assert result.outcome == "game_signal_observations"
    assert "29 required" in result.reason
    assert "probability" not in result.to_dict()


def test_chart_series_calculates_the_latest_twenty_bars_without_future_data() -> None:
    bars = [_bar(index, close=100.0 + index / 10) for index in range(48)]
    source = FakeMarketDataSource(
        kline_data={(CODE, "K_120M", START, END): bars}
    )
    calculator = GameSignalCalculator(source, load_game_signal_config(CONFIG_PATH))
    request = GameSignalRequest(
        code=CODE,
        start=START,
        end=END,
        decision_point=DecisionPoint.CLOSE,
    )

    points = calculator.calculate_series(request)

    assert len(points) == 20
    assert all(point.status == "available" for point in points)
    assert points[0].bar_time == bars[28].time_key
    assert points[-1].bar_time == bars[-1].time_key
    assert points[-1].signal == calculator.calculate(request)

    future_changed = list(bars)
    future_changed[29:] = [
        _bar(index, close=500.0 + index) for index in range(29, len(bars))
    ]
    changed_source = FakeMarketDataSource(
        kline_data={(CODE, "K_120M", START, END): future_changed}
    )
    changed_points = GameSignalCalculator(
        changed_source, load_game_signal_config(CONFIG_PATH)
    ).calculate_series(request)
    assert changed_points[0] == points[0]


def test_chart_series_keeps_unavailable_points_aligned_to_their_bars() -> None:
    bars = [_bar(index, close=100.0 + index / 10) for index in range(30)]
    source = FakeMarketDataSource(
        kline_data={(CODE, "K_120M", START, END): bars}
    )
    calculator = GameSignalCalculator(source, load_game_signal_config(CONFIG_PATH))

    points = calculator.calculate_series(
        GameSignalRequest(CODE, START, END, DecisionPoint.CLOSE)
    )

    assert len(points) == 20
    assert points[0].bar_time == bars[10].time_key
    assert points[0].status == "insufficient_data"
    assert points[-1].status == "available"
    assert points[0].to_dict()["signal"] is None


def test_zero_range_current_bar_returns_insufficient_data_without_carrying_values() -> None:
    bars = [_bar(index, close=100.0 + index / 10) for index in range(39)]
    bars.append(
        _bar(
            39,
            close=110.0,
            open_=110.0,
            high=110.0,
            low=110.0,
            volume=1_000,
        )
    )

    result = _calculate(bars)

    assert isinstance(result, InsufficientData)
    assert "zero-range" in result.reason
    assert "probability" not in result.to_dict()


def test_available_payload_is_observation_only_and_contains_no_position_or_probability() -> None:
    result = _calculate([_bar(index, close=100.0 + index / 10) for index in range(40)])

    assert isinstance(result, GameSignalSnapshot)
    payload = result.to_dict()
    assert payload["role"] == "observation_feature"
    assert payload["notice"] == "专家先验推演，非统计估计"
    assert payload["decision_point"] == "收盘"
    assert set(payload["features"]) == {
        "contrarian_buy",
        "contrarian_sell",
        "momentum_buy",
        "momentum_sell",
        "nash_reversion_buy",
        "nash_reversion_sell",
    }
    serialized_keys = repr(payload)
    assert "position_size" not in serialized_keys
    assert "probability" not in serialized_keys
    assert "composite_signal" not in serialized_keys


def test_lower_liquidity_trap_is_detected_and_no_longer_drives_contrarian_buy() -> None:
    bars = [
        _bar(
            index,
            close=20.0,
            open_=20.0,
            high=20.5,
            low=10.01,
        )
        for index in range(39)
    ]
    bars.append(
        _bar(
            39,
            close=10.02,
            open_=10.03,
            high=10.1,
            low=9.9999,
            volume=1_000,
        )
    )

    result = _calculate(bars)

    assert isinstance(result, GameSignalSnapshot)
    assert result.liquidity_trap.lower_psychological_level == pytest.approx(10.0)
    assert result.liquidity_trap.lower is True
    assert result.herd.selling is True
    # ADR-0026：trap_down 重定义为看跌，不再参与 contrarian_buy（其公式现为
    # herd_selling AND accumulation，本场景 accumulation 为 False）
    assert result.features.contrarian_buy is False


@pytest.mark.parametrize(
    ("close", "open_", "high", "low", "expected_feature"),
    [
        (90.0, 89.0, 90.1, 88.9, "momentum_buy"),
        (110.0, 111.0, 111.1, 109.9, "momentum_sell"),
    ],
)
def test_momentum_observations_combine_nash_location_and_smart_money_direction(
    close: float,
    open_: float,
    high: float,
    low: float,
    expected_feature: str,
) -> None:
    bars = [
        _bar(index, close=100.0, open_=100.0, high=100.5, low=99.5)
        for index in range(39)
    ]
    bars.append(
        _bar(39, close=close, open_=open_, high=high, low=low)
    )

    result = _calculate(bars)

    assert isinstance(result, GameSignalSnapshot)
    assert getattr(result.features, expected_feature) is True


@pytest.mark.parametrize(
    ("previous_close", "close", "expected_feature"),
    [
        (89.0, 90.0, "nash_reversion_buy"),
        (111.0, 110.0, "nash_reversion_sell"),
    ],
)
def test_nash_reversion_keeps_the_configured_one_times_average_volume_condition(
    previous_close: float,
    close: float,
    expected_feature: str,
) -> None:
    config_values = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    config_values["nash"]["deviation"] = 1.0
    config = GameSignalConfig.from_mapping(config_values)
    bars = [_bar(index, close=100.0) for index in range(38)]
    bars.append(_bar(38, close=previous_close))
    bars.append(_bar(39, close=close, volume=200))

    result = _calculate(bars, config)

    assert isinstance(result, GameSignalSnapshot)
    assert result.herd.volume_spike is False
    assert getattr(result.features, expected_feature) is True


def test_institutional_distribution_uses_cumulative_ad_not_only_current_bar_flow() -> None:
    bars = [_bar(index, close=50.0) for index in range(30)]
    bars.extend(
        _bar(
            index,
            close=49.5,
            open_=49.5,
            high=50.5,
            low=49.5,
        )
        for index in range(30, 39)
    )
    bars.append(
        _bar(
            39,
            close=50.025,
            open_=50.0,
            high=50.5,
            low=49.5,
            volume=1_000,
        )
    )

    result = _calculate(bars)

    assert isinstance(result, GameSignalSnapshot)
    assert result.institutional_flow.institutional_volume is True
    assert result.institutional_flow.accumulation is False
    assert result.institutional_flow.distribution is True
