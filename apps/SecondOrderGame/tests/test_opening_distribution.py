"""Public behavior tests for B-class next-period distributions."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from time import perf_counter

import pytest

from src.data.fake_client import FakeMarketDataSource
from src.data.models import Bar
from src.probability import (
    DecisionPoint,
    InsufficientData,
    OpeningDistributionConfig,
    OpeningDistributionEstimator,
    OpeningDistributionKind,
    OpeningDistributionResult,
    OpeningRange,
)


CODE = "SZ.000001"
START = "2026-08-03"
END = "2026-08-06"


def _bar(day: str, time: str, *, open_: float, close: float) -> Bar:
    return Bar(
        time_key=f"{day} {time}:00",
        open=open_,
        high=max(open_, close),
        low=min(open_, close),
        close=close,
        volume=100,
        turnover=10_000,
    )


def _config(
    *,
    min_day_blocks: int = 2,
    bootstrap_iterations: int = 32,
    prior_strength: float = 1.0,
) -> OpeningDistributionConfig:
    ranges = (
        OpeningRange("gap_down", None, -0.01),
        OpeningRange("near_reference", -0.01, 0.01),
        OpeningRange("gap_up", 0.01, None),
    )
    uniform_prior = {item.outcome: 1 / 3 for item in ranges}
    return OpeningDistributionConfig(
        ranges=ranges,
        min_day_blocks=min_day_blocks,
        bootstrap_iterations=bootstrap_iterations,
        prior_strength=prior_strength,
        priors={
            OpeningDistributionKind.INTRADAY_NEXT_BAR: uniform_prior,
            OpeningDistributionKind.OVERNIGHT_NEXT_BAR: uniform_prior,
        },
        config_version=7,
        random_seed=19,
    )


def _estimate(
    bars: list[Bar],
    point: DecisionPoint,
    config: OpeningDistributionConfig | None = None,
) -> OpeningDistributionResult:
    source = FakeMarketDataSource(
        kline_data={(CODE, "K_120M", START, END): bars}
    )
    return OpeningDistributionEstimator(source, config or _config()).estimate(
        CODE, point, START, END
    )


def _priors(
    gap_down: float, near_reference: float, gap_up: float
) -> dict[OpeningDistributionKind, dict[str, float]]:
    return {
        kind: {
            "gap_down": gap_down,
            "near_reference": near_reference,
            "gap_up": gap_up,
        }
        for kind in OpeningDistributionKind
    }


def test_midday_and_close_use_independent_opening_distributions() -> None:
    bars: list[Bar] = []
    for day in ("2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06"):
        bars.extend(
            [
                _bar(day, "11:30", open_=110, close=100),
                _bar(day, "15:00", open_=100, close=90),
            ]
        )
    source = FakeMarketDataSource(
        kline_data={(CODE, "K_120M", START, END): bars}
    )
    estimator = OpeningDistributionEstimator(source, _config())

    midday = estimator.estimate(CODE, DecisionPoint.MIDDAY, START, END)
    close = estimator.estimate(CODE, DecisionPoint.CLOSE, START, END)

    assert [item.outcome for item in midday] == [
        "gap_down",
        "near_reference",
        "gap_up",
    ]
    assert sum(item.probability for item in midday) == pytest.approx(1.0)
    assert sum(item.probability for item in close) == pytest.approx(1.0)
    assert midday[0].probability > midday[2].probability
    assert close[2].probability > close[0].probability
    assert {item.prior_weight for item in midday} == {midday[0].prior_weight}
    assert midday[0].prior_weight == pytest.approx(0.2)
    assert {item.prior_weight for item in close} == {close[0].prior_weight}
    assert close[0].prior_weight == pytest.approx(0.25)
    assert {item.data_source for item in midday} == {
        "historical_ohlcv:intraday_next_bar"
    }
    assert {item.data_source for item in close} == {
        "historical_ohlcv:overnight_next_bar"
    }


def test_midday_uses_afternoon_close_instead_of_lunch_reopen() -> None:
    baseline: list[Bar] = []
    changed_open: list[Bar] = []
    changed_close: list[Bar] = []
    for day in ("2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06"):
        morning = _bar(day, "11:30", open_=110, close=100)
        baseline.extend([morning, _bar(day, "15:00", open_=100, close=100)])
        changed_open.extend([morning, _bar(day, "15:00", open_=80, close=100)])
        changed_close.extend([morning, _bar(day, "15:00", open_=100, close=80)])

    assert _estimate(baseline, DecisionPoint.MIDDAY) == _estimate(
        changed_open, DecisionPoint.MIDDAY
    )
    assert _estimate(baseline, DecisionPoint.MIDDAY) != _estimate(
        changed_close, DecisionPoint.MIDDAY
    )


def test_close_uses_next_morning_close_instead_of_open() -> None:
    baseline: list[Bar] = []
    changed_open: list[Bar] = []
    changed_close: list[Bar] = []
    for day in ("2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06"):
        afternoon = _bar(day, "15:00", open_=100, close=100)
        baseline.extend([_bar(day, "11:30", open_=100, close=100), afternoon])
        changed_open.extend([_bar(day, "11:30", open_=120, close=100), afternoon])
        changed_close.extend([_bar(day, "11:30", open_=100, close=120), afternoon])

    assert _estimate(baseline, DecisionPoint.CLOSE) == _estimate(
        changed_open, DecisionPoint.CLOSE
    )
    assert _estimate(baseline, DecisionPoint.CLOSE) != _estimate(
        changed_close, DecisionPoint.CLOSE
    )


def test_each_calendar_year_keeps_its_historical_weight_in_distribution() -> None:
    bars: list[Bar] = []
    for day, afternoon_close in (
        ("2019-12-30", 80),
        ("2020-01-02", 120),
        ("2020-01-03", 120),
        ("2020-01-06", 120),
    ):
        bars.extend(
            [
                _bar(day, "11:30", open_=100, close=100),
                _bar(day, "15:00", open_=100, close=afternoon_close),
            ]
        )
    config = _config(bootstrap_iterations=16, prior_strength=0)

    result = _estimate(bars, DecisionPoint.MIDDAY, config)

    assert not isinstance(result, InsufficientData)
    assert [item.probability for item in result] == pytest.approx([0.25, 0, 0.75])


def test_midday_distribution_does_not_collapse_to_lunch_reopen_price() -> None:
    bars: list[Bar] = []
    first_day = date(2020, 1, 1)
    afternoon_closes = (98.0, 100.0, 102.0)
    for offset in range(120):
        day = (first_day + timedelta(days=offset)).isoformat()
        bars.extend(
            [
                _bar(day, "11:30", open_=100, close=100),
                _bar(
                    day,
                    "15:00",
                    open_=100,
                    close=afternoon_closes[offset % len(afternoon_closes)],
                ),
            ]
        )

    result = _estimate(
        bars,
        DecisionPoint.MIDDAY,
        _config(min_day_blocks=30, bootstrap_iterations=500, prior_strength=3),
    )

    assert not isinstance(result, InsufficientData)
    probabilities = {item.outcome: item.probability for item in result}
    assert probabilities["near_reference"] < 0.5
    assert probabilities["gap_down"] > 0.25
    assert probabilities["gap_up"] > 0.25


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (("", None, 0.01), "non-empty string"),
        (("gap", "invalid", 0.01), "finite number or None"),
        (("gap", float("nan"), 0.01), "finite number or None"),
        (("gap", None, float("inf")), "finite number or None"),
        (("gap", 0.01, 0.01), "less than"),
        (("gap", 0.02, 0.01), "less than"),
    ],
)
def test_invalid_opening_range_is_rejected(
    arguments: tuple[object, object, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        OpeningRange(*arguments)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"ranges": []}, "non-empty tuple"),
        ({"ranges": ()}, "non-empty tuple"),
        (
            {
                "ranges": (
                    OpeningRange("duplicate", None, 0),
                    OpeningRange("duplicate", 0, None),
                )
            },
            "unique",
        ),
        (
            {
                "ranges": (
                    OpeningRange("gap_down", -1, -0.01),
                    OpeningRange("near_reference", -0.01, 0.01),
                    OpeningRange("gap_up", 0.01, None),
                )
            },
            "cover all",
        ),
        (
            {
                "ranges": (
                    OpeningRange("gap_down", None, -0.01),
                    OpeningRange("near_reference", -0.01, 0.01),
                    OpeningRange("gap_up", 0.01, 1),
                )
            },
            "cover all",
        ),
        (
            {
                "ranges": (
                    OpeningRange("gap_down", None, -0.01),
                    OpeningRange("gap_up", 0.01, None),
                )
            },
            "contiguous",
        ),
        (
            {
                "priors": {
                    OpeningDistributionKind.INTRADAY_NEXT_BAR: {
                        "gap_down": 1 / 3,
                        "near_reference": 1 / 3,
                        "gap_up": 1 / 3,
                    }
                }
            },
            "intraday and overnight",
        ),
        (
            {
                "priors": {
                    kind: {"gap_down": 0.5, "gap_up": 0.5}
                    for kind in OpeningDistributionKind
                }
            },
            "match configured ranges",
        ),
        (
            {"priors": _priors(-0.1, 0.5, 0.6)},
            "between 0 and 1",
        ),
        (
            {"priors": _priors(float("nan"), 0.5, 0.5)},
            "between 0 and 1",
        ),
        (
            {"priors": _priors(0.4, 0.4, 0.4)},
            "sum to 1",
        ),
        ({"min_day_blocks": 0}, "positive integer"),
        ({"min_day_blocks": True}, "positive integer"),
        ({"bootstrap_iterations": 0}, "positive integer"),
        ({"prior_strength": -1}, "finite non-negative"),
        ({"prior_strength": float("inf")}, "finite non-negative"),
        ({"config_version": -1}, "non-negative integer"),
        ({"config_version": True}, "non-negative integer"),
        ({"random_seed": 1.5}, "must be an integer"),
    ],
)
def test_invalid_distribution_configuration_is_rejected(
    changes: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        replace(_config(), **changes)


def test_too_few_complete_day_blocks_returns_only_insufficient_data() -> None:
    bars = [
        _bar("2026-08-03", "11:30", open_=100, close=100),
        _bar("2026-08-03", "15:00", open_=100, close=100),
    ]
    source = FakeMarketDataSource(
        kline_data={(CODE, "K_120M", START, END): bars}
    )

    result = OpeningDistributionEstimator(source, _config()).estimate(
        CODE, DecisionPoint.CLOSE, START, END
    )

    assert isinstance(result, InsufficientData)
    assert result.data_source == "historical_ohlcv:overnight_next_bar"
    assert result.decision_point is DecisionPoint.CLOSE
    assert result.config_version == 7
    assert "probability" not in result.to_dict()


def test_overnight_samples_do_not_bridge_an_incomplete_trading_day_block() -> None:
    bars = [
        _bar("2026-08-05", "15:00", open_=100, close=100),
        _bar("2026-08-03", "11:30", open_=100, close=100),
        _bar("2026-08-04", "11:30", open_=150, close=150),
        _bar("2026-08-05", "11:30", open_=200, close=200),
        _bar("2026-08-03", "15:00", open_=100, close=100),
    ]
    source = FakeMarketDataSource(
        kline_data={(CODE, "K_120M", START, END): bars}
    )

    result = OpeningDistributionEstimator(
        source, _config(min_day_blocks=1)
    ).estimate(CODE, DecisionPoint.CLOSE, START, END)

    assert isinstance(result, InsufficientData)
    assert result.reason.startswith("0 complete day blocks available")


def test_large_price_history_returns_within_three_seconds() -> None:
    bars: list[Bar] = []
    first_day = date(2010, 1, 1)
    for offset in range(5_000):
        day = (first_day + timedelta(days=offset)).isoformat()
        bars.extend(
            [
                _bar(day, "11:30", open_=100, close=100),
                _bar(day, "15:00", open_=100, close=100),
            ]
        )
    source = FakeMarketDataSource(
        kline_data={(CODE, "K_120M", START, END): bars}
    )
    estimator = OpeningDistributionEstimator(source, _config())

    started = perf_counter()
    result = estimator.estimate(CODE, DecisionPoint.CLOSE, START, END)
    elapsed = perf_counter() - started

    assert not isinstance(result, InsufficientData)
    assert elapsed < 3.0
