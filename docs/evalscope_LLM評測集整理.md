# EvalScope LLM 評測集整理

本文整理的是 EvalScope 官方文件 `LLM评测集` 頁面，並補上實際使用方式，讓你可以直接拿來選資料集、下指令，或改寫成 Python API。

查核時間：2026-06-15  
主要來源：

- https://evalscope.readthedocs.io/zh-cn/latest/get_started/supported_dataset/llm.html
- https://evalscope.readthedocs.io/zh-cn/latest/get_started/basic_usage.html
- https://evalscope.readthedocs.io/zh-cn/latest/get_started/parameters.html

---

## 1. 這頁在講什麼

`LLM评测集` 是 EvalScope 內建的 LLM 基準資料集總索引頁。這一頁本身不是單一 benchmark 教學，而是：

- 列出支援的資料集 ID
- 對應標準 benchmark 名稱
- 標上任務類別

我在 2026-06-15 依官方頁面整理，該頁共有 **111 個資料集條目**。

從分類分布來看，最多的是：

- `Knowledge`: 51
- `Reasoning`: 33
- `MCQ`: 32
- `NER`: 22
- `Math`: 14
- `Coding`: 11
- `InstructionFollowing`: 7
- `LongContext`: 6
- `QA`: 6
- `Chinese`: 5
- `MultiLingual`: 5

一句話總結：

- 如果你要做通用模型驗收，核心會落在 `Knowledge / Reasoning / MCQ / Math / Coding / LongContext / InstructionFollowing`
- 如果你要做中文能力驗收，重點看 `ceval / cmmlu / chinese_simpleqa / iquiz / maritime_bench`
- 如果你要做長上下文或 Agent 風格測試，重點看 `longbench_v2 / needle_haystack / openai_mrcr / acebench / multi_if / tool_bench`

---

## 2. 怎麼使用這些 LLM 資料集

### 2.1 CLI

最基本的用法：

```bash
evalscope eval \
  --model Qwen/Qwen2.5-0.5B-Instruct \
  --datasets gsm8k arc \
  --limit 5
```

評測 OpenAI 相容 API：

```bash
evalscope eval \
  --model qwen2.5 \
  --api-url http://127.0.0.1:8801/v1 \
  --api-key EMPTY \
  --eval-type openai_api \
  --datasets gsm8k mmlu ceval \
  --limit 20
```

### 2.2 Python API

EvalScope 的一般 LLM 評測主入口是：

```python
from evalscope.run import run_task
from evalscope.config import TaskConfig
```

最小範例：

```python
from evalscope.run import run_task
from evalscope.config import TaskConfig

task_cfg = TaskConfig(
    model="Qwen/Qwen2.5-0.5B-Instruct",
    datasets=["gsm8k", "arc"],
    limit=5,
)

run_task(task_cfg=task_cfg)
```

也可以直接傳 `dict`：

```python
from evalscope.run import run_task

task_cfg = {
    "model": "Qwen/Qwen2.5-0.5B-Instruct",
    "datasets": ["gsm8k", "arc", "mmlu"],
    "limit": 5,
}

run_task(task_cfg=task_cfg)
```

評測 OpenAI 相容 API：

```python
from evalscope.run import run_task
from evalscope.config import TaskConfig

task_cfg = TaskConfig(
    model="qwen2.5",
    api_url="http://127.0.0.1:8801/v1",
    api_key="EMPTY",
    eval_type="openai_api",
    datasets=["gsm8k", "mmlu", "ceval"],
    limit=20,
)

run_task(task_cfg=task_cfg)
```

---

## 3. 最常用參數

和 LLM 資料集最直接相關的參數：

| 參數 | 用途 |
| --- | --- |
| `datasets` / `--datasets` | 指定一個或多個資料集 |
| `limit` / `--limit` | 每個資料集最多跑多少筆；可用整數或比例 |
| `dataset_dir` / `--dataset-dir` | 資料集快取 / 下載路徑 |
| `dataset_hub` / `--dataset-hub` | 資料來源，官方文件列出 `modelscope` 與 `huggingface` |
| `repeats` / `--repeats` | 同一樣本重複生成次數 |
| `dataset_args` / `--dataset-args` | 資料集級別細部設定 |
| `generation_config` / `--generation-config` | 生成參數，例如 `temperature`、`max_tokens` |
| `eval_type` / `--eval-type` | 本地模型、OpenAI API、Responses API 等評測模式 |
| `judge_strategy` / `--judge-strategy` | 有些資料集要裁判模型時使用 |

