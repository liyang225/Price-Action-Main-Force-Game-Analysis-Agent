"""Public behavior tests for the T+1 first-passage estimator."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from time import perf_counter

import pytest

from src.data.fake_client import FakeMarketDataSource
from src.data.models import Bar
from src.probability import DecisionPoint, InsufficientData, ProbabilityResult
from src.probability.t1_first_passage import (
    ConditionCell,
    ConditionDimension,
    ConditionedFirstPassageSample,
    T1FirstPassageConfig,
    T1FirstPassageEstimator,
    T1FirstPassageRequest,
)


def _config(*, min_samples: int = 3) -> T1FirstPassageConfig:
    return T1FirstPassageConfig(
        min_samples=min_samples,
        volatility_lookback=2,
        recent_return_lookback=2,
        volatility_quantiles=(1 / 3, 2 / 3),
        turnover_quantiles=(1 / 3, 2 / 3),
        recent_return_edges=(-0.01, 0.01),
        degradation_order=(
            ConditionDimension.RECENT_RETURN,
            ConditionDimension.CYCLE_STATE,
            ConditionDimension.TURNOVER_QUANTILE,
        ),
        bootstrap_iterations=200,
        random_seed=20260811,
        config_version=7,
    )


def _bar(
    day: str,
    session: str,
    *,
    open_: float = 100.0,
    high: float = 101.0,
    low: float = 99.0,
    close: float = 100.0,
    turnover: float = 1_000.0,
) -> Bar:
    return Bar(
        f"{day} {session}:00",
        open_,
        high,
        low,
        close,
        100,
        turnover,
    )


def _condition(cycle_state: str | None = "启动") -> ConditionCell:
    return ConditionCell(
        volatility_quantile="medium",
        recent_return="flat",
        turnover_quantile="medium",
        cycle_state=cycle_state,
    )


def test_condition_cell_rejects_unknown_domain_values() -> None:
    with pytest.raises(ValueError):
        ConditionCell("extreme", "flat", "medium", "启动")
    with pytest.raises(ValueError):
        ConditionCell("medium", "flat", "medium", "狂热")


def test_complete_condition_cell_estimates_target_and_stop_first() -> None:
    samples = (
        ConditionedFirstPassageSample(
            day="2026-08-01",
            condition=_condition(),
            reference_price=100.0,
            passage_bars=(
                _bar("2026-08-02", "11:30", high=106.0, low=99.0),
                _bar("2026-08-02", "15:00", high=107.0, low=94.0),
            ),
        ),
        ConditionedFirstPassageSample(
            day="2026-08-02",
            condition=_condition(),
            reference_price=100.0,
            passage_bars=(
                _bar("2026-08-03", "11:30", high=101.0, low=94.0),
                _bar("2026-08-03", "15:00", high=106.0, low=93.0),
            ),
        ),
        ConditionedFirstPassageSample(
            day="2026-08-03",
            condition=_condition(),
            reference_price=100.0,
            passage_bars=(
                _bar("2026-08-04", "11:30", high=102.0, low=98.0),
                _bar("2026-08-04", "15:00", high=103.0, low=97.0),
            ),
        ),
    )
    estimator = T1FirstPassageEstimator(FakeMarketDataSource(), _config())

    result = estimator.estimate_samples(
        samples,
        current_condition=_condition(),
        target_return=0.05,
        stop_loss_return=-0.05,
        decision_point=DecisionPoint.CLOSE,
    )

    assert isinstance(result.target_first, ProbabilityResult)
    assert isinstance(result.stop_first, ProbabilityResult)
    assert result.target_first.probability > 0
    assert result.stop_first.probability > 0
    assert result.target_first.prior_weight == 0.0
    assert result.sample_count == 3
    assert result.condition_dimensions == (
        "volatility_quantile",
        "recent_return",
        "turnover_quantile",
        "cycle_state",
    )
    assert result.dropped_dimensions == ()


def test_same_bar_ambiguity_is_resolved_conservatively_as_stop_first() -> None:
    same_bar_samples = tuple(
        ConditionedFirstPassageSample(
            day=f"2026-08-0{index}",
            condition=_condition(),
            reference_price=100.0,
            passage_bars=(
                _bar(f"2026-08-0{index}", "15:00", high=106.0, low=94.0),
            ),
        )
        for index in range(1, 4)
    )
    estimator = T1FirstPassageEstimator(FakeMarketDataSource(), _config())

    result = estimator.estimate_samples(
        same_bar_samples,
        current_condition=_condition(),
        target_return=0.05,
        stop_loss_return=-0.05,
        decision_point=DecisionPoint.MIDDAY,
    )

    assert result.target_first.probability == 0.0
    assert result.stop_first.probability == 1.0
    assert result.same_bar_rule == "stop_loss_first"
    assert result.target_first.data_source.endswith(":intraday_next_bar")


def test_short_plan_treats_lower_target_and_upper_stop_as_valid_levels() -> None:
    samples = (
        ConditionedFirstPassageSample(
            day=f"2026-08-0{index}",
            condition=_condition(),
            reference_price=100.0,
            passage_bars=(
                _bar(
                    f"2026-08-0{index}",
                    "15:00",
                    high=101.0 if index < 3 else 106.0,
                    low=94.0 if index < 3 else 99.0,
                ),
            ),
        )
        for index in range(1, 4)
    )
    estimator = T1FirstPassageEstimator(FakeMarketDataSource(), _config())

    result = estimator.estimate_samples(
        samples,
        current_condition=_condition(),
        target_return=-0.05,
        stop_loss_return=0.05,
        decision_point=DecisionPoint.MIDDAY,
    )

    assert isinstance(result.target_first, ProbabilityResult)
    assert result.target_first.probability > result.stop_first.probability


def test_sample_shortage_degrades_in_configured_order() -> None:
    full = _condition("高潮")
    samples = (
        ConditionedFirstPassageSample(
            day="2026-08-01",
            condition=full,
            reference_price=100.0,
            passage_bars=(
                _bar("2026-08-02", "11:30", high=106.0),
                _bar("2026-08-02", "15:00"),
            ),
        ),
        ConditionedFirstPassageSample(
            day="2026-08-02",
            condition=ConditionCell("medium", "positive", "medium", "高潮"),
            reference_price=100.0,
            passage_bars=(
                _bar("2026-08-03", "11:30", high=106.0),
                _bar("2026-08-03", "15:00"),
            ),
        ),
        ConditionedFirstPassageSample(
            day="2026-08-03",
            condition=ConditionCell("medium", "negative", "medium", "高潮"),
            reference_price=100.0,
            passage_bars=(
                _bar("2026-08-04", "11:30", high=106.0),
                _bar("2026-08-04", "15:00"),
            ),
        ),
    )
    estimator = T1FirstPassageEstimator(FakeMarketDataSource(), _config())

    result = estimator.estimate_samples(
        samples,
        current_condition=full,
        target_return=0.05,
        stop_loss_return=-0.05,
        decision_point=DecisionPoint.CLOSE,
    )

    assert result.sample_count == 3
    assert result.dropped_dimensions == ("recent_return",)
    assert result.condition_dimensions == (
        "volatility_quantile",
        "turnover_quantile",
        "cycle_state",
    )


def test_still_insufficient_after_all_degradation_returns_no_probability() -> None:
    estimator = T1FirstPassageEstimator(FakeMarketDataSource(), _config(min_samples=4))
    samples = tuple(
        ConditionedFirstPassageSample(
            day=f"2026-08-0{index}",
            condition=_condition(),
            reference_price=100.0,
            passage_bars=(
                _bar(f"2026-08-1{index}", "11:30"),
                _bar(f"2026-08-1{index}", "15:00"),
            ),
        )
        for index in range(1, 4)
    )

    result = estimator.estimate_samples(
        samples,
        current_condition=_condition(),
        target_return=0.05,
        stop_loss_return=-0.05,
        decision_point=DecisionPoint.CLOSE,
    )

    assert isinstance(result.target_first, InsufficientData)
    assert isinstance(result.stop_first, InsufficientData)
    assert "probability" not in result.target_first.to_dict()
    assert "confidence" not in result.target_first.reason.lower()
    assert "置信度" not in result.target_first.reason


def test_close_rejects_incomplete_next_day_blocks() -> None:
    estimator = T1FirstPassageEstimator(FakeMarketDataSource(), _config())
    incomplete = tuple(
        ConditionedFirstPassageSample(
            day=f"2026-07-0{index}",
            condition=_condition(),
            reference_price=100.0,
            passage_bars=(_bar(f"2026-07-1{index}", "11:30", high=106.0),),
        )
        for index in range(1, 4)
    )

    result = estimator.estimate_samples(
        incomplete,
        current_condition=_condition(),
        target_return=0.05,
        stop_loss_return=-0.05,
        decision_point=DecisionPoint.CLOSE,
    )

    assert isinstance(result.target_first, InsufficientData)
    assert result.sample_count == 0


@pytest.mark.parametrize(
    ("sample_day", "decision_point", "passage_bars"),
    [
        (
            "2026-07-12",
            DecisionPoint.MIDDAY,
            (_bar("2026-07-12", "11:30"),),
        ),
        (
            "2026-07-11",
            DecisionPoint.CLOSE,
            (
                _bar("2026-07-12", "11:30"),
                _bar("2026-07-12", "11:30"),
            ),
        ),
        (
            "2026-07-11",
            DecisionPoint.CLOSE,
            (
                _bar("2026-07-12", "11:30"),
                _bar("2026-07-13", "15:00"),
            ),
        ),
        (
            "2026-07-11",
            DecisionPoint.MIDDAY,
            (_bar("2026-07-12", "15:00"),),
        ),
        (
            "2026-07-12",
            DecisionPoint.CLOSE,
            (
                _bar("2026-07-12", "11:30"),
                _bar("2026-07-12", "15:00"),
            ),
        ),
        (
            "2026-07-13",
            DecisionPoint.CLOSE,
            (
                _bar("2026-07-12", "11:30"),
                _bar("2026-07-12", "15:00"),
            ),
        ),
    ],
)
def test_passage_window_requires_exact_k120_session_identity(
    sample_day: str,
    decision_point: DecisionPoint,
    passage_bars: tuple[Bar, ...],
) -> None:
    estimator = T1FirstPassageEstimator(FakeMarketDataSource(), _config())
    malformed = tuple(
        ConditionedFirstPassageSample(
            day=sample_day,
            condition=_condition(),
            reference_price=100.0,
            passage_bars=passage_bars,
        )
        for _ in range(3)
    )

    result = estimator.estimate_samples(
        malformed,
        current_condition=_condition(),
        target_return=0.05,
        stop_loss_return=-0.05,
        decision_point=decision_point,
    )

    assert isinstance(result.target_first, InsufficientData)
    assert result.sample_count == 0


def test_snapshot_features_must_be_keyed_by_decision_point() -> None:
    estimator = T1FirstPassageEstimator(FakeMarketDataSource(), _config())

    with pytest.raises(ValueError, match="decision_point"):
        estimator.estimate(
            T1FirstPassageRequest(
                code="SZ.000005",
                start="2026-01-01",
                end="2026-01-10",
                target_price=105.0,
                stop_loss_price=95.0,
                decision_point=DecisionPoint.MIDDAY,
                cycle_state_snapshots={"2026-01-10": "启动"},  # type: ignore[dict-item]
                turnover_rate_snapshots={
                    "2026-01-10": 0.02  # type: ignore[dict-item]
                },
            )
        )


def test_malformed_historical_session_cannot_change_later_condition_features() -> None:
    start = date(2026, 4, 1)
    stable_bars: list[Bar] = []
    extreme_bars: list[Bar] = []
    cycle_states: dict[tuple[str, DecisionPoint], str] = {}
    turnover_rates: dict[tuple[str, DecisionPoint], float] = {}
    for offset in range(11):
        day = (start + timedelta(days=offset)).isoformat()
        cycle_states[(day, DecisionPoint.CLOSE)] = "启动"
        turnover_rates[(day, DecisionPoint.CLOSE)] = 0.01 + offset / 10_000
        if offset == 5:
            stable_bars.append(_bar(day, "12:00", close=100.0))
            extreme_bars.append(
                _bar(
                    day,
                    "12:00",
                    open_=1_000.0,
                    high=1_010.0,
                    low=990.0,
                    close=1_000.0,
                    turnover=999_999.0,
                )
            )
            continue
        session = (
            _bar(day, "11:30", close=100.0),
            _bar(day, "15:00", close=100.0),
        )
        stable_bars.extend(session)
        extreme_bars.extend(session)
    key = ("SZ.000006", "K_120M", "2026-04-01", "2026-04-11")
    config = T1FirstPassageConfig(
        **{
            **_config(min_samples=2).as_dict(),
            "degradation_order": tuple(ConditionDimension),
        }
    )
    request = T1FirstPassageRequest(
        code="SZ.000006",
        start="2026-04-01",
        end="2026-04-11",
        target_price=105.0,
        stop_loss_price=95.0,
        decision_point=DecisionPoint.CLOSE,
        cycle_state_snapshots=cycle_states,
        turnover_rate_snapshots=turnover_rates,
    )

    stable = T1FirstPassageEstimator(
        FakeMarketDataSource(kline_data={key: stable_bars}), config
    ).estimate(request)
    extreme = T1FirstPassageEstimator(
        FakeMarketDataSource(kline_data={key: extreme_bars}), config
    ).estimate(request)

    assert stable.to_dict() == extreme.to_dict()


def test_missing_cycle_state_enters_the_defined_degradation_path() -> None:
    samples = tuple(
        ConditionedFirstPassageSample(
            day=f"2026-08-0{index}",
            condition=ConditionCell("medium", "flat", "medium", state),
            reference_price=100.0,
            passage_bars=(
                _bar(f"2026-08-1{index}", "11:30", high=106.0),
                _bar(f"2026-08-1{index}", "15:00"),
            ),
        )
        for index, state in enumerate(("启动", "发酵", "高潮"), start=1)
    )
    config = _config()
    config = T1FirstPassageConfig(
        **{
            **config.as_dict(),
            "degradation_order": (
                ConditionDimension.CYCLE_STATE,
                ConditionDimension.RECENT_RETURN,
                ConditionDimension.TURNOVER_QUANTILE,
            ),
        }
    )
    estimator = T1FirstPassageEstimator(FakeMarketDataSource(), config)

    result = estimator.estimate_samples(
        samples,
        current_condition=_condition(None),
        target_return=0.05,
        stop_loss_return=-0.05,
        decision_point=DecisionPoint.CLOSE,
    )

    assert result.sample_count == 3
    assert result.dropped_dimensions == ("cycle_state",)


def test_source_path_builds_conditions_without_using_later_bars() -> None:
    start = date(2026, 1, 1)
    bars: list[Bar] = []
    cycle_states: dict[tuple[str, DecisionPoint], str] = {}
    turnover_rates: dict[tuple[str, DecisionPoint], float] = {}
    close = 100.0
    for offset in range(12):
        day = (start + timedelta(days=offset)).isoformat()
        cycle_states[(day, DecisionPoint.CLOSE)] = "启动"
        turnover_rates[(day, DecisionPoint.CLOSE)] = 0.01 + offset / 10_000
        close *= 1.002
        bars.extend(
            (
                _bar(day, "11:30", open_=close, high=close * 1.01, low=close * 0.99, close=close, turnover=1_000 + offset),
                _bar(day, "15:00", open_=close, high=close * 1.02, low=close * 0.99, close=close * 1.001, turnover=1_100 + offset),
            )
        )
    key = ("SZ.000001", "K_120M", "2026-01-01", "2026-01-12")
    source = FakeMarketDataSource(kline_data={key: bars})
    config = _config(min_samples=2)
    config = T1FirstPassageConfig(
        **{
            **config.as_dict(),
            "degradation_order": tuple(ConditionDimension),
        }
    )
    estimator = T1FirstPassageEstimator(source, config)

    result = estimator.estimate(
        T1FirstPassageRequest(
            code="SZ.000001",
            start="2026-01-01",
            end="2026-01-12",
            target_price=close * 1.05,
            stop_loss_price=close * 0.95,
            decision_point=DecisionPoint.CLOSE,
            cycle_state_snapshots=cycle_states,
            turnover_rate_snapshots=turnover_rates,
        )
    )

    assert isinstance(result.target_first, ProbabilityResult)
    assert result.sample_count >= 2
    assert result.target_first.data_source.endswith(":overnight_next_bar")


def test_midday_estimate_excludes_the_current_incomplete_afternoon() -> None:
    start = date(2026, 2, 1)
    normal_bars: list[Bar] = []
    extreme_bars: list[Bar] = []
    cycle_states: dict[tuple[str, DecisionPoint], str] = {}
    turnover_rates: dict[tuple[str, DecisionPoint], float] = {}
    close = 100.0
    for offset in range(10):
        day = (start + timedelta(days=offset)).isoformat()
        cycle_states[(day, DecisionPoint.MIDDAY)] = "启动"
        turnover_rates[(day, DecisionPoint.MIDDAY)] = 0.01 + offset / 10_000
        close *= 1.001
        morning = _bar(
            day,
            "11:30",
            open_=close,
            high=close * 1.01,
            low=close * 0.99,
            close=close,
            turnover=1_000 + offset,
        )
        afternoon = _bar(
            day,
            "15:00",
            open_=close,
            high=close * 1.01,
            low=close * 0.99,
            close=close,
            turnover=1_100 + offset,
        )
        normal_bars.extend((morning, afternoon))
        extreme_bars.extend((morning, afternoon))
    extreme_bars = [replace(bar, turnover=bar.turnover * 1_000) for bar in extreme_bars]
    last_day = (start + timedelta(days=9)).isoformat()
    extreme_bars[-1] = _bar(
        last_day,
        "15:00",
        open_=close,
        high=close * 2,
        low=close * 0.5,
        close=close,
        turnover=999_999,
    )
    key = ("SZ.000002", "K_120M", "2026-02-01", "2026-02-10")
    config = T1FirstPassageConfig(
        **{
            **_config(min_samples=2).as_dict(),
            "degradation_order": tuple(ConditionDimension),
        }
    )
    request = T1FirstPassageRequest(
        code="SZ.000002",
        start="2026-02-01",
        end="2026-02-10",
        target_price=close * 1.05,
        stop_loss_price=close * 0.95,
        decision_point=DecisionPoint.MIDDAY,
        cycle_state_snapshots=cycle_states,
        turnover_rate_snapshots=turnover_rates,
    )

    normal = T1FirstPassageEstimator(
        FakeMarketDataSource(kline_data={key: normal_bars}), config
    ).estimate(request)
    extreme = T1FirstPassageEstimator(
        FakeMarketDataSource(kline_data={key: extreme_bars}), config
    ).estimate(request)

    assert normal.to_dict() == extreme.to_dict()


def test_midday_source_uses_same_day_afternoon_without_overnight_gap() -> None:
    start = date(2026, 3, 1)
    bars: list[Bar] = []
    cycle_states: dict[tuple[str, DecisionPoint], str] = {}
    turnover_rates: dict[tuple[str, DecisionPoint], float] = {}
    for offset in range(8):
        day = (start + timedelta(days=offset)).isoformat()
        cycle_states[(day, DecisionPoint.MIDDAY)] = "启动"
        turnover_rates[(day, DecisionPoint.MIDDAY)] = 0.01 + offset / 10_000
        bars.extend(
            (
                _bar(day, "11:30", high=101.0, low=94.0, close=100.0),
                _bar(day, "15:00", high=106.0, low=99.0, close=100.0),
            )
        )
    key = ("SZ.000003", "K_120M", "2026-03-01", "2026-03-08")
    config = T1FirstPassageConfig(
        **{
            **_config(min_samples=2).as_dict(),
            "degradation_order": tuple(ConditionDimension),
        }
    )

    result = T1FirstPassageEstimator(
        FakeMarketDataSource(kline_data={key: bars}), config
    ).estimate(
        T1FirstPassageRequest(
            code="SZ.000003",
            start="2026-03-01",
            end="2026-03-08",
            target_price=105.0,
            stop_loss_price=95.0,
            decision_point=DecisionPoint.MIDDAY,
            cycle_state_snapshots=cycle_states,
            turnover_rate_snapshots=turnover_rates,
        )
    )

    assert result.target_first.probability == 1.0
    assert result.stop_first.probability == 0.0
    assert result.target_first.data_source.endswith(":intraday_next_bar")


def test_stock_source_history_completes_within_three_seconds() -> None:
    start = date(2020, 1, 1)
    bars: list[Bar] = []
    cycle_states: dict[tuple[str, DecisionPoint], str] = {}
    turnover_rates: dict[tuple[str, DecisionPoint], float] = {}
    close = 100.0
    last_day = ""
    for offset in range(2_000):
        last_day = (start + timedelta(days=offset)).isoformat()
        cycle_states[(last_day, DecisionPoint.CLOSE)] = "启动"
        turnover_rates[(last_day, DecisionPoint.CLOSE)] = 0.01 + (offset % 100) / 10_000
        close *= 1.0001
        bars.extend(
            (
                _bar(last_day, "11:30", open_=close, high=close * 1.01, low=close * 0.99, close=close),
                _bar(last_day, "15:00", open_=close, high=close * 1.01, low=close * 0.99, close=close),
            )
        )
    key = ("SZ.000004", "K_120M", "2020-01-01", last_day)
    config = T1FirstPassageConfig(
        **{
            **_config(min_samples=20).as_dict(),
            "degradation_order": tuple(ConditionDimension),
        }
    )
    estimator = T1FirstPassageEstimator(
        FakeMarketDataSource(kline_data={key: bars}), config
    )

    started = perf_counter()
    result = estimator.estimate(
        T1FirstPassageRequest(
            code="SZ.000004",
            start="2020-01-01",
            end=last_day,
            target_price=close * 1.05,
            stop_loss_price=close * 0.95,
            decision_point=DecisionPoint.CLOSE,
            cycle_state_snapshots=cycle_states,
            turnover_rate_snapshots=turnover_rates,
        )
    )
    elapsed = perf_counter() - started

    assert isinstance(result.target_first, ProbabilityResult)
    assert elapsed < 3.0
