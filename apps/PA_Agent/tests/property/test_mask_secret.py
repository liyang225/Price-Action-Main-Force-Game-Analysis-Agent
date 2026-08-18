"""Property-based tests for mask_secret (task 3.4 / PR6)."""
from __future__ import annotations
import pytest
from hypothesis import given, settings as h_settings
from hypothesis import strategies as st
from pa_agent.util.mask_secret import mask_secret


@given(st.text(min_size=4))
def test_last_four_preserved(s: str) -> None:
    """For strings of length >= 4, the last 4 chars are preserved."""
    result = mask_secret(s)
    assert result.endswith(s[-4:])


@given(st.text(min_size=4))
def test_prefix_all_stars(s: str) -> None:
    """For strings of length >= 4, all chars before the last 4 are '*'."""
    result = mask_secret(s)
    prefix = result[: len(s) - 4]
    assert all(c == "*" for c in prefix)


@given(st.text(max_size=3))
def test_short_strings_unchanged(s: str) -> None:
    """Strings shorter than 4 chars are returned unchanged."""
    assert mask_secret(s) == s


@given(st.text())
def test_never_raises(s: str) -> None:
    """mask_secret never raises for any input."""
    mask_secret(s)  # must not raise


@given(st.text(min_size=4))
def test_length_preserved(s: str) -> None:
    """Output length equals input length."""
    assert len(mask_secret(s)) == len(s)
