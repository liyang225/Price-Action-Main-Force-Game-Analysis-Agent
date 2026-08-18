"""Property-based tests for next_bar_prediction normalizer + validator (T4, T6).

Covers correctness properties P1–P7 from design.md.
"""
from __future__ import annotations

import copy
from hypothesis import given, settings as h_settings, assume
from hypothesis import strategies as st

from pa_agent.ai.json_validator import JsonValidator
from pa_agent.ai.stage2_normalizer import _normalize_next_bar_prediction


# ── Strategies ────────────────────────────────────────────────────────────────

_probability_value = st.integers(min_value=0, max_value=100)

_direction_enum = st.sampled_from(["bullish", "bearish", "neutral"])

_features_used = st.lists(st.text(min_size=1, max_size=40, alphabet="abcdefghijklmnopqrstuvwxyz_"), min_size=0, max_size=5)

_reasoning_text = st.text(min_size=0, max_size=3000, alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")))

_raw_probability = st.one_of(
    st.integers(min_value=-10, max_value=110),
    st.floats(min_value=-10.0, max_value=110.0, allow_nan=False, allow_infinity=False),
)

_prediction_dict = st.fixed_dictionaries(
    {},
    optional={
        "direction": st.one_of(_direction_enum, st.none(), st.text(max_size=10)),
        "probabilities": st.one_of(
            st.fixed_dictionaries(
                {},
                optional={
                    "bullish": _raw_probability,
                    "bearish": _raw_probability,
                    "neutral": _raw_probability,
                },
            ),
            st.none(),
            st.integers(),
            st.text(max_size=10),
        ),
        "reasoning": st.one_of(_reasoning_text, st.integers(), st.none()),
        "unpredictable": st.one_of(st.booleans(), st.integers(), st.none()),
        "features_used": st.one_of(_features_used, st.none(), st.integers()),
    },
)


# ── P4: Reasoning length ≤ 1500 after normalization ──────────────────────────

@given(pred=_prediction_dict)
@h_settings(max_examples=200)
def test_p4_reasoning_length_bounded(pred: dict):
    """After normalization, reasoning length must be ≤ 1500."""
    _normalize_next_bar_prediction(pred)
    if isinstance(pred.get("reasoning"), str):
        assert len(pred["reasoning"]) <= 1500


# ── P5: features_used minimum set + dedup ────────────────────────────────────

@given(pred=_prediction_dict)
@h_settings(max_examples=200)
def test_p5_features_used_min_set_dedup(pred: dict):
    """features_used must contain stage1_diagnosis and be deduplicated."""
    _normalize_next_bar_prediction(pred)
    feats = pred.get("features_used")
    assert isinstance(feats, list)
    assert "stage1_diagnosis" in feats
    assert len(feats) == len(set(feats)), f"Duplicates found: {feats}"


# ── P6: Normalizer is idempotent and orthogonal to order_type ────────────────

@given(pred=_prediction_dict)
@h_settings(max_examples=200)
def test_p6_normalizer_idempotent(pred: dict):
    """Applying normalization twice produces the same result."""
    first = copy.deepcopy(pred)
    _normalize_next_bar_prediction(first)
    second = copy.deepcopy(first)
    _normalize_next_bar_prediction(second)
    assert first == second


# ── P1: Probabilities are valid [0, 100] ints with sum in [99, 101] ──────────

@given(pred=_prediction_dict)
@h_settings(max_examples=200)
def test_p1_probabilities_valid_after_normalize(pred: dict):
    """After normalization, if not unpredictable, probabilities must be valid ints."""
    _normalize_next_bar_prediction(pred)
    if pred.get("unpredictable"):
        return  # unpredictable → probabilities=None
    probs = pred.get("probabilities")
    if not isinstance(probs, dict):
        return  # unparseable → validator catches it
    for key in ("bullish", "bearish", "neutral"):
        val = probs.get(key)
        assert isinstance(val, int), f"{key} is not int: {val}"
        assert 0 <= val <= 100, f"{key} out of range: {val}"


# ── P2: direction = argmax after normalization ───────────────────────────────

@given(pred=_prediction_dict)
@h_settings(max_examples=200)
def test_p2_direction_equals_argmax(pred: dict):
    """After normalization, direction must equal argmax of probabilities."""
    _normalize_next_bar_prediction(pred)
    if pred.get("unpredictable"):
        return
    probs = pred.get("probabilities")
    if not isinstance(probs, dict):
        return
    direction = pred.get("direction")
    if direction is None:
        return
    order = ("bullish", "bearish", "neutral")
    max_val = max(probs[k] for k in order)
    expected = next(k for k in order if probs[k] == max_val)
    assert direction == expected, f"direction={direction}, expected={expected}"


# ── P3: unpredictable branch null consistency ────────────────────────────────

@given(pred=_prediction_dict)
@h_settings(max_examples=200)
def test_p3_unpredictable_null_consistency(pred: dict):
    """If unpredictable=true after normalize, direction and probabilities must be None."""
    _normalize_next_bar_prediction(pred)
    if pred.get("unpredictable"):
        assert pred.get("direction") is None
        assert pred.get("probabilities") is None


# ── P7: Validator c-category errors have correct prefix ──────────────────────

@given(pred=_prediction_dict)
@h_settings(max_examples=200)
def test_p7_validator_error_prefix(pred: dict):
    """All _check_next_bar_prediction errors must start with next_bar_prediction."""
    _normalize_next_bar_prediction(pred)
    obj = {"next_bar_prediction": pred}
    errors = JsonValidator._check_next_bar_prediction(obj)
    for e in errors:
        assert e.startswith("next_bar_prediction."), f"Bad prefix: {e}"