常見 `dataset_args` 欄位：

- `dataset_id`
- `local_path`
- `prompt_template`
- `system_prompt`
- `subset_list`
- `few_shot_num`
- `few_shot_random`
- `shuffle`
- `shuffle_choices`
- `metric_list`
- `aggregation`
- `filters`
- `force_redownload`
- `extra_params`

實用範例：

```python
from evalscope.run import run_task
from evalscope.config import TaskConfig

task_cfg = TaskConfig(
    model="Qwen/Qwen3-0.6B",
    datasets=["gsm8k", "arc", "ifeval", "hle"],
    limit=10,
    repeats=3,
    generation_config={
        "do_sample": True,
        "temperature": 0.6,
        "max_tokens": 512,
    },
    dataset_args={
        "gsm8k": {
            "few_shot_num": 4,
            "few_shot_random": False,
        },
        "arc": {
            "subset_list": ["ARC-Easy", "ARC-Challenge"],
            "shuffle_choices": True,
        },
    },
)

run_task(task_cfg=task_cfg)
```

---

## 4. 我建議怎麼理解這 111 個 LLM 資料集

最實用的方式不是一個個背名稱，而是先按任務能力分：

1. 通識與知識型
2. 推理型
3. 數學型
4. 程式碼型
5. 中文能力型
6. 指令遵循型
7. 長上下文型
8. QA / 幻覺 / 事實性型
9. 工具調用 / 多輪 / Agent 相關
10. NER / 結構化抽取型
11. 多語言型
12. 客製 / 泛化資料格式型

下面用「拿來做什麼」的角度整理。

---

## 5. 依能力選資料集

### 5.1 通用基線：先拿這幾組做第一輪驗收

如果你只想先快速知道模型大概在哪個水位，我建議先從這組開始：

- `mmlu`
- `arc`
- `hellaswag`
- `gsm8k`
- `ifeval`
- `truthful_qa`

原因：

- `mmlu` 看通用知識
- `arc` 看基礎科學與 MCQ
- `hellaswag` 看常識續寫 / 推理
- `gsm8k` 看數學 chain-of-thought 能力
- `ifeval` 看指令遵循
- `truthful_qa` 看事實性與幻覺傾向

Python 範例：

```python
from evalscope.run import run_task
from evalscope.config import TaskConfig

task_cfg = TaskConfig(
    model="Qwen/Qwen2.5-7B-Instruct",
    datasets=["mmlu", "arc", "hellaswag", "gsm8k", "ifeval", "truthful_qa"],
    limit=50,
)

run_task(task_cfg)
```

### 5.2 中文能力

中文場景建議優先看：

- `ceval`
- `cmmlu`
- `chinese_simpleqa`
- `iquiz`
- `maritime_bench`

解讀：

- `ceval` / `cmmlu` 是中文知識與考試風格常用基準
- `chinese_simpleqa` 比較像直接問答
- `iquiz` 偏中文知識 / 題庫場景
- `maritime_bench` 是中文垂類知識資料集

### 5.3 推理與常識

重點資料集：

- `bbh`
- `logi_qa`
- `commonsense_qa`
- `piqa`
- `siqa`
- `musr`
- `zebralogicbench`
- `drop`

這組比較適合：

- 比較模型 reasoning 退化或升級
- 驗證推理提示詞是否真的有改善
- 比較 reasoning 模型與 instruct 模型差異

### 5.4 數學

最常用的一組：

- `gsm8k`
- `math_500`
- `competition_math`
- `minerva_math`
- `aime24`
- `aime25`
- `aime26`
- `process_bench`
- `poly_math`
- `mgsm`

建議：

- 日常 regression 用 `gsm8k + math_500`
- 高難度挑戰看 `aime24/25/26 + competition_math`
- 多語言數學看 `mgsm` 或 `poly_math`

如果你會多次採樣，可以配 `repeats` 和聚合方式。

```python
task_cfg = TaskConfig(
    model="your-math-model",
    datasets=["gsm8k", "math_500", "aime24"],
    repeats=5,
    dataset_args={
        "gsm8k": {"aggregation": "mean_and_vote_at_k"},
        "math_500": {"aggregation": "mean_and_vote_at_k"},
    },
)
```

### 5.5 程式碼

核心資料集：

