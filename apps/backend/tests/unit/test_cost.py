"""Cost costing: per-1M-token input/output pricing, defaults, per-model + per-key
roll-ups."""
import pytest

from app.services.cost import build_summary, line_cost

pytestmark = pytest.mark.unit


def test_line_cost_splits_input_output():
    price = {"input_price": 2.0, "output_price": 6.0}  # per 1M
    # 1M prompt @2 + 0.5M completion @6 = 2 + 3 = 5
    assert line_cost(1_000_000, 500_000, price, 0, 0) == 5.0


def test_line_cost_falls_back_to_defaults():
    assert line_cost(1_000_000, 0, None, 3.0, 9.0) == 3.0


def test_build_summary_totals_and_priced_flag():
    by_model = [
        {"model_key": "A", "requests": 2, "prompt_tokens": 1_000_000,
         "completion_tokens": 1_000_000, "total_tokens": 2_000_000},
        {"model_key": "B", "requests": 1, "prompt_tokens": 1_000_000,
         "completion_tokens": 0, "total_tokens": 1_000_000},
    ]
    prices = [{"model": "A", "input_price": 1.0, "output_price": 2.0}]  # B unpriced
    s = build_summary(by_model, [], prices, default_in=0.5, default_out=0.5,
                      currency="USD")
    a = next(m for m in s["by_model"] if m["model"] == "A")
    b = next(m for m in s["by_model"] if m["model"] == "B")
    assert a["cost"] == 3.0 and a["priced"] is True          # 1*1 + 1*2
    assert b["cost"] == 0.5 and b["priced"] is False          # default 0.5 on 1M prompt
    assert s["total_cost"] == 3.5 and s["any_unpriced"] is True
    assert s["currency"] == "USD"


def test_build_summary_rolls_up_per_key_across_models():
    by_key = [
        {"name": "alice", "model_key": "A", "requests": 1, "prompt_tokens": 1_000_000,
         "completion_tokens": 0, "total_tokens": 1_000_000},
        {"name": "alice", "model_key": "B", "requests": 1, "prompt_tokens": 1_000_000,
         "completion_tokens": 0, "total_tokens": 1_000_000},
    ]
    prices = [{"model": "A", "input_price": 1.0, "output_price": 0.0},
              {"model": "B", "input_price": 3.0, "output_price": 0.0}]
    s = build_summary([], by_key, prices, 0, 0, "USD")
    assert len(s["by_key"]) == 1
    assert s["by_key"][0]["name"] == "alice"
    assert s["by_key"][0]["cost"] == 4.0          # 1 + 3
    assert s["by_key"][0]["total_tokens"] == 2_000_000
