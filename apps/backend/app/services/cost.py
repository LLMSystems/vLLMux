"""Cost costing: turn per-model token aggregates into money using the price table.

Prices are per 1M tokens, split into input (prompt) and output (completion).
A model without an explicit price falls back to the configured defaults (0 by
default, so cost reads 0 until prices are set). Pure functions over plain dicts —
the store provides token sums, the API provides prices + defaults.
"""
from __future__ import annotations

PER = 1_000_000.0


def _rate(price: dict | None, default_in: float, default_out: float) -> tuple[float, float]:
    if price is None:
        return default_in, default_out
    return price["input_price"], price["output_price"]


def line_cost(prompt_tokens: int, completion_tokens: int,
              price: dict | None, default_in: float, default_out: float) -> float:
    pin, pout = _rate(price, default_in, default_out)
    return (prompt_tokens or 0) / PER * pin + (completion_tokens or 0) / PER * pout


def build_summary(by_model: list[dict], by_key: list[dict], prices: list[dict],
                  default_in: float, default_out: float, currency: str) -> dict:
    """Assemble the cost dashboard payload from token aggregates + prices."""
    price_by_model = {p["model"]: p for p in prices}

    models = []
    total = 0.0
    for row in by_model:
        p = price_by_model.get(row["model_key"])
        cost = line_cost(row["prompt_tokens"], row["completion_tokens"], p, default_in, default_out)
        total += cost
        pin, pout = _rate(p, default_in, default_out)
        models.append({
            "model": row["model_key"],
            "requests": row["requests"],
            "prompt_tokens": row["prompt_tokens"],
            "completion_tokens": row["completion_tokens"],
            "total_tokens": row["total_tokens"],
            "input_price": pin,
            "output_price": pout,
            "priced": p is not None,
            "cost": round(cost, 6),
        })

    # Per (key, model) rows collapsed to per-key totals.
    keys: dict[str, dict] = {}
    for row in by_key:
        p = price_by_model.get(row["model_key"])
        cost = line_cost(row["prompt_tokens"], row["completion_tokens"], p, default_in, default_out)
        k = keys.setdefault(row["name"], {
            "name": row["name"], "requests": 0, "prompt_tokens": 0,
            "completion_tokens": 0, "total_tokens": 0, "cost": 0.0,
        })
        k["requests"] += row["requests"]
        k["prompt_tokens"] += row["prompt_tokens"]
        k["completion_tokens"] += row["completion_tokens"]
        k["total_tokens"] += row["total_tokens"]
        k["cost"] += cost
    by_key_out = sorted(
        ({**v, "cost": round(v["cost"], 6)} for v in keys.values()),
        key=lambda r: r["cost"], reverse=True,
    )

    return {
        "currency": currency,
        "total_cost": round(total, 6),
        "default_input_price": default_in,
        "default_output_price": default_out,
        "any_unpriced": any(not m["priced"] for m in models),
        "by_model": models,
        "by_key": by_key_out,
    }
