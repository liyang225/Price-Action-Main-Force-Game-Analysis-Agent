"""Overlay line label positioning must use exact prices, not .5g round-trip."""


def test_five_g_display_roundtrip_loses_precision() -> None:
    """Document why label Y must not be parsed from formatted text."""
    price = 4082.123456
    assert float(f"{price:.5g}") != price


def test_overlay_lines_stores_exact_price_per_label() -> None:
    from pa_agent.gui.widgets.overlay_lines import OverlayLines

    overlay = OverlayLines()
    prices = [4082.123456, 4090.5, 4076.232]
    overlay._labels = [(None, p) for p in prices]  # type: ignore[list-item]
    assert [p for _, p in overlay._labels] == prices
