"""Per-model pricing for the token meter.

A small table of ``model -> (input_usd_per_mtok, output_usd_per_mtok)``. Used
only to estimate session cost; unknown models price at 0 (the meter still
counts tokens). Prices are best-effort and easily edited.
"""

from __future__ import annotations

from dunders.ai.types import Usage


__all__ = ["price_for", "cost_of", "PRICES"]

# (input $/1M, output $/1M). Local models (ollama) are free.
PRICES: dict[str, tuple[float, float]] = {
    # Anthropic
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    # OpenAI (representative)
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.6),
    # groq / open models (representative; often free tier)
    "gpt-oss-120b": (0.0, 0.0),
}


def price_for(model: str) -> tuple[float, float]:
    """Return (input, output) $/1M for ``model``, or (0, 0) if unknown.

    Matches the longest known prefix so dated/suffixed ids still price.
    """
    if model in PRICES:
        return PRICES[model]
    best: tuple[float, float] | None = None
    best_len = -1
    for key, val in PRICES.items():
        if model.startswith(key) and len(key) > best_len:
            best, best_len = val, len(key)
    return best if best is not None else (0.0, 0.0)


def cost_of(model: str, usage: Usage) -> float:
    """Estimate USD cost of ``usage`` for ``model``."""
    pin, pout = price_for(model)
    billed_in = usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens
    return billed_in / 1_000_000 * pin + usage.output_tokens / 1_000_000 * pout
