"""Deterministic scenario-simulation baselines for the Loupe agent.

Ported unchanged in substance from ecommerce-analytics-agent/main.py's
LEVER_RULES and get_lever_baseline(). The documented sensitivity rules
themselves are never generated or altered by an LLM -- they are fixed,
reviewed text that the LLM (chat.py::simulate_scenario) applies to real
baseline data, per docs/loupe-agent.md's evidence contract: "Claude may
explain only values present in returned BigQuery results or deterministic
metadata."
"""

from __future__ import annotations

from typing import Optional

from apps.loupe_agent.metrics import (
    BigQueryClientLike,
    get_category_metrics,
    get_channel_mix_trend,
    get_company_benchmark,
    get_lever_price_position,
)

LEVER_RULES = {
    "return_rate_improvement": {
        "description": "Sensitivity of category margin to a change in return rate",
        "rule": (
            "A return rate above roughly 20% is a significant margin drain for apparel e-commerce; "
            "below roughly 10% is considered healthy. If a category's return rate drops by N percentage "
            "points, recalculate the retained revenue and margin assuming the recovered items would have "
            "sold at the category's current average sale price and margin. The larger the point drop, the "
            "more material the retained margin, but returns rarely reach zero, so treat full elimination "
            "as unrealistic."
        ),
    },
    "channel_mix_shift": {
        "description": "Sensitivity of growth durability to paid vs. unpaid channel mix",
        "rule": (
            "An increasing paid-channel share of order volume signals growth that is more dependent on "
            "continued ad spend and more exposed to rising acquisition costs or budget cuts. An increasing "
            "unpaid (organic/direct) share signals more durable, self-sustaining growth that is less "
            "vulnerable to a marketing budget change."
        ),
    },
    "category_price_position": {
        "description": "Sensitivity of category resilience to its margin percentage relative to the company average",
        "rule": (
            "A category with a margin percentage below the company-wide average has less room to absorb "
            "cost inflation, supplier price increases, or discount promotions before becoming unprofitable. "
            "A category priced above the company average has more cushion to withstand those same pressures."
        ),
    },
}


class UnknownLeverError(ValueError):
    """Raised when get_lever_baseline() is asked for a lever not in
    LEVER_RULES -- fails loudly rather than silently returning an
    ambiguous "no baseline data available" string, so callers cannot
    mistake a typo'd lever name for a genuinely unsupported one without
    at least seeing which levers ARE supported."""


def get_lever_baseline(
    client: BigQueryClientLike, lever: str, category: Optional[str] = None
) -> dict:
    """Pull the real current baseline data relevant to a specific lever.

    Returns a dict with a "lever" key plus lever-specific evidence fields
    (never a pre-formatted string -- see metrics.py's module docstring for
    why). Raises UnknownLeverError for any lever not in LEVER_RULES, and
    ValueError if a category-scoped lever is invoked without a category --
    both changes from the original, which returned a plain error string
    ("No category specified...") indistinguishable from real data by a
    caller that didn't check the type.
    """

    if lever not in LEVER_RULES:
        raise UnknownLeverError(
            f"lever={lever!r} is not one of {sorted(LEVER_RULES.keys())}"
        )

    if lever == "return_rate_improvement":
        if not category:
            raise ValueError("return_rate_improvement requires a category")
        baseline = get_category_metrics(client, category)
        if baseline is None:
            raise ValueError(f"No data found for category: {category}")
        return {"lever": lever, "category": category, **baseline}

    if lever == "channel_mix_shift":
        baseline = get_channel_mix_trend(client)
        return {"lever": lever, **baseline}

    # lever == "category_price_position"
    if not category:
        raise ValueError("category_price_position requires a category")
    baseline = get_lever_price_position(client, category)
    if baseline is None:
        raise ValueError(f"No data found for category: {category}")
    company_benchmark = get_company_benchmark(client)
    return {"lever": lever, "company_benchmark": company_benchmark, **baseline}
