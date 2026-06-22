"""Thin wrapper over ModelScope's dataset cache for evalscope datasets.

evalscope downloads datasets via ModelScope (``dataset_snapshot_download``) into
``$MODELSCOPE_CACHE/datasets/<dataset_id>/`` — a different cache from the HF
weights cache. Two flavours live here:

* **perf** datasets (multi-turn load tests) — a single JSONL file inside a repo.
* **eval** datasets (accuracy benchmarks like gsm8k/mmlu) — the whole repo.

This module lists / pre-downloads / deletes the handful the dashboard uses so a
benchmark or eval run doesn't stall on a first-time download. Synchronous
(disk/network IO) — callers thread it.
"""
from __future__ import annotations

import os
import shutil
from typing import Any, Optional

# Perf catalog: single-file datasets the load-test UI uses. key -> dataset id + file.
PERF_CATALOG: list[dict[str, Any]] = [
    {
        "key": "share_gpt_zh",
        "label": "ShareGPT 中文",
        "dataset_id": "swift/sharegpt",
        "file": "common_zh_70k.jsonl",
        "category": "perf",
        "note": "多輪對話壓測 share_gpt_zh_multi_turn 用",
        "approx": "~460 MB",
    },
    {
        "key": "share_gpt_en",
        "label": "ShareGPT 英文",
        "dataset_id": "swift/sharegpt",
        "file": "common_en_70k.jsonl",
        "category": "perf",
        "note": "多輪對話壓測 share_gpt_en_multi_turn 用",
        "approx": "~500 MB",
    },
    {
        "key": "openqa",
        "label": "OpenQA（HC3 中文）",
        "dataset_id": "AI-ModelScope/HC3-Chinese",
        "file": "open_qa.jsonl",
        "category": "perf",
        "note": "單輪 openqa 資料集",
        "approx": "~數 MB",
    },
]

