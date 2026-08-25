from __future__ import annotations

from datetime import date

from research_harness import (
    DataConfig,
    Instrument,
    OutputConfig,
    ResearchConfig,
    Rule,
    load_rule_expression,
    measure_history_depth,
)


class DepthProvider:
    def __init__(self):
        self.requests = []

    def fetch_history(self, request):
        self.requests.append(request)
        if request.period == "day":
            return [
                {"code": request.code, "time_key": "2020-01-02 00:00:00"},
                {"code": request.code, "time_key": "2024-01-02 00:00:00"},
            ]
        return [
            {"code": request.code, "time_key": "2023-01-03 11:30:00"},
            {"code": request.code, "time_key": "2023-01-03 15:00:00"},
        ]


def _config():
    return ResearchConfig(
        version=1,
        data=DataConfig(
            provider="memory",
            provider_options={},
            start=date(1990, 1, 1),
            end=date(2024, 1, 31),
            period="day",
            instruments=(
                Instrument("SH.600000", "stock"),
                Instrument("SH.BK0001", "sector_index"),
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


def test_depth_report_covers_stock_and_sector_for_both_supported_periods():
    provider = DepthProvider()
    ticks = iter([0.0, 0.25, 1.0, 1.5, 2.0, 2.75, 3.0, 4.0])

    report = measure_history_depth(
        _config(),
        provider,
        periods=("day", "120m"),
        clock=lambda: next(ticks),
    )

    assert [(entry.kind, entry.period) for entry in report.entries] == [
        ("stock", "day"),
        ("stock", "120m"),
        ("sector_index", "day"),
        ("sector_index", "120m"),
    ]
    assert report.entries[0].earliest == date(2020, 1, 2)
    assert report.entries[0].latest == date(2024, 1, 2)
    assert report.entries[0].row_count == 2
    assert report.entries[0].trading_day_count == 2
    assert report.entries[0].elapsed_seconds == 0.25
    assert report.entries[0].page_count is None
    assert report.entries[0].error is None
    assert report.entries[1].trading_day_count == 1
    assert "SH.BK0001" in report.to_json()
    assert '"page_count": null' in report.to_json()
    assert (
        "| SH.600000 | stock | day | 2020-01-02 | 2024-01-02 | 2 | 2 | 未知 | 0.250 |  |"
        in report.to_markdown()
    )


def test_depth_report_keeps_other_combinations_when_one_request_fails():
    class PartiallyFailingProvider(DepthProvider):
        last_page_count = None

        def fetch_history(self, request):
            self.last_page_count = 1
            if request.code == "SH.600000" and request.period == "120m":
                raise RuntimeError("permission denied")
            return super().fetch_history(request)

    report = measure_history_depth(_config(), PartiallyFailingProvider())

    assert len(report.entries) == 4
    failed = report.entries[1]
    assert failed.code == "SH.600000"
    assert failed.period == "120m"
    assert failed.page_count == 1
    assert failed.error == "permission denied"
    assert failed.row_count == 0
    assert report.entries[3].row_count == 2
