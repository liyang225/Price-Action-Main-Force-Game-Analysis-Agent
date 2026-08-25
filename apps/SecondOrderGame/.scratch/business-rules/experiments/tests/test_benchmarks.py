from __future__ import annotations

import pandas as pd

from behavior_study.benchmarks import compare_forward_benchmarks, extreme_market_sample_analysis


def _features() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "forward_stock_return": [-0.08, -0.04, -0.02, 0.01, 0.08],
            "forward_sector_return": [-0.10, -0.02, -0.01, 0.03, 0.05],
            "forward_excess_return": [0.02, -0.04, -0.01, -0.02, 0.03],
            "benchmark_available": [True, True, True, True, False],
        }
    )


def test_compare_reports_absolute_relative_distributions_and_confusion() -> None:
    report = compare_forward_benchmarks(_features(), negative_threshold=-0.03, positive_threshold=0.03)

    assert report["valid_count"] == 4
    assert report["absolute"]["negative"]["count"] == 2
    assert report["relative"]["negative"]["count"] == 1
    assert report["absolute"]["dominant_share"] == 0.5
    assert report["confusion"].loc["negative", "negative"] == 1


def test_extreme_market_analysis_measures_concentration_and_exclusion_cost() -> None:
    report = extreme_market_sample_analysis(
        _features(),
        market_return_threshold=-0.05,
        absolute_threshold=-0.03,
        relative_threshold=-0.03,
    )

    assert report["extreme_count"] == 1
    assert report["extreme_share"] == 0.25
    assert report["absolute"]["negative_share"] == 1.0
    assert report["relative"]["negative_share"] == 0.0
    assert report["excluded_if_absolute_extreme_filter"]["count"] == 1
