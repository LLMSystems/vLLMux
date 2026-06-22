import types

import pytest

from app.services.dataset_service import (_json_safe, _metric_names,
                                          _sample_question, eval_dataset_meta)

pytestmark = pytest.mark.unit


def test_json_safe_breaks_cycles_and_bounds_depth():
    # A self-referential structure must not recurse forever (the bug that surfaced
    # as "maximum recursion depth exceeded" when FastAPI encoded a preview row).
    d: dict = {"a": 1}
    d["self"] = d
    out = _json_safe(d)
    import json
    json.dumps(out)  # must be serialisable (raises if cyclic/deep)
    # deep nesting collapses to a string past the depth cap
    deep = cur = {}
    for _ in range(20):
        cur["x"] = {}
        cur = cur["x"]
    json.dumps(_json_safe(deep))


def test_json_safe_passes_primitives():
    assert _json_safe("A") == "A"
    assert _json_safe([1, 2, "3"]) == [1, 2, "3"]
    assert _json_safe({"k": True}) == {"k": True}


def test_metric_names_flattens_str_and_dict():
    # evalscope metric_list mixes plain names and {name: opts} dicts.
    assert _metric_names(["acc"]) == ["acc"]
    assert _metric_names([{"acc": {"numeric": True}}]) == ["acc"]
    assert _metric_names(["a", {"b": {}}, {"c": 1}]) == ["a", "b", "c"]
    assert _metric_names(None) == []


def _msg(role, content):
    return types.SimpleNamespace(role=role, content=content)


def test_sample_question_takes_last_user_text():
    s = types.SimpleNamespace(input=[_msg("system", "sys"), _msg("user", "What is 2+2?")])
    assert _sample_question(s) == "What is 2+2?"


def test_sample_question_handles_plain_string_and_multimodal():
    assert _sample_question(types.SimpleNamespace(input="just text")) == "just text"
    parts = [types.SimpleNamespace(text="hello"), types.SimpleNamespace(text="world")]
    s = types.SimpleNamespace(input=[_msg("user", parts)])
    assert _sample_question(s) == "hello world"


def test_eval_dataset_meta_unknown_key_is_none():
    # Unknown benchmark -> None (whether or not evalscope is importable), so the
    # catalog entry still works without an inspector.
    assert eval_dataset_meta("definitely_not_a_registered_benchmark_xyz") is None