# Eval catalog: whole-repo evalscope benchmarks (no single file). key == the
# evalscope dataset name (passed straight to TaskConfig.datasets); dataset_id is
# its ModelScope repo. `tier` groups them in the UI; `note` flags caveats.
EVAL_CATALOG: list[dict[str, Any]] = [
    # 基線組 — 通用能力第一輪驗收
    {"key": "mmlu", "label": "MMLU", "dataset_id": "cais/mmlu", "category": "eval", "tier": "基線", "metric": "acc"},
    {"key": "arc", "label": "ARC", "dataset_id": "allenai/ai2_arc", "category": "eval", "tier": "基線", "metric": "acc"},
    {"key": "hellaswag", "label": "HellaSwag", "dataset_id": "evalscope/hellaswag", "category": "eval", "tier": "基線", "metric": "acc"},
    {"key": "gsm8k", "label": "GSM8K", "dataset_id": "AI-ModelScope/gsm8k", "category": "eval", "tier": "基線", "metric": "acc"},
    {"key": "ifeval", "label": "IFEval", "dataset_id": "opencompass/ifeval", "category": "eval", "tier": "基線", "metric": "規則"},
    {"key": "truthful_qa", "label": "TruthfulQA", "dataset_id": "evalscope/truthful_qa", "category": "eval", "tier": "基線", "metric": "acc"},
    # 知識進階 — 更難的知識 / MCQ 基準
    {"key": "gpqa_diamond", "label": "GPQA-Diamond", "dataset_id": "AI-ModelScope/gpqa_diamond", "category": "eval", "tier": "知識進階", "metric": "acc"},
    {"key": "mmlu_pro", "label": "MMLU-Pro", "dataset_id": "TIGER-Lab/MMLU-Pro", "category": "eval", "tier": "知識進階", "metric": "acc"},
    {"key": "mmlu_redux", "label": "MMLU-Redux", "dataset_id": "AI-ModelScope/mmlu-redux-2.0", "category": "eval", "tier": "知識進階", "metric": "acc"},
    {"key": "super_gpqa", "label": "SuperGPQA", "dataset_id": "m-a-p/SuperGPQA", "category": "eval", "tier": "知識進階", "metric": "acc"},
    {"key": "trivia_qa", "label": "TriviaQA", "dataset_id": "evalscope/trivia_qa", "category": "eval", "tier": "知識進階", "metric": "acc"},
    # 中文組
    {"key": "ceval", "label": "C-Eval", "dataset_id": "evalscope/ceval", "category": "eval", "tier": "中文", "metric": "acc"},
    {"key": "cmmlu", "label": "C-MMLU", "dataset_id": "evalscope/cmmlu", "category": "eval", "tier": "中文", "metric": "acc"},
    {"key": "iquiz", "label": "IQuiz", "dataset_id": "AI-ModelScope/IQuiz", "category": "eval", "tier": "中文", "metric": "acc"},
    # 推理組
    {"key": "bbh", "label": "BBH", "dataset_id": "evalscope/bbh", "category": "eval", "tier": "推理", "metric": "acc"},
    {"key": "logi_qa", "label": "LogiQA", "dataset_id": "extraordinarylab/logiqa", "category": "eval", "tier": "推理", "metric": "acc"},
    # 數學組
    {"key": "math_500", "label": "MATH-500", "dataset_id": "AI-ModelScope/MATH-500", "category": "eval", "tier": "數學", "metric": "acc"},
    {"key": "competition_math", "label": "Competition-MATH", "dataset_id": "evalscope/competition_math", "category": "eval", "tier": "數學", "metric": "acc"},
    {"key": "aime24", "label": "AIME-2024", "dataset_id": "evalscope/aime24", "category": "eval", "tier": "數學", "metric": "acc", "note": "高難度，建議配重複採樣"},
    {"key": "aime25", "label": "AIME-2025", "dataset_id": "evalscope/aime25", "category": "eval", "tier": "數學", "metric": "acc", "note": "高難度，建議配重複採樣"},
    # 多語言組
    {"key": "mgsm", "label": "MGSM", "dataset_id": "evalscope/mgsm", "category": "eval", "tier": "多語言", "metric": "acc"},
    {"key": "mmmlu", "label": "MMMLU", "dataset_id": "openai-mirror/MMMLU", "category": "eval", "tier": "多語言", "metric": "acc"},
    # 工具調用
    # tool_bench: 靜態評測（模型輸出文字，用 EM/F1/Rouge 比對標準工具調用序列；不需伺服器 tools API）
    {"key": "tool_bench", "label": "ToolBench-Static", "dataset_id": "AI-ModelScope/ToolBench-Static", "category": "eval", "tier": "工具調用", "metric": "F1/EM"},
    # general_fc: 真實函數調用——檢查 finish_reason=tool_calls / schema 正確率，
    # 需模型啟用 vLLM tool parser（enable_auto_tool_choice + tool_call_parser），否則全 0。
    {"key": "general_fc", "label": "General-FunctionCall", "dataset_id": "evalscope/GeneralFunctionCall-Test", "category": "eval", "tier": "工具調用", "metric": "tool_call_f1", "needs_tool_parser": True, "note": "需模型開 tool parser"},
    # 長上下文組 — 需要大 context window 的模型（小 max_model_len 會被截斷 / 報錯）
    {"key": "needle_haystack", "label": "Needle-in-a-Haystack", "dataset_id": "AI-ModelScope/Needle-in-a-Haystack-Corpus", "category": "eval", "tier": "長上下文", "metric": "acc", "long_context": True, "note": "需大 context"},
    {"key": "longbench_v2", "label": "LongBench-v2", "dataset_id": "ZhipuAI/LongBench-v2", "category": "eval", "tier": "長上下文", "metric": "acc", "long_context": True, "note": "需大 context"},
    {"key": "frames", "label": "FRAMES", "dataset_id": "iic/frames", "category": "eval", "tier": "長上下文", "metric": "acc", "long_context": True, "note": "需大 context"},
    {"key": "openai_mrcr", "label": "OpenAI-MRCR", "dataset_id": "openai-mirror/mrcr", "category": "eval", "tier": "長上下文", "metric": "mrcr_score", "long_context": True, "note": "需大 context"},
    # 問答 / 事實性（需裁判模型評分）
    {"key": "simple_qa", "label": "SimpleQA", "dataset_id": "evalscope/SimpleQA", "category": "eval", "tier": "問答（需裁判）", "metric": "judge", "needs_judge": True},
    {"key": "chinese_simple_qa", "label": "Chinese-SimpleQA", "dataset_id": "AI-ModelScope/Chinese-SimpleQA", "category": "eval", "tier": "問答（需裁判）", "metric": "judge", "needs_judge": True},
    # 程式碼組 — HumanEval 在後端容器內本機執行生成的程式碼來算 pass@1。
    # （MBPP 被 evalscope 強制要求 docker 沙箱 ms-enclave，本部署未提供，故不列入。）
    {"key": "humaneval", "label": "HumanEval", "dataset_id": "opencompass/humaneval", "category": "eval", "tier": "程式碼", "metric": "pass@1", "note": "會執行生成碼"},
]

