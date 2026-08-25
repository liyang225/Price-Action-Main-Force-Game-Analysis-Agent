from __future__ import annotations

from datetime import date

import pytest

from research_harness import HistoryProviderError, HistoryRequest
from research_harness.futu_provider import FutuHistoryProvider


class FakeFrame:
    def __init__(self, rows):
        self.rows = rows

    def to_dict(self, orient):
        assert orient == "records"
        return self.rows


class FakeFutuApi:
    RET_OK = 0

    class KLType:
        K_DAY = "K_DAY"
        K_120M = "K_120M"

    class AuType:
        QFQ = "QFQ"

    class KL_FIELD:
        ALL = "ALL"


class PaginatedQuoteContext:
    def __init__(self):
        self.calls = []

    def request_history_kline(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["page_req_key"] is None:
            return (
                0,
                FakeFrame(
                    [
                        {
                            "code": "SH.600000",
                            "time_key": "2024-01-02 00:00:00",
                            "open": 10,
                            "high": 11,
                            "low": 9,
                            "close": 10.5,
                            "volume": 1000,
                        }
                    ]
                ),
                "next-page",
            )
        return (
            0,
            FakeFrame(
                [
                    {
                        "code": "SH.600000",
                        "time_key": "2024-01-03 00:00:00",
                        "open": 10.5,
                        "high": 12,
                        "low": 10,
                        "close": 11.5,
                        "volume": 1200,
                    }
                ]
            ),
            None,
        )


@pytest.mark.parametrize(
    ("period", "expected_ktype"),
    [("day", "K_DAY"), ("120m", "K_120M")],
)
def test_futu_provider_maps_periods_and_consumes_every_page(period, expected_ktype):
    context = PaginatedQuoteContext()
    provider = FutuHistoryProvider(
        quote_context=context,
        futu_api=FakeFutuApi,
        max_count=500,
    )
    request = HistoryRequest(
        code="SH.600000",
        kind="stock",
        period=period,
        start=date(2024, 1, 2),
        end=date(2024, 1, 3),
    )

    rows = list(provider.fetch_history(request))

    assert [row["trading_date"] for row in rows] == [
        date(2024, 1, 2),
        date(2024, 1, 3),
    ]
    assert [call["page_req_key"] for call in context.calls] == [None, "next-page"]
    assert all(call["ktype"] == expected_ktype for call in context.calls)
    assert all(call["max_count"] == 500 for call in context.calls)
    assert all(call["fields"] == ["ALL"] for call in context.calls)
    assert provider.last_page_count == 2


def test_futu_provider_surfaces_api_errors_with_request_context():
    class FailedContext:
        def request_history_kline(self, **kwargs):
            return 1, "no permission", None

    provider = FutuHistoryProvider(
        quote_context=FailedContext(),
        futu_api=FakeFutuApi,
    )

    with pytest.raises(HistoryProviderError, match="SH.600000.*no permission"):
        list(
            provider.fetch_history(
                HistoryRequest(
                    code="SH.600000",
                    kind="stock",
                    period="day",
                    start=date(2024, 1, 2),
                    end=date(2024, 1, 3),
                )
            )
        )
