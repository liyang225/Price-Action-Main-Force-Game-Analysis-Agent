from __future__ import annotations

import os
from datetime import date

import pytest

from research_harness import (
    DataConfig,
    Instrument,
    OutputConfig,
    ResearchConfig,
    Rule,
    load_rule_expression,
    measure_history_depth,
)
from research_harness.futu_provider import FutuHistoryProvider


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_FUTU_LIVE_TESTS") != "1",
    reason="set RUN_FUTU_LIVE_TESTS=1 with a logged-in OpenD to run live tests",
)


def test_live_futu_measures_all_four_instrument_period_combinations():
    provider = FutuHistoryProvider()
    config = ResearchConfig(
        version=1,
        data=DataConfig(
            provider="futu",
            provider_options={},
            start=date.fromisoformat(os.environ.get("FUTU_DEPTH_START", "2000-01-01")),
            end=date.today(),
            period="day",
            instruments=(
                Instrument(os.environ.get("FUTU_TEST_CODE", "SH.600000"), "stock"),
                Instrument(os.environ.get("FUTU_TEST_SECTOR", "SH.LIST0002"), "sector_index"),
            ),
        ),
        rules=(
            Rule(
                label="unused",
                when=load_rule_expression({"field": "close", "op": "gt", "value": 0}),
            ),
        ),
        output=OutputConfig(),
    )
    try:
        report = measure_history_depth(config, provider)
    finally:
        provider.close()

    assert [(entry.kind, entry.period) for entry in report.entries] == [
        ("stock", "day"),
        ("stock", "120m"),
        ("sector_index", "day"),
        ("sector_index", "120m"),
    ]
    assert all(entry.earliest is not None for entry in report.entries)
    assert all(entry.row_count > 0 for entry in report.entries)