# The full library = both flavours; keyed lookup spans both (keys never collide).
CATALOG: list[dict[str, Any]] = PERF_CATALOG + EVAL_CATALOG
_BY_KEY = {d["key"]: d for d in CATALOG}


def get_entry(key: str) -> Optional[dict[str, Any]]:
    return _BY_KEY.get(key)


def _cache_root() -> str:
    """ModelScope dataset cache root. Mirrors modelscope's own resolution
    (``$MODELSCOPE_CACHE`` or ``~/.cache/modelscope/hub``, then ``/datasets``)
    without importing modelscope — so listing the cache works even where the
    heavy package isn't installed (e.g. the lightweight CI test job)."""
    base = os.environ.get("MODELSCOPE_CACHE") or os.path.join(
        os.path.expanduser("~"), ".cache", "modelscope", "hub"
    )
    return os.path.join(os.path.expanduser(base), "datasets")


def dataset_dir(entry: dict[str, Any]) -> str:
    return os.path.join(_cache_root(), entry["dataset_id"])


def file_path(entry: dict[str, Any]) -> Optional[str]:
    """On-disk path of a single-file dataset (None for whole-repo eval datasets)."""
    if not entry.get("file"):
        return None
    return os.path.join(dataset_dir(entry), entry["file"])


def dir_size(entry: dict[str, Any]) -> int:
    """Bytes under the dataset dir incl. in-flight temp files (smooth progress)."""
    total = 0
    for root, _dirs, files in os.walk(dataset_dir(entry)):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def cached_size(entry: dict[str, Any]) -> int:
    """Bytes on disk: the single file (perf) or the whole repo dir (eval)."""
    path = file_path(entry)
    if path is None:  # whole-repo dataset
        return dir_size(entry)
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def download(entry: dict[str, Any]) -> str:
    """Blocking ModelScope download. Single file for perf datasets, whole repo
    for eval datasets. Returns the local file (perf) or dir (eval) path."""
    from modelscope import dataset_snapshot_download

    if entry.get("file"):
        local = dataset_snapshot_download(entry["dataset_id"], allow_patterns=[entry["file"]])
        return os.path.join(local, entry["file"])
    return dataset_snapshot_download(entry["dataset_id"])


def disk_usage() -> dict[str, int]:
    """total/used/free bytes of the volume holding the dataset cache."""
    root = _cache_root()
    probe = root
    while probe and not os.path.exists(probe):
        probe = os.path.dirname(probe)
    usage = shutil.disk_usage(probe or "/")
    return {"total": usage.total, "used": usage.used, "free": usage.free}


def scan() -> list[dict[str, Any]]:
    """Catalog with per-dataset cached flag + on-disk size (both flavours)."""
    out: list[dict[str, Any]] = []
    for e in CATALOG:
        size = cached_size(e)
        out.append({**e, "cached": size > 0, "size_on_disk": size})
    return out


# Catalog key -> evalscope registry name, only where they differ. The catalog
# key is usually identical to the benchmark name; a few drop/realign separators.
_REGISTRY_NAME = {"chinese_simple_qa": "chinese_simpleqa"}


def _registry_name(key: str) -> str:
    return _REGISTRY_NAME.get(key, key)


def _json_safe(obj: Any, _depth: int = 0) -> Any:
    """Coerce an arbitrary value to a JSON-safe primitive tree, bounding depth and
    breadth. Evalscope Sample.metadata/target can hold nested or self-referential
    objects that blow FastAPI's encoder stack (RecursionError) — this guarantees a
    finite, serialisable result."""
    if _depth >= 6:
        return str(obj)
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _json_safe(v, _depth + 1) for k, v in list(obj.items())[:50]}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v, _depth + 1) for v in list(obj)[:50]]
    return str(obj)


def _metric_names(metric_list: Any) -> list[str]:
    """evalscope metric_list is a list of str or {name: opts} dicts — flatten to names."""
    out: list[str] = []
    for m in metric_list or []:
        if isinstance(m, str):
            out.append(m)
        elif isinstance(m, dict):
            out.extend(m.keys())
    return out


def eval_dataset_meta(key: str) -> Optional[dict[str, Any]]:
    """Per-benchmark detail from evalscope's registry: subsets, metric, tags,
    few-shot, description. None when the key isn't a registered benchmark (the
    catalog entry still works, just without an inspector). Cheap — no data load."""
    try:
        from evalscope.api.registry import BENCHMARK_REGISTRY
    except Exception:
        return None
    meta = BENCHMARK_REGISTRY.get(_registry_name(key))
    if meta is None:
        return None
    desc = (getattr(meta, "description", None) or "").strip()
    return {
        "subsets": list(getattr(meta, "subset_list", []) or []),
        "default_subset": getattr(meta, "default_subset", "default"),
        "few_shot_num": getattr(meta, "few_shot_num", 0),
        "eval_split": getattr(meta, "eval_split", None),
        "metric": _metric_names(getattr(meta, "metric_list", [])),
        "tags": list(getattr(meta, "tags", []) or []),
        "pretty_name": getattr(meta, "pretty_name", None),
        "description": desc[:1200],
        "paper_url": getattr(meta, "paper_url", None),
    }


