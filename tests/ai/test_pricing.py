"""Pricing table + cost estimation."""

from __future__ import annotations

import pytest

from dunders.ai.pricing import cost_of, price_for
from dunders.ai.types import Usage


def test_known_model():
    assert price_for("claude-opus-4-8") == (5.0, 25.0)


def test_prefix_match():
    # dated/suffixed ids still price via longest-prefix match
    assert price_for("claude-opus-4-8-20260101") == (5.0, 25.0)


def test_unknown_model_is_free():
    assert price_for("totally-unknown") == (0.0, 0.0)


def test_cost_of():
    cost = cost_of("claude-opus-4-8", Usage(input_tokens=1_000_000, output_tokens=1_000_000))
    assert cost == pytest.approx(30.0)  # $5 in + $25 out
