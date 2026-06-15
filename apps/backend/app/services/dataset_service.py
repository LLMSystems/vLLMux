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
