from pa_agent.records.trade_rules import (
    ManualEntryOverride,
    SETTLEMENT_T0,
    SETTLEMENT_T1,
    SETTLEMENT_UNSET,
    InstrumentTradeRuleStore,
    TradeEntryOverrideStore,
)


def test_instrument_trade_rule_is_persisted_by_normalized_symbol(tmp_path) -> None:
    path = tmp_path / "instrument_trade_rules.json"
    store = InstrumentTradeRuleStore(path)

    assert store.mode_for("sz.159732") == SETTLEMENT_UNSET

    store.set_mode(" sz.159732 ", SETTLEMENT_T1)

    restored = InstrumentTradeRuleStore(path)
    assert restored.mode_for("SZ.159732") == SETTLEMENT_T1

    restored.set_mode("SZ.159732", SETTLEMENT_T0)
    assert InstrumentTradeRuleStore(path).mode_for("SZ.159732") == SETTLEMENT_T0

    restored.set_mode("SZ.159732", SETTLEMENT_UNSET)
    assert InstrumentTradeRuleStore(path).mode_for("SZ.159732") == SETTLEMENT_UNSET


def test_instrument_entry_tolerance_and_manual_override_are_persisted(tmp_path) -> None:
    rules_path = tmp_path / "instrument_trade_rules.json"
    rules = InstrumentTradeRuleStore(rules_path)

    rules.set_entry_tolerance_ticks("SZ.513090", 3)

    restored_rules = InstrumentTradeRuleStore(rules_path)
    assert restored_rules.entry_tolerance_ticks_for("SZ.513090") == 3

    record_path = tmp_path / "history" / "record.json"
    override_path = tmp_path / "trade_entry_overrides.json"
    overrides = TradeEntryOverrideStore(override_path)
    overrides.set_override(record_path, timestamp_ms=123456789, price=1.826)

    restored_overrides = TradeEntryOverrideStore(override_path)
    assert restored_overrides.override_for(record_path) == ManualEntryOverride(
        timestamp_ms=123456789,
        price=1.826,
    )
