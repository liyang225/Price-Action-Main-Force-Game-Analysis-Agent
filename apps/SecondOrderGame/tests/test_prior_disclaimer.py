from src.probability.disclaimer import (
    DISCLAIMER_TEXT,
    annotate_probability_row,
    disclaimer_for_prior_weight,
)


def test_prior_disclaimer_is_row_specific_and_exits_at_the_configured_threshold():
    assert disclaimer_for_prior_weight(0.20) == DISCLAIMER_TEXT
    assert disclaimer_for_prior_weight(0.199999) is None
    assert annotate_probability_row({"outcome": "建仓", "probability": 0.4, "prior_weight": 0.8})["disclaimer"] == DISCLAIMER_TEXT
    assert "disclaimer" not in annotate_probability_row({"outcome": "建仓", "probability": 0.4, "prior_weight": 0.1})
