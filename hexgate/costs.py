"""Best-effort cost estimation.

Prices are USD per million tokens and are intentionally conservative: they are
used for relative dashboards, not invoices. Unknown models return ``None``
rather than a made-up number.
"""

from __future__ import annotations

from dataclasses import dataclass

# Baseline used for the "savings" figure: a frontier model at list price.
BASELINE_INPUT = 15.0
BASELINE_OUTPUT = 75.0


@dataclass(frozen=True, slots=True)
class Price:
    input: float
    output: float


PRICES: tuple[tuple[str, Price], ...] = (
    # Anthropic
    ("claude-opus", Price(15.0, 75.0)),
    ("claude-sonnet", Price(3.0, 15.0)),
    ("claude-haiku", Price(0.8, 4.0)),
    ("claude-3-5-haiku", Price(0.8, 4.0)),
    # OpenAI
    ("gpt-4o-mini", Price(0.15, 0.6)),
    ("gpt-4o", Price(2.5, 10.0)),
    ("gpt-4.1-mini", Price(0.4, 1.6)),
    ("gpt-4.1", Price(2.0, 8.0)),
    ("gpt-5-mini", Price(0.25, 2.0)),
    ("gpt-5", Price(1.25, 10.0)),
    ("o4-mini", Price(1.1, 4.4)),
    ("o3-mini", Price(1.1, 4.4)),
    ("gpt-oss-120b", Price(0.2, 0.8)),
    ("gpt-oss-20b", Price(0.1, 0.4)),
    # Google
    ("gemini-2.5-flash", Price(0.3, 2.5)),
    ("gemini-2.5-pro", Price(1.25, 10.0)),
    ("gemini-2.0-flash", Price(0.1, 0.4)),
    # Others
    ("deepseek-reasoner", Price(0.55, 2.19)),
    ("deepseek", Price(0.27, 1.1)),
    ("grok-3-mini", Price(0.3, 0.5)),
    ("grok-code", Price(0.2, 1.5)),
    ("grok", Price(3.0, 15.0)),
    ("kimi-k2", Price(0.6, 2.5)),
    ("glm-4.6", Price(0.6, 2.2)),
    ("glm-4.5", Price(0.6, 2.2)),
    ("minimax", Price(0.3, 1.2)),
    ("devstral", Price(0.4, 2.0)),
    ("codestral", Price(0.3, 0.9)),
    ("command-r-plus", Price(2.5, 10.0)),
    ("command-r", Price(0.15, 0.6)),
    ("llama-3.3-70b", Price(0.6, 0.7)),
    ("llama-3.1-70b", Price(0.6, 0.7)),
    ("llama-3.1-8b", Price(0.05, 0.08)),
    ("qwen3-coder", Price(0.3, 1.2)),
    ("qwen3-32b", Price(0.3, 0.6)),
    ("qwen2.5-coder", Price(0.2, 0.6)),
    ("nemotron", Price(0.3, 0.9)),
)

_ORDERED = sorted(PRICES, key=lambda item: len(item[0]), reverse=True)


def price_for(model: str) -> Price | None:
    needle = (model or "").lower()
    for pattern, price in _ORDERED:
        if pattern in needle:
            return price
    return None


def estimate_cost(
    model: str, prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0
) -> float | None:
    price = price_for(model)
    if price is None:
        return None
    billable_prompt = max(int(prompt_tokens or 0) - int(cached_tokens or 0), 0)
    return (
        billable_prompt / 1_000_000 * price.input
        + int(completion_tokens or 0) / 1_000_000 * price.output
    )


def baseline_cost(prompt_tokens: int, completion_tokens: int) -> float:
    return (
        int(prompt_tokens or 0) / 1_000_000 * BASELINE_INPUT
        + int(completion_tokens or 0) / 1_000_000 * BASELINE_OUTPUT
    )


def savings(
    model: str, prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0
) -> float | None:
    cost = estimate_cost(model, prompt_tokens, completion_tokens, cached_tokens)
    if cost is None:
        return None
    return max(baseline_cost(prompt_tokens, completion_tokens) - cost, 0.0)
