from __future__ import annotations


from types import SimpleNamespace

from PyQt6.QtWidgets import QFrame


def test_market_summary_does_not_send_volume_to_quote_strip() -> None:
    from pa_agent.gui.main_window import MainWindow

    captured: dict[str, object] = {}

    class FakeStrip:
        def set_summary(self, **values) -> None:
            captured.update(values)

    class FakeSource:
        @staticmethod
        def latest_market_summary():
            return {"last_price": 10.5, "volume": 0.0}

    host = SimpleNamespace(
        _market_summary_strip=FakeStrip(),
        _ctx=SimpleNamespace(data_source=FakeSource()),
    )
    bars = [
        SimpleNamespace(open=10.0, high=11.0, low=9.0, close=10.5, pct_chg=1.0, volume=0.0),
        SimpleNamespace(open=9.8, high=10.6, low=9.7, close=10.4, pct_chg=0.5, volume=1234.0),
    ]

    MainWindow._update_market_summary(host, bars)

    assert "volume" not in captured


def test_market_summary_omits_volume_when_source_has_none() -> None:
    from pa_agent.gui.main_window import MainWindow

    captured: dict[str, object] = {}

    class FakeStrip:
        def set_summary(self, **values) -> None:
            captured.update(values)

    host = SimpleNamespace(
        _market_summary_strip=FakeStrip(),
        _ctx=SimpleNamespace(data_source=None),
    )
    bars = [
        SimpleNamespace(open=10.0, high=11.0, low=9.0, close=10.5, pct_chg=1.0, volume=0.0)
    ]

    MainWindow._update_market_summary(host, bars)

    assert "volume" not in captured


def test_market_summary_strip_renders_quote_fields(qtbot) -> None:
    from pa_agent.gui.widgets.market_summary_strip import MarketSummaryStrip

    strip = MarketSummaryStrip()
    qtbot.addWidget(strip)
    strip.set_summary(
        latest_price=12.3456,
        open_price=12.0,
        change_rate=2.5,
        high_price=12.8,
        low_price=11.9,
    )

    assert strip.height() == 44
    assert strip.layout().contentsMargins().left() == 52
    assert strip._price_value.minimumWidth() == 0
    assert strip._price_value.text() == "12.3456"
    assert "最新价格" not in strip._price_value.text()
    assert "#E8ECF1" in strip._price_value.styleSheet()
    assert "font-size: 22px" in strip._price_value.styleSheet()
    assert list(strip._values) == ["change", "open", "high_low"]
    assert strip._values["open"].text() == "12"
    assert strip._values["change"].text() == "+2.50%"
    assert strip._values["high_low"].text() == "12.8 / 11.9"
    assert not isinstance(strip.layout().itemAt(1).widget(), QFrame)
    assert "turnover" not in strip._values
    assert "turnover_rate" not in strip._values


def test_market_summary_strip_uses_green_for_a_fall(qtbot) -> None:
    from pa_agent.gui.widgets.market_summary_strip import MarketSummaryStrip

    strip = MarketSummaryStrip()
    qtbot.addWidget(strip)
    strip.set_summary(latest_price=12.0, change_rate=-1.5)

    assert "#E8ECF1" in strip._price_value.styleSheet()
    assert "#00D084" in strip._values["change"].styleSheet()


def test_market_summary_strip_clear_resets_values(qtbot) -> None:
    from pa_agent.gui.widgets.market_summary_strip import MarketSummaryStrip

    strip = MarketSummaryStrip()
    qtbot.addWidget(strip)
    strip.set_summary(latest_price=12.34)
    strip.clear()

    assert strip._price_value.text() == "--"
    assert all(value.text() == "--" for value in strip._values.values())