- `humaneval`
- `humaneval_plus`
- `mbpp`
- `mbpp_plus`
- `live_code_bench`
- `scicode`
- `multiple_humaneval`
- `multiple_mbpp`
- `swe_bench_lite`
- `swe_bench_verified`
- `swe_bench_verified_mini`

怎麼選：

- 函數補全 / 小程式能力：`humaneval`, `mbpp`
- 測試更嚴格：`humaneval_plus`, `mbpp_plus`
- 真實工程修 bug：`swe_bench_*`
- 多語言程式：`multiple_humaneval`, `multiple_mbpp`

### 5.6 指令遵循

重點資料集：

- `ifeval`
- `ifbench`
- `cl_bench`
- `eq_bench`
- `multi_if`
- `alpaca_eval`
- `arena_hard`

怎麼看：

- `ifeval` / `ifbench` 偏嚴格指令遵循
- `multi_if` 是多語言 + 多輪方向
- `alpaca_eval` / `arena_hard` 比較偏對話回答品質 / 偏好比較

### 5.7 長上下文

重點資料集：

- `longbench_v2`
- `needle_haystack`
- `openai_mrcr`
- `aa_lcr`
- `docmath`
- `frames`

適合：

- 長文件閱讀
- 長上下文擷取
- 文中定位
- 跨段整合推理

如果你主要在測「能不能從很長的 context 找到那根針」，`needle_haystack` 最直觀。

### 5.8 QA、事實性、幻覺

重點資料集：

- `simple_qa`
- `chinese_simpleqa`
- `trivia_qa`
- `truthful_qa`
- `halueval`
- `pubmedqa`
- `health_bench`
- `hle`

適合：

- 看模型胡說八道的傾向
- 驗證 factual QA
- 看醫療 / 高風險回答可靠性

### 5.9 工具調用、函數調用、多輪

重點資料集：

- `acebench`
- `tool_bench`
- `multi_if`

這幾個要特別注意：

- 雖然它們在 LLM 頁面裡
- 但已經明顯接近 Agent / Tool Use 評測
- 如果你評的是 function calling 或工具鏈路，這幾組很值得放進 smoke test

### 5.10 結構化抽取 / NER

這一群很多，代表 EvalScope 很重視資訊抽取型任務：

- `conll2003`
- `conllpp`
- `cross_ner`
- `multi_nerd`
- `ontonotes5`
- `bc2gm`
- `bc4chemd`
- `bc5cdr`
- `genia_ner`
- `jnlpba`
- `jnlpba_rare`
- `ncbi`
- `wnut2017`
- `tweet_ner_7`
- `tweebank_ner`
- `fin_ner`
- `harvey_ner`

這組適合：

- 抽取器 / 結構化輸出模型
- 垂類知識抽取
- 醫療 / 金融 / 社群語料 NER

### 5.11 多語言

重點資料集：

- `mmmlu`
- `mgsm`
- `poly_math`
- `multi_if`
- `wmt24pp`

解讀：

- `mmmlu` 偏知識型
- `mgsm` / `poly_math` 偏數學型
- `multi_if` 偏指令遵循
- `wmt24pp` 偏翻譯

### 5.12 客製 / 泛格式資料集

這幾個很值得留意：

- `general_mcq`
- `general_qa`
- `general_arena`
- `data_collection`

它們的角色比較像：

- 給你一個標準格式容器
- 讓你把自家題庫 / 任務資料接進 EvalScope

如果你最後要做企業內部 benchmark，這組通常會比純公開 benchmark 更實用。

---

## 6. 我會怎麼組合資料集

### 6.1 通用 LLM 發版 smoke test

```python
datasets = [
    "mmlu",
    "arc",
    "hellaswag",
    "gsm8k",
    "ifeval",
    "truthful_qa",
]
```

### 6.2 中文模型 smoke test

```python
datasets = [
    "ceval",
    "cmmlu",
    "chinese_simpleqa",
    "iquiz",
]
```

### 6.3 推理模型對比組

```python
datasets = [
    "bbh",
    "logi_qa",
    "gsm8k",
    "math_500",
    "aime24",
]
```

### 6.4 程式碼模型對比組

```python
datasets = [
    "humaneval",
    "humaneval_plus",
    "mbpp",
    "live_code_bench",
]
```

### 6.5 長上下文模型對比組

```python
datasets = [
    "longbench_v2",
    "needle_haystack",
    "openai_mrcr",
    "frames",
]
```