def _sample_question(sample: Any) -> str:
    """Best-effort plain-text prompt from an evalscope Sample.input (chat messages)."""
    msgs = getattr(sample, "input", None)
    if isinstance(msgs, str):
        return msgs
    if isinstance(msgs, list) and msgs:
        # last user turn (few_shot_num=0 keeps it to just the question)
        for m in reversed(msgs):
            content = getattr(m, "content", None)
            if content is None:
                continue
            if isinstance(content, str):
                return content
            if isinstance(content, list):  # multimodal parts
                return " ".join(getattr(p, "text", "") or "" for p in content)
    return str(msgs or "")


def eval_dataset_preview(key: str, subset: Optional[str] = None, n: int = 20) -> dict[str, Any]:
    """Load the first `n` rows of one subset straight from the ModelScope cache,
    normalised for display. Blocking (disk IO + dataset parse) — call off-thread.

    `few_shot_num=0` so the prompt is just the question (no in-context examples).
    Returns the subset's full row count too (len of the loaded split)."""
    import sys

    from evalscope.api.registry import get_benchmark
    from evalscope.config import TaskConfig

    # Some benchmarks (e.g. tool_bench) build deeply-nested sample structures on a
    # cold first load that legitimately exceed Python's default 1000-frame limit
    # ("maximum recursion depth exceeded"). The structures are finite, so lift the
    # cap for this worker call. Output is separately bounded by _json_safe.
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 8000))

    meta = eval_dataset_meta(key) or {}
    subsets = meta.get("subsets") or ["default"]
    # default_subset may be a meta-subset ("all") that fans out to everything —
    # never preview that; fall back to the first concrete subset.
    if not subset:
        d = meta.get("default_subset")
        subset = d if d in subsets else subsets[0]
    n = max(1, min(int(n), 50))
    rname = _registry_name(key)

    # `limit=n`: only build ~n samples instead of processing the whole subset, so a
    # cold first preview is fast (the slow part is per-row sample construction, not
    # the row count). Trade-off: we can't report the true total for subsets larger
    # than n — `truncated` then signals "there are more" and `count` is the loaded
    # size (which IS the true total for subsets that fit within n).
    cfg = TaskConfig(
        model="preview",
        datasets=[rname],
        limit=n,
        dataset_args={rname: {"subset_list": [subset], "few_shot_num": 0}},
    )
    adapter = get_benchmark(rname, cfg)
    dd = adapter.load_dataset()
    keys = list(dd.keys())  # DatasetDict has no __contains__; check the key list
    split_key = subset if subset in keys else keys[0]
    ds = dd[split_key]
    rows = []
    for i in range(min(n, len(ds))):
        s = ds[i]
        rows.append({
            "question": _sample_question(s)[:4000],
            "choices": _json_safe(list(getattr(s, "choices", None) or [])),
            "target": _json_safe(getattr(s, "target", None)),
            "metadata": _json_safe(getattr(s, "metadata", None) or {}),
        })
    # len == n almost certainly means the limit clipped a larger subset.
    truncated = len(ds) >= n
    return {
        "subset": split_key,
        "count": len(ds),
        "rows": rows,
        "truncated": truncated,
        # Intro shown alongside the rows in the library preview.
        "pretty_name": meta.get("pretty_name"),
        "description": meta.get("description"),
        "tags": meta.get("tags", []),
        "metric": meta.get("metric", []),
        "subsets": meta.get("subsets", []),
    }


def delete(key: str) -> bool:
    """Remove a cached dataset. Single file for perf, whole repo dir for eval.
    False if it wasn't cached."""
    entry = _BY_KEY.get(key)
    if entry is None:
        return False
    path = file_path(entry)
    if path is None:  # whole-repo dataset
        ddir = dataset_dir(entry)
        if not os.path.isdir(ddir):
            return False
        shutil.rmtree(ddir, ignore_errors=True)
        return True
    if not os.path.exists(path):
        return False
    os.remove(path)
    # Tidy now-empty dataset dir (best-effort).
    try:
        os.removedirs(os.path.dirname(path))
    except OSError:
        pass
    return True
