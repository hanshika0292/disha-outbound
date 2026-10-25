from __future__ import annotations

from decimal import Decimal

import pytest

from disha.llm.pricing import PRICING, cost_usd
from disha.llm.schemas import TokenUsage


def test_gemini_flash_cost_for_known_token_counts() -> None:
    usage = TokenUsage(tokens_in=1_000_000, tokens_out=1_000_000)
    cost = cost_usd("gemini", "gemini-2.5-flash", usage)
    expected = (
        PRICING[("gemini", "gemini-2.5-flash")]["input"]
        + PRICING[("gemini", "gemini-2.5-flash")]["output"]
    )
    assert cost == expected.quantize(Decimal("0.000001"))


def test_anthropic_haiku_cost_proportional() -> None:
    small = cost_usd(
        "anthropic", "claude-haiku-4-5-20251001", TokenUsage(tokens_in=500, tokens_out=500)
    )
    big = cost_usd(
        "anthropic", "claude-haiku-4-5-20251001", TokenUsage(tokens_in=5000, tokens_out=5000)
    )
    assert big == small * 10


def test_mock_model_is_free() -> None:
    cost = cost_usd("mock", "mock-model", TokenUsage(tokens_in=10000, tokens_out=10000))
    assert cost == Decimal("0.000000")


def test_unknown_pair_raises_clear_error() -> None:
    with pytest.raises(KeyError, match="No pricing"):
        cost_usd("nonexistent", "ghost", TokenUsage(tokens_in=1, tokens_out=1))