### 6.6 企業內部知識問答 / 抽取模型驗收

```python
datasets = [
    "general_qa",
    "conll2003",
    "cross_ner",
    "truthful_qa",
]
```

---

## 7. 這頁有幾個容易誤解的地方

1. 這頁叫 `LLM评测集`，但裡面不是每個條目都只有純文字任務。
2. `refcoco` 被標成 `Grounding / ImageCaptioning / MultiModal`，嚴格說已跨到多模態。
3. `seed_tts_eval` 被標成 `Audio / TextToSpeech`，也不是典型文字問答。
4. `acebench`、`tool_bench`、`multi_if` 雖然列在 LLM 頁面，但其實已經很接近工具調用 / Agent 評測。
5. `general_mcq / general_qa / general_arena / data_collection` 比較像「通用資料格式入口」，不是傳統公開 benchmark。

所以如果你要做純文字 LLM 評測，不建議直接無差別全選這 111 個資料集。

---

## 8. 推薦工作流

### 8.1 先做小樣本驗證

```python
task_cfg = TaskConfig(
    model="Qwen/Qwen2.5-7B-Instruct",
    datasets=["mmlu", "gsm8k", "ifeval"],
    limit=10,
)
```

### 8.2 再做正式基線

```python
task_cfg = TaskConfig(
    model="Qwen/Qwen2.5-7B-Instruct",
    datasets=["mmlu", "arc", "hellaswag", "gsm8k", "ifeval", "truthful_qa"],
    limit=None,
)
```

### 8.3 最後做垂類 / 場景組

例如醫療：

```python
task_cfg = TaskConfig(
    model="your-med-model",
    datasets=["health_bench", "pubmedqa", "mri_mcqa", "biomix_qa"],
)
```

例如程式碼：

```python
task_cfg = TaskConfig(
    model="your-code-model",
    datasets=["humaneval", "humaneval_plus", "mbpp", "swe_bench_lite"],
)
```

---

## 9. 任務類別速查

| 類別 | 數量 | 代表資料集 |
| --- | ---: | --- |
| `Knowledge` | 51 | `mmlu`, `ceval`, `cmmlu`, `truthful_qa`, `hellaswag` |
| `Reasoning` | 33 | `bbh`, `gsm8k`, `logi_qa`, `aime24`, `zebralogicbench` |
| `MCQ` | 32 | `arc`, `mmlu`, `commonsense_qa`, `race`, `winogrande` |
| `NER` | 22 | `conll2003`, `ontonotes5`, `cross_ner`, `multi_nerd` |
| `Math` | 14 | `gsm8k`, `math_500`, `competition_math`, `aime24` |
| `Coding` | 11 | `humaneval`, `mbpp`, `live_code_bench`, `swe_bench_lite` |
| `InstructionFollowing` | 7 | `ifeval`, `ifbench`, `cl_bench`, `alpaca_eval` |
| `LongContext` | 6 | `longbench_v2`, `needle_haystack`, `openai_mrcr`, `frames` |
| `QA` | 6 | `simple_qa`, `chinese_simpleqa`, `trivia_qa`, `health_bench` |
| `Chinese` | 5 | `ceval`, `cmmlu`, `iquiz`, `chinese_simpleqa` |
| `MultiLingual` | 5 | `mgsm`, `mmmlu`, `poly_math`, `wmt24pp` |

---

## 10. 全量資料集索引

下表是我依官方 `LLM评测集` 頁面整理出的完整索引。

