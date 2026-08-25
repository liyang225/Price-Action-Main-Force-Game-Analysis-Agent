from __future__ import annotations

from dataclasses import replace
from datetime import date

from research_harness import (
    DataConfig,
    HistoryRequest,
    Instrument,
    OutputConfig,
    ResearchConfig,
    Rule,
    load_rule_expression,
    replay,
)


class RecordingProvider:
    def __init__(self, rows_by_code):
        self.rows_by_code = rows_by_code
        self.requests: list[HistoryRequest] = []

    def fetch_history(self, request: HistoryRequest):
        self.requests.append(request)
        return list(self.rows_by_code[request.code])


def _config(*rules: Rule) -> ResearchConfig:
    return ResearchConfig(
        version=1,
        data=DataConfig(
            provider="memory",
            provider_options={},
            start=date(2024, 1, 2),
            end=date(2024, 1, 4),
            period="120m",
            instruments=(Instrument(code="SH.600000", kind="stock"),),
        ),
        rules=rules,
        output=OutputConfig(),
    )


def _rule(label: str, field: str, op: str, value) -> Rule:
    return Rule(
        label=label,
        when=load_rule_expression({"field": field, "op": op, "value": value}),
    )


def test_replay_reports_label_shares_conflicts_and_unmatched_by_code_day():
    provider = RecordingProvider(
        {
            "SH.600000": [
                {
                    "code": "SH.600000",
                    "time_key": "2024-01-02 11:30:00",
                    "close": 11,
                    "volume": 900,
                },
                {
                    "code": "SH.600000",
                    "time_key": "2024-01-02 15:00:00",
                    "close": 9,
                    "volume": 1200,
                },
                {
                    "code": "SH.600000",
                    "time_key": "2024-01-03 15:00:00",
                    "close": 12,
                    "volume": 1300,
                },
                {
                    "code": "SH.600000",
                    "time_key": "2024-01-04 15:00:00",
                    "close": 9,
                    "volume": 900,
                },
            ]
        }
    )
    config = _config(
        _rule("价格强", "close", "gt", 10),
        _rule("放量", "volume", "gte", 1000),
    )

    report = replay(config, provider)

    assert provider.requests == [
        HistoryRequest(
            code="SH.600000",
            kind="stock",
            period="120m",
            start=date(2024, 1, 2),
            end=date(2024, 1, 4),
        )
    ]
    assert report.total_code_days == 3
    assert report.total_rows == 4
    assert report.label_counts == {"价格强": 2, "放量": 2}
    assert report.label_shares == {"价格强": 2 / 3, "放量": 2 / 3}
    assert report.multi_label_conflict_count == 2
    assert report.multi_label_conflict_share == 2 / 3
    assert report.conflict_combinations == {"价格强 + 放量": 2}
    assert report.unmatched_count == 1
    assert report.unmatched_share == 1 / 3
    assert [(item.code, item.trading_date, item.labels) for item in report.matches] == [
        ("SH.600000", date(2024, 1, 2), ("价格强", "放量")),
        ("SH.600000", date(2024, 1, 3), ("价格强", "放量")),
        ("SH.600000", date(2024, 1, 4), ()),
    ]


def test_replay_threshold_changes_only_require_a_new_config_value():
    rows = {
        "SH.600000": [
            {
                "code": "SH.600000",
                "trading_date": "2024-01-02",
                "close": 10.5,
            }
        ]
    }
    provider = RecordingProvider(rows)
    loose = _config(_rule("拉升", "close", "gte", 10))
    strict_rule = _rule("拉升", "close", "gte", 11)
    strict = replace(loose, rules=(strict_rule,))

    assert replay(loose, provider).label_counts == {"拉升": 1}
    assert replay(strict, provider).label_counts == {"拉升": 0}


def test_nested_rules_are_evaluated_without_python_expressions():
    provider = RecordingProvider(
        {
            "SH.600000": [
                {
                    "code": "SH.600000",
                    "trading_date": "2024-01-02",
                    "close": 10.5,
                    "volume": 800,
                    "halted": False,
                },
                {
                    "code": "SH.600000",
                    "trading_date": "2024-01-03",
                    "close": None,
                    "volume": 1200,
                    "halted": True,
                },
            ]
        }
    )
    rule = Rule(
        label="candidate",
        when=load_rule_expression(
            {
                "all": [
                    {
                        "any": [
                            {"field": "close", "op": "between", "value": [10, 11]},
                            {"field": "volume", "op": "gt", "value": 1000},
                        ]
                    },
                    {"not": {"field": "halted", "op": "eq", "value": True}},
                ]
            }
        ),
    )

    report = replay(_config(rule), provider)

    assert report.label_counts == {"candidate": 1}
    assert report.unmatched_count == 1


def test_report_json_and_markdown_are_stable_and_explicit_about_denominators():
    provider = RecordingProvider(
        {
            "SH.600000": [
                {
                    "code": "SH.600000",
                    "trading_date": "2024-01-02",
                    "close": 10.5,
                }
            ]
        }
    )
    report = replay(_config(_rule("拉升", "close", "gte", 10)), provider)

    json_text = report.to_json(include_matches=True)
    markdown = report.to_markdown(include_matches=True)

    assert '"total_code_days": 1' in json_text
    assert '"trading_date": "2024-01-02"' in json_text
    assert "| 拉升 | 1 | 100.00% |" in markdown
    assert "统计分母：1 个「标的×交易日」" in markdown
