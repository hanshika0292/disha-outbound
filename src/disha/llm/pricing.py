"""Per-1M-token prices in USD. Update from provider docs quarterly.

Use `cost_usd(provider, model, usage)` rather than reading PRICING directly
so we get a clear error when a (provider, model) pair isn't in the table.
"""

from decimal import Decimal

from disha.llm.schemas import TokenUsage

PRICING: dict[tuple[str, str], dict[str, Decimal]] = {
    ("gemini", "gemini-2.5-flash"): {
        "input": Decimal("0.075"),
        "output": Decimal("0.30"),
    },
    ("gemini", "gemini-2.5-pro"): {
        "input": Decimal("1.25"),
        "output": Decimal("5.00"),
    },
    ("anthropic", "claude-haiku-4-5-20251001"): {
        "input": Decimal("0.80"),
        "output": Decimal("4.00"),
    },
    ("anthropic", "claude-sonnet-4-6"): {
        "input": Decimal("3.00"),
        "output": Decimal("15.00"),
    },
    ("openai", "gpt-4.1-mini"): {
        "input": Decimal("0.15"),
        "output": Decimal("0.60"),
    },
    ("openai", "gpt-4.1"): {
        "input": Decimal("2.50"),
        "output": Decimal("10.00"),
    },
    ("mock", "mock-model"): {
        "input": Decimal("0"),
        "output": Decimal("0"),
    },
}

_PER_M = Decimal("1000000")


def cost_usd(provider: str, model: str, usage: TokenUsage) -> Decimal:
    key = (provider, model)
    if key not in PRICING:
        raise KeyError(
            f"No pricing for ({provider!r}, {model!r}). Add to disha.llm.pricing.PRICING."
        )
    rates = PRICING[key]
    cost = (
        rates["input"] * Decimal(usage.tokens_in) + rates["output"] * Decimal(usage.tokens_out)
    ) / _PER_M
    return cost.quantize(Decimal("0.000001"))