| 数据集 ID | 标准名称 | 任务类别 |
| --- | --- | --- |
| `aa_lcr` | AA-LCR | `Knowledge`, `LongContext`, `Reasoning` |
| `acebench` | ACEBench | `Agent`, `FunctionCalling`, `MultiTurn` |
| `aime24` | AIME-2024 | `Math`, `Reasoning` |
| `aime25` | AIME-2025 | `Math`, `Reasoning` |
| `aime26` | AIME-2026 | `Math`, `Reasoning` |
| `alpaca_eval` | AlpacaEval2.0 | `Arena`, `InstructionFollowing` |
| `amc` | AMC | `Math`, `Reasoning` |
| `anat_em` | AnatEM | `Knowledge`, `NER` |
| `arc` | ARC | `MCQ`, `Reasoning` |
| `arena_hard` | ArenaHard | `Arena`, `InstructionFollowing` |
| `arxivrollbench` | ArxivRollBench | `Knowledge`, `MCQ`, `Reasoning` |
| `arxivrollbench_full` | ArxivRollBench-Full | `Knowledge`, `MCQ`, `Reasoning` |
| `bbh` | BBH | `Reasoning` |
| `bc2gm` | BC2GM | `Knowledge`, `NER` |
| `bc4chemd` | BC4CHEMD | `Knowledge`, `NER` |
| `bc5cdr` | BC5CDR | `Knowledge`, `NER` |
| `biomix_qa` | BioMixQA | `Knowledge`, `MCQ`, `Medical` |
| `broad_twitter_corpus` | BroadTwitterCorpus | `Knowledge`, `NER` |
| `ceval` | C-Eval | `Chinese`, `Knowledge`, `MCQ` |
| `chinese_simpleqa` | Chinese-SimpleQA | `Chinese`, `Knowledge`, `QA` |
| `cl_bench` | CL-bench | `InstructionFollowing`, `Reasoning` |
| `cmmlu` | C-MMLU | `Chinese`, `Knowledge`, `MCQ` |
| `coin_flip` | CoinFlip | `Reasoning`, `Yes/No` |
| `commonsense_qa` | CommonsenseQA | `Commonsense`, `MCQ`, `Reasoning` |
| `competition_math` | Competition-MATH | `Math`, `Reasoning` |
| `conll2003` | CoNLL2003 | `Knowledge`, `NER` |
| `conllpp` | CoNLL++ | `Knowledge`, `NER` |
| `copious` | Copious | `Knowledge`, `NER` |
| `cross_ner` | CrossNER | `Knowledge`, `NER` |
| `data_collection` | Data-Collection | `Custom` |
| `docmath` | DocMath | `LongContext`, `Math`, `Reasoning` |
| `drivel_binary` | DrivelologyBinaryClassification | `Yes/No` |
| `drivel_multilabel` | DrivelologyMultilabelClassification | `MCQ` |
| `drivel_selection` | DrivelologyNarrativeSelection | `MCQ` |
| `drivel_writing` | DrivelologyNarrativeWriting | `Knowledge`, `Reasoning` |
| `drop` | DROP | `Reasoning` |
| `eq_bench` | EQ-Bench | `InstructionFollowing` |
| `fin_ner` | FinNER | `Knowledge`, `NER` |
| `frames` | FRAMES | `LongContext`, `Reasoning` |
| `general_arena` | GeneralArena | `Arena`, `Custom` |
| `general_mcq` | General-MCQ | `Custom`, `MCQ` |
| `general_qa` | General-QA | `Custom`, `QA` |
| `genia_ner` | GeniaNER | `Knowledge`, `NER` |
| `gpqa_diamond` | GPQA-Diamond | `Knowledge`, `MCQ` |
| `gsm8k` | GSM8K | `Math`, `Reasoning` |
| `halueval` | HaluEval | `Hallucination`, `Knowledge`, `Yes/No` |
| `harvey_ner` | HarveyNER | `Knowledge`, `NER` |
| `health_bench` | HealthBench | `Knowledge`, `Medical`, `QA` |
| `hellaswag` | HellaSwag | `Commonsense`, `Knowledge`, `MCQ` |
| `hle` | Humanity's-Last-Exam | `Knowledge`, `QA` |
| `hmmt25` | HMMT25 | `Math`, `Reasoning` |
| `humaneval` | HumanEval | `Coding` |
| `humaneval_plus` | HumanEvalPlus | `Coding` |
| `ifbench` | IFBench | `InstructionFollowing` |
| `ifeval` | IFEval | `InstructionFollowing` |
| `iquiz` | IQuiz | `Chinese`, `Knowledge`, `MCQ` |
| `jnlpba` | JNLPBA | `Knowledge`, `NER` |
| `jnlpba_rare` | JNLPBA-Rare | `Knowledge`, `NER` |
| `live_code_bench` | Live-Code-Bench | `Coding` |
| `logi_qa` | LogiQA | `MCQ`, `Reasoning` |
| `longbench_v2` | LongBench-v2 | `LongContext`, `MCQ`, `ReadingComprehension` |
| `maritime_bench` | MaritimeBench | `Chinese`, `Knowledge`, `MCQ` |
| `math_500` | MATH-500 | `Math`, `Reasoning` |
| `math_qa` | MathQA | `MCQ`, `Math`, `Reasoning` |
| `mbpp` | MBPP | `Coding` |
| `mbpp_plus` | MBPP-Plus | `Coding` |
| `med_mcqa` | Med-MCQA | `Knowledge`, `MCQ` |
| `mgsm` | MGSM | `Math`, `MultiLingual`, `Reasoning` |
| `minerva_math` | Minerva-Math | `Math`, `Reasoning` |
| `mit_movie_trivia` | MIT-Movie-Trivia | `Knowledge`, `NER` |
| `mit_restaurant` | MIT-Restaurant | `Knowledge`, `NER` |
| `mmlu` | MMLU | `Knowledge`, `MCQ` |
| `mmlu_pro` | MMLU-Pro | `Knowledge`, `MCQ` |
| `mmlu_redux` | MMLU-Redux | `Knowledge`, `MCQ` |
| `mmmlu` | MMMLU | `Knowledge`, `MCQ`, `MultiLingual` |
| `mri_mcqa` | MRI-MCQA | `Knowledge`, `MCQ`, `Medical` |
| `multi_if` | Multi-IF | `InstructionFollowing`, `MultiLingual`, `MultiTurn` |
| `multi_nerd` | MultiNERD | `Knowledge`, `NER` |
| `multiple_humaneval` | MultiPL-E HumanEval | `Coding` |
| `multiple_mbpp` | MultiPL-E MBPP | `Coding` |
| `music_trivia` | MusicTrivia | `Knowledge`, `MCQ` |
| `musr` | MuSR | `MCQ`, `Reasoning` |
| `ncbi` | NCBI | `Knowledge`, `NER` |
| `needle_haystack` | Needle-in-a-Haystack | `LongContext`, `Retrieval` |
| `ontonotes5` | OntoNotes5 | `Knowledge`, `NER` |
| `openai_mrcr` | OpenAI MRCR | `LongContext`, `Retrieval` |
| `piqa` | PIQA | `Commonsense`, `MCQ`, `Reasoning` |
| `poly_math` | PolyMath | `Math`, `MultiLingual`, `Reasoning` |
| `process_bench` | ProcessBench | `Math`, `Reasoning` |
| `pubmedqa` | PubMedQA | `Knowledge`, `Yes/No` |
| `qasc` | QASC | `Knowledge`, `MCQ` |
| `race` | RACE | `MCQ`, `Reasoning` |
| `refcoco` | RefCOCO | `Grounding`, `ImageCaptioning`, `Knowledge`, `MultiModal` |
| `scicode` | SciCode | `Coding` |
| `sciq` | SciQ | `Knowledge`, `MCQ`, `ReadingComprehension` |
| `seed_tts_eval` | Seed-TTS-Eval | `Audio`, `TextToSpeech` |
| `simple_qa` | SimpleQA | `Knowledge`, `QA` |
| `siqa` | SIQA | `Commonsense`, `MCQ`, `Reasoning` |
| `super_gpqa` | SuperGPQA | `Knowledge`, `MCQ` |
| `swe_bench_lite` | SWE-bench_Lite | `Coding` |
| `swe_bench_verified` | SWE-bench_Verified | `Coding` |
| `swe_bench_verified_mini` | SWE-bench_Verified_mini | `Coding` |
| `tool_bench` | ToolBench-Static | `FunctionCalling`, `Reasoning` |
| `trivia_qa` | TriviaQA | `QA`, `ReadingComprehension` |
| `truthful_qa` | TruthfulQA | `Knowledge` |
| `tweebank_ner` | TweeBankNER | `Knowledge`, `NER` |
| `tweet_ner_7` | TweetNER7 | `Knowledge`, `NER` |
| `winogrande` | Winogrande | `MCQ`, `Reasoning` |
| `wmt24pp` | WMT2024++ | `MachineTranslation`, `MultiLingual` |
| `wnut2017` | WNUT2017 | `Knowledge`, `NER` |
| `zebralogicbench` | ZebraLogicBench | `Reasoning` |

---

## 11. 結論

如果你只是要做 LLM 一般能力驗收，不要一開始就跑全量 111 個。

我建議你先分三層：

1. 基線組：`mmlu + arc + hellaswag + gsm8k + ifeval + truthful_qa`
2. 場景組：中文、程式碼、長上下文、醫療、NER 擇一追加
3. 企業組：再把 `general_mcq / general_qa / general_arena` 或自家資料接進來

這樣最實用，也最接近真實模型選型流程。

