# EvalScope 模型壓測方法整理（Python API 版）

本文根據我於 2026-06-15 查核的 EvalScope `latest` 官方文件整理，重點只放在「模型推理性能壓測（perf）」這條線，並統一改寫成 Python API 用法。

適用範圍：

- OpenAI 相容聊天 / completions / responses 端點
- Embedding / Rerank 壓測
- 單輪、開放環路（open-loop）、多輪對話、長上下文、速度基準、SLA 自動調優
- 本地 `transformers` 與本地 `vLLM` 推理

不在本文重點內：

- 一般能力評測 `run_task`
- Arena、AIGC、Agent 評測

---

## 1. 先講結論：EvalScope 壓測的核心 API 是什麼

Perf 壓測主入口是：

```bash
pip install evalscope
```

```python
from evalscope.perf.arguments import Arguments
from evalscope.perf.main import run_perf_benchmark
```

最常見的呼叫方式有兩種：

```python
results = run_perf_benchmark(
    Arguments(
        model="Qwen2.5-0.5B-Instruct",
        url="http://127.0.0.1:8801/v1/chat/completions",
        api="openai",
        dataset="openqa",
        parallel=10,
        number=100,
        stream=True,
    )
)
```

或直接傳 `dict`：

```python
results = run_perf_benchmark(
    {
        "model": "Qwen2.5-0.5B-Instruct",
        "url": "http://127.0.0.1:8801/v1/chat/completions",
        "api": "openai",
        "dataset": "openqa",
        "parallel": 10,
        "number": 100,
        "stream": True,
    }
)
```

`run_perf_benchmark()` 依官方原始碼可接受：

- `Arguments`
- `dict`
- `argparse.Namespace`

回傳值是結果字典，並且會把完整報告寫到輸出目錄。

---

## 2. 安裝與最小可跑骨架

安裝：

```bash
pip install -U evalscope[perf]
```

最小骨架：

```python
from evalscope.perf.arguments import Arguments
from evalscope.perf.main import run_perf_benchmark


def main():
    args = Arguments(
        model="qwen2.5",
        url="http://127.0.0.1:8000/v1/chat/completions",
        api="openai",
        dataset="openqa",
        parallel=1,
        number=10,
        max_tokens=128,
        stream=True,
    )
    results = run_perf_benchmark(args)
    print(results)


if __name__ == "__main__":
    main()
```

建議你把 `stream=True` 明確寫上。官方文件說明：要量到 `TTFT`，必須啟用串流。

---

## 3. CLI 參數到 Python API 欄位對照

大原則是：

- CLI 的 `--xxx-yyy` 幾乎都對應成 Python 的 `xxx_yyy`
- 單值或多值都可傳；原始碼內部會把 `number / parallel / rate` 正規化成 list

常用對照：

| CLI | Python `Arguments` |
| --- | --- |
| `--model` | `model` |
| `--url` | `url` |
| `--api` | `api` |
| `--api-key` | `api_key` |
| `--parallel` | `parallel` |
| `--number` | `number` |
| `--rate` | `rate` |
| `--stream` | `stream` |
| `--dataset` | `dataset` |
| `--dataset-path` | `dataset_path` |
| `--tokenizer-path` | `tokenizer_path` |
| `--min-prompt-length` | `min_prompt_length` |
| `--max-prompt-length` | `max_prompt_length` |
| `--prefix-length` | `prefix_length` |
| `--min-tokens` | `min_tokens` |
| `--max-tokens` | `max_tokens` |
| `--extra-args` | `extra_args` |
| `--query-template` | `query_template` |
| `--open-loop` | `open_loop` |
| `--warmup-num` | `warmup_num` |
| `--duration` | `duration` |
| `--tokenize-prompt` | `tokenize_prompt` |
| `--multi-turn` | `multi_turn` |
| `--min-turns` | `min_turns` |
| `--max-turns` | `max_turns` |
| `--multi-turn-args` | `multi_turn_args` |
| `--dataset-offset` | `dataset_offset` |
| `--sla-auto-tune` | `sla_auto_tune` |
| `--sla-variable` | `sla_variable` |
| `--sla-params` | `sla_params` |
| `--sla-upper-bound` | `sla_upper_bound` |
| `--sla-lower-bound` | `sla_lower_bound` |
| `--sla-fixed-parallel` | `sla_fixed_parallel` |
| `--sla-num-runs` | `sla_num_runs` |
| `--sla-number-multiplier` | `sla_number_multiplier` |
| `--outputs-dir` | `outputs_dir` |
| `--visualizer` | `visualizer` |
| `--name` | `name` |
| `--debug` | `debug` |

---

## 4. EvalScope 壓測到底有哪些方法

實務上可以把它分成 8 類：

1. 單輪 closed-loop 壓測
2. 多組並發 sweep 壓測
3. open-loop 速率壓測
4. warmup 預熱壓測
5. 多輪對話 / 長上下文壓測
6. 速度基準測試
7. SLA 自動調優
8. 特殊模型壓測：VL / Embedding / Rerank / 本地模型 / 客製 API

下面逐一整理成 Python API。

---

## 5. 方法一：單輪 closed-loop 壓測

這是最標準的模式。每個 worker 送出一個請求後，會等待回應，再送下一個請求。這種模式有背壓保護，最適合做服務穩定性與飽和前曲線分析。

### 5.1 用真實資料集 `openqa`

```python
from evalscope.perf.arguments import Arguments
from evalscope.perf.main import run_perf_benchmark

args = Arguments(
    model="qwen2.5",
    url="http://127.0.0.1:8000/v1/chat/completions",
    api="openai",
    dataset="openqa",
    parallel=10,
    number=200,
    max_tokens=256,
    temperature=0.0,
    stream=True,
)

results = run_perf_benchmark(args)
```

適合：

- API 服務初次驗收
- 看固定並發下的延遲、TTFT、TPOT、成功率

### 5.2 用隨機資料集 `random`

`random` 是官方最常用的壓測資料集，會依設定自動產生指定長度的 prompt。

```python
args = Arguments(
    model="Qwen2.5-0.5B-Instruct",
    url="http://127.0.0.1:8801/v1/chat/completions",
    api="openai",
    dataset="random",
    parallel=20,
    number=100,
    min_tokens=128,
    max_tokens=128,
    prefix_length=64,
    min_prompt_length=1024,
    max_prompt_length=2048,
    tokenizer_path="Qwen/Qwen2.5-0.5B-Instruct",
    stream=True,
    debug=True,
)
results = run_perf_benchmark(args)
```

重點：

- `random` 幾乎一定要配 `tokenizer_path`
- prompt 長度分布由 `prefix_length + min_prompt_length` 到 `prefix_length + max_prompt_length`
- 同一輪測試內所有請求的 prefix 相同，適合觀察 prefix cache 影響

### 5.3 要求服務端收到精確 token 數

如果你很在意「服務端實際收到的 prompt token 數」要和設定完全對齊，可打開 `tokenize_prompt=True`。

```python
args = Arguments(
    model="qwen2.5",
    url="http://127.0.0.1:8000/v1/completions",
    api="openai",
    dataset="random",
    parallel=8,
    number=100,
    min_tokens=128,
    max_tokens=128,
    prefix_length=64,
    min_prompt_length=1024,
    max_prompt_length=1024,
    tokenizer_path="Qwen/Qwen2.5-7B-Instruct",
    tokenize_prompt=True,
    stream=True,
)
```

這會在客戶端先 tokenize，再把 token IDs 直接送到 `/v1/completions`，用來繞過服務端重新 tokenize 的誤差。

適合：

- vLLM / SGLang / LMDeploy
- 精確對齊 token 長度的壓測對比

---

## 6. 方法二：多組並發 sweep 壓測

這是最推薦的日常壓測方式。一次掃多個並發點，快速畫出吞吐與延遲的 trade-off。

```python
args = Arguments(
    parallel=[1, 10, 50, 100, 200],
    number=[10, 20, 100, 200, 400],
    model="Qwen2.5-0.5B-Instruct",
    url="http://127.0.0.1:8801/v1/chat/completions",
    api="openai",
    dataset="random",
    min_tokens=1024,
    max_tokens=1024,
    prefix_length=0,
    min_prompt_length=1024,
    max_prompt_length=1024,
    tokenizer_path="Qwen/Qwen2.5-0.5B-Instruct",
    extra_args={"ignore_eos": True},
    stream=True,
)
results = run_perf_benchmark(args)
```

判讀方式：

- `parallel` 拉高後，`RPS / TPS` 先上升再趨緩
- `p99 latency / p99 TTFT` 一旦明顯惡化，通常代表快碰到服務飽和點
- 這種模式最後會產生彙總報告，方便你比較不同並發

---

## 7. 方法三：客製請求體壓測

如果你不是純 OpenAI chat 預設格式，而是想把一些欄位直接塞進請求體，官方提供兩種做法。

### 7.1 直接用一般生成參數

```python
args = Arguments(
    model="qwen2.5",
    url="http://127.0.0.1:8000/v1/chat/completions",
    api="openai",
    dataset="openqa",
    parallel=2,
    number=20,
    min_prompt_length=128,
    max_prompt_length=128000,
    max_tokens=1024,
    temperature=0.7,
    stop=["<|im_end|>"],
    log_every_n_query=10,
    connect_timeout=120,
    read_timeout=120,
    stream=True,
)
results = run_perf_benchmark(args)
```

### 7.2 用 `query_template`

當你要完全控制請求 JSON 時，用 `query_template` 最方便。

```python
import json

template = {
    "model": "%m",
    "messages": [{"role": "user", "content": "%p"}],
    "stream": True,
    "skip_special_tokens": False,
    "stop": ["<|im_end|>"],
    "temperature": 0.7,
    "max_tokens": 1024,
}

args = Arguments(
    model="qwen2.5",
    url="http://127.0.0.1:8000/v1/chat/completions",
    api="openai",
    dataset="openqa",
    parallel=2,
    number=20,
    min_prompt_length=128,
    max_prompt_length=128000,
    query_template=json.dumps(template, ensure_ascii=False),
)
results = run_perf_benchmark(args)
```

模板變數：

- `%m` 會替換成模型名
- `%p` 會替換成 prompt

適合：

- 非標準 body 欄位
- 想在壓測時固定某些 vendor-specific 參數

---

## 8. 方法四：Open-loop 開放環路壓測

Open-loop 模式不是等回應再送下一個，而是按照設定速率持續發送請求。這更接近真實線上流量。

官方定義：

- `rate` 是請求到達速率
- 請求按泊松到達調度
- `parallel` 在這個模式下被忽略
- `number` 必須和 `rate` 等長

```python
args = Arguments(
    model="qwen2.5",
    url="http://127.0.0.1:8000/v1/chat/completions",
    api="openai",
    dataset="openqa",
    open_loop=True,
    rate=[5, 10, 20],
    number=[500, 1000, 2000],
    max_tokens=1024,
    stream=True,
)
results = run_perf_benchmark(args)
```

適合：

- 模擬線上流量
- 掃 throughput-latency 曲線
- 比較不同限流 / 排隊 / autoscaling 策略

注意：

- `rate` 必須大於 0
- 高速率下可能堆積大量 in-flight requests
- 這個模式沒有 closed-loop 的自然背壓

---

## 9. 方法五：Warmup 預熱壓測

Warmup 用來排除冷啟動影響，例如：

- KV cache 初次建立
- JIT 編譯
- 連線池初始化

### 9.1 固定預熱數量

```python
args = Arguments(
    model="qwen2.5",
    url="http://127.0.0.1:8000/v1/chat/completions",
    api="openai",
    dataset="openqa",
    parallel=10,
    number=100,
    warmup_num=10,
    stream=True,
)
results = run_perf_benchmark(args)
```

### 9.2 比例式預熱

```python
args = Arguments(
    model="qwen2.5",
    url="http://127.0.0.1:8000/v1/chat/completions",
    api="openai",
    dataset="openqa",
    parallel=10,
    number=100,
    warmup_num=0.1,
    stream=True,
)
results = run_perf_benchmark(args)
```

規則：

- `warmup_num = 0`：關閉
- `warmup_num >= 1`：絕對數量
- `0 < warmup_num < 1`：按 `number` 比例計算

預熱資料不計入最終性能指標。

---

## 10. 方法六：多輪對話壓測

這是 EvalScope 很重要的一條線。它不是單純重播訊息，而是把「模型真實回覆」接回上下文，再送下一輪，能更接近實際聊天與 Agent 場景。

### 10.1 多輪模式的核心語義

依專門的多輪文件頁面，建議這樣理解：

- `multi_turn=True`
- `number` = 總對話數
- `parallel` = 並發對話數

注意：官方「參數總表」有一處把 `number/parallel` 寫成「總 turn 數 / 並發 turn 數」，但多輪專頁與原始碼都把它定義成「對話數 / 並發對話數」。我建議以多輪專頁與原始碼為準。

### 10.2 隨機多輪：`random_multi_turn`

```python
args = Arguments(
    model="qwen2.5",
    url="http://127.0.0.1:8000/v1/chat/completions",
    api="openai",
    dataset="random_multi_turn",
    tokenizer_path="Qwen/Qwen2.5-7B-Instruct",
    multi_turn=True,
    min_turns=2,
    max_turns=5,
    number=20,
    parallel=10,
    max_tokens=256,
    stream=True,
)
results = run_perf_benchmark(args)
```

適合：

- 先驗證多輪壓測框架是否正常
- 看上下文逐輪增長時的延遲變化

### 10.3 真實聊天資料：`share_gpt_zh_multi_turn`

```python
args = Arguments(
    model="YOUR_MODEL",
    url="OPENAI_API_COMPAT_URL",
    api="openai",
    dataset="share_gpt_zh_multi_turn",
    max_tokens=512,
    multi_turn=True,
    max_turns=3,
    number=300,
    parallel=10,
    stream=True,
)
results = run_perf_benchmark(args)
```

如果你想指定本地資料：

```python
args = Arguments(
    model="YOUR_MODEL",
    url="OPENAI_API_COMPAT_URL",
    api="openai",
    dataset="share_gpt_zh_multi_turn",
    dataset_path="D:/data/common_zh_70k.jsonl",
    max_tokens=512,
    multi_turn=True,
    max_turns=3,
    number=300,
    parallel=10,
    stream=True,
)
```

本地 JSONL 格式（每行一條 conversation）：

```json
{"conversation": [{"human": "你好", "assistant": "你好！"}, {"human": "幫我寫一首詩", "assistant": "好的"}]}
```

重要觀念：

- 資料裡的 `assistant` 文字只用來保留對話結構
- 執行時真正接回上下文的是「模型當下實際輸出」

### 10.4 直接重放 OpenAI messages：`custom_multi_turn`

這個很實用，因為很多團隊本來就有 chat logs。

```python
args = Arguments(
    model="YOUR_MODEL",
    url="OPENAI_API_COMPAT_URL",
    api="openai",
    dataset="custom_multi_turn",
    dataset_path="D:/data/my_conversations.jsonl",
    max_tokens=512,
    multi_turn=True,
    max_turns=3,
    number=100,
    parallel=10,
    stream=True,
)
results = run_perf_benchmark(args)
```

JSONL 每行格式：

```json
[{"role": "user", "content": "你好"}, {"role": "assistant", "content": "你好！"}, {"role": "user", "content": "再介紹一下台北"}]
```

要求：

- 每行必須是 JSON array
- 每個元素都要有 `role` 和 `content`
- `role` 只能是 `user` 或 `assistant`
- 至少有一條 `user`

### 10.5 長上下文 Agent 軌跡：`swe_smith`

這是官方用來測長上下文 + 多輪 Agent 的重型資料集。

#### Live 構建模式

```python
args = Arguments(
    model="YOUR_MODEL",
    url="OPENAI_API_COMPAT_URL",
    api="openai",
    dataset="swe_smith",
    tokenizer_path="YOUR_MODEL",
    max_tokens=[512, 1024],
    min_tokens=512,
    multi_turn=True,
    multi_turn_args={
        "first_turn_length": [4096, 8192],
        "subsequent_turn_length": [512, 1024],
    },
    min_turns=3,
    max_turns=8,
    seed=42,
    number=[10, 20],
    parallel=[5, 10],
    extra_args={"ignore_eos": True},
    stream=True,
)
results = run_perf_benchmark(args)
```

#### 預構建 JSON 模式

```python
args = Arguments(
    model="YOUR_MODEL",
    url="OPENAI_API_COMPAT_URL",
    api="openai",
    dataset="swe_smith",
    dataset_path="D:/data/agentic_dataset.json",
    max_tokens=512,
    multi_turn=True,
    dataset_offset=100,
    number=200,
    parallel=20,
    stream=True,
)
results = run_perf_benchmark(args)
```

如果你要先建資料，官方提供的是前處理指令，不是 `run_perf_benchmark` 本體的一部分：

```bash
python examples/perf/build_swe_smith_dataset.py \
  --model-path Qwen/Qwen2.5-7B-Instruct \
  --first-turn-length 8192 \
  --subsequent-turn-length 1024 \
  --min-turns 3 \
  --max-turns 8 \
  --number 128 \
  --output-path outputs/agentic_dataset.json \
  --seed 42 \
  --num-workers 8
```

### 10.6 Trie trace 重放：`trie_*`

官方還有幾個偏生產 trace 的資料集：

- `trie_agentic_coding`
- `trie_code_qa`
- `trie_office_work`

其中 `trie_office_work` 最重，適合壓上限。

```python
args = Arguments(
    model="qwen-plus",
    url="https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    api_key="YOUR_API_KEY",
    api="openai",
    dataset="trie_office_work",
    multi_turn=True,
    parallel=4,
    number=10,
    duration=600,
    tokenizer_path="Qwen/Qwen2.5-7B-Instruct",
    extra_args={"ignore_eos": True},
    stream=True,
)
results = run_perf_benchmark(args)
```

---

## 11. 方法七：多輪模式下的 `duration` 軟退出

多輪時 `duration` 很重要，語義是：

- 到時間後不再啟動新對話
- 已開始的對話會把剩餘 turn 全跑完

```python
args = Arguments(
    model="YOUR_MODEL",
    url="OPENAI_API_COMPAT_URL",
    api="openai",
    dataset="custom_multi_turn",
    dataset_path="D:/data/my_conversations.jsonl",
    multi_turn=True,
    number=50,
    duration=300,
    parallel=10,
    max_tokens=512,
    stream=True,
)
```

建議：

- 多輪壓測最好同時設 `number` 和 `duration`
- 這樣可以避免資料太少或太多時，牆鐘時間失控

---

## 12. 方法八：速度基準測試

這個不是壓「併發容量」，而是量「單請求條件下的標準速度」。

官方資料集：

- `speed_benchmark`
  - prompt 長度：`[1, 6144, 14336, 30720]`
  - 每種長度 2 條
  - 固定輸出 2048 tokens
- `speed_benchmark_long`
  - prompt 長度：`[63488, 129024]`
  - 每種長度 2 條
  - 固定輸出 2048 tokens

官方特別提醒：速度基準測試要打 `/v1/completions`，不要打 `/v1/chat/completions`，避免 chat template 干擾。

```python
args = Arguments(
    model="qwen2.5",
    url="http://127.0.0.1:8000/v1/completions",
    api="openai",
    dataset="speed_benchmark",
    parallel=1,
    number=8,
    max_tokens=2048,
    min_tokens=2048,
    log_every_n_query=5,
    connect_timeout=6000,
    read_timeout=6000,
    stream=True,
)
results = run_perf_benchmark(args)
```

適合：

- 跟官方 benchmark 對齊
- 比較不同模型 / 不同服務框架的純速度

---

## 13. 方法九：SLA 自動調優

SLA auto-tune 是 EvalScope 很有價值的功能。它不是固定壓某個並發，而是自動找出：

- 在某個 SLA 限制下，最大可承受並發
- 或最大可承受速率
- 或 TPS 最大點

底層方法是二分搜尋。

### 13.1 核心欄位

```python
args = Arguments(
    ...,
    sla_auto_tune=True,
    sla_variable="parallel",     # 或 "rate"
    sla_params=[{"p99_latency": "<=2"}],
    sla_lower_bound=1,
    sla_upper_bound=64,
    sla_num_runs=3,
)
```

支援指標：

- 延遲類：`avg_latency`, `p99_latency`, `avg_ttft`, `p99_ttft`, `avg_tpot`, `p99_tpot`
- 吞吐類：`rps`, `tps`

支援操作：

- 延遲類：`<=`, `<`, `min`
- 吞吐類：`>=`, `>`, `max`

### 13.2 `sla_params` 的邏輯

Python 版請直接傳 list of dict：

```python
sla_params = [{"avg_ttft": "<=2", "avg_tpot": "<=0.05"}]
```

規則：

- 同一個 dict 內：AND
- 不同 dict 之間：OR

例如：

```python
sla_params = [
    {"avg_ttft": "<=1", "avg_tpot": "<=0.05"},
    {"p99_latency": "<=5"},
]
```

意思是：

- 第一組條件獨立搜尋
- 第二組條件也獨立搜尋

### 13.3 找滿足 `p99_latency <= 2s` 的最大並發

```python
args = Arguments(
    model="Qwen2.5-0.5B-Instruct",
    tokenizer_path="Qwen/Qwen2.5-0.5B-Instruct",
    url="http://127.0.0.1:8801/v1/chat/completions",
    api="openai",
    dataset="random",
    max_tokens=1024,
    prefix_length=0,
    min_prompt_length=1024,
    max_prompt_length=1024,
    sla_auto_tune=True,
    sla_variable="parallel",
    sla_params=[{"p99_latency": "<=2"}],
    parallel=2,
    sla_upper_bound=64,
    stream=True,
)
results = run_perf_benchmark(args)
```

### 13.4 找 TPS 最大的並發

```python
args = Arguments(
    model="Qwen2.5-0.5B-Instruct",
    tokenizer_path="Qwen/Qwen2.5-0.5B-Instruct",
    url="http://127.0.0.1:8801/v1/chat/completions",
    api="openai",
    dataset="random",
    max_tokens=1024,
    prefix_length=0,
    min_prompt_length=1024,
    max_prompt_length=1024,
    sla_auto_tune=True,
    sla_variable="parallel",
    sla_params=[{"tps": "max"}],
    parallel=4,
    stream=True,
)
results = run_perf_benchmark(args)
```

### 13.5 固定並發，找滿足 TTFT 的最大速率

```python
args = Arguments(
    model="Qwen2.5-0.5B-Instruct",
    tokenizer_path="Qwen/Qwen2.5-0.5B-Instruct",
    url="http://127.0.0.1:8801/v1/chat/completions",
    api="openai",
    dataset="random",
    max_tokens=512,
    prefix_length=0,
    min_prompt_length=512,
    max_prompt_length=512,
    sla_auto_tune=True,
    sla_variable="rate",
    sla_params=[{"p99_ttft": "<0.05"}, {"p99_ttft": "<0.01"}],
    rate=2,
    sla_num_runs=1,
    sla_fixed_parallel=40,
    sla_lower_bound=10,
    sla_upper_bound=40,
    stream=True,
)
results = run_perf_benchmark(args)
```

注意：

- `sla_variable="rate"` 時，最好顯式給 `sla_fixed_parallel`
- 成功率若低於 100%，該測點會被視為違反 SLA

---

## 14. 方法十：多模態 / Embedding / Rerank 壓測

### 14.1 `random_vl`

```python
args = Arguments(
    model="Qwen2.5-VL-3B-Instruct",
    url="http://127.0.0.1:8801/v1/chat/completions",
    api="openai",
    dataset="random_vl",
    parallel=20,
    number=100,
    min_tokens=128,
    max_tokens=128,
    prefix_length=0,
    min_prompt_length=100,
    max_prompt_length=100,
    image_width=512,
    image_height=512,
    image_format="RGB",
    image_num=1,
    tokenizer_path="Qwen/Qwen2.5-VL-3B-Instruct",
    stream=True,
    debug=True,
)
results = run_perf_benchmark(args)
```

### 14.2 Embedding 模型

```python
args = Arguments(
    model="text-embedding-v4",
    url="https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings",
    api_key="YOUR_API_KEY",
    api="openai_embedding",
    dataset="random_embedding",
    parallel=2,
    number=10,
    min_prompt_length=256,
    max_prompt_length=256,
    tokenizer_path="Qwen/Qwen3-Embedding-0.6B",
)
results = run_perf_benchmark(args)
```

### 14.3 Rerank 模型

```python
args = Arguments(
    model="qwen3-rerank",
    url="https://dashscope.aliyuncs.com/compatible-api/v1/reranks",
    api_key="YOUR_API_KEY",
    api="openai_rerank",
    dataset="random_rerank",
    parallel=2,
    number=10,
    min_prompt_length=256,
    max_prompt_length=256,
    tokenizer_path="Qwen/Qwen3-Embedding-0.6B",
    extra_args={
        "num_documents": 5,
        "document_length_ratio": 3,
    },
)
results = run_perf_benchmark(args)
```

---

## 15. 方法十一：本地模型壓測

### 15.1 `transformers` 本地推理

不需要 `url`。

```python
args = Arguments(
    model="Qwen/Qwen2.5-0.5B-Instruct",
    api="local",
    attn_implementation="flash_attention_2",
    number=20,
    parallel=2,
    dataset="openqa",
)
results = run_perf_benchmark(args)
```

`attn_implementation` 可選：

- `flash_attention_2`
- `eager`
- `sdpa`

### 15.2 本地 `vLLM`

```python
args = Arguments(
    model="Qwen/Qwen2.5-0.5B-Instruct",
    api="local_vllm",
    number=20,
    parallel=2,
    dataset="openqa",
)
results = run_perf_benchmark(args)
```

---

## 16. 方法十二：客製 API 與客製資料集

這一段是進階玩法：當你的服務不完全相容 OpenAI，或你想自己定義資料來源。

### 16.1 客製 API 插件

官方建議你繼承預設 API 插件，實作請求構造與回應解析，再註冊：

```python
from evalscope.perf.arguments import Arguments
from evalscope.perf.main import run_perf_benchmark

args = Arguments(
    model="your-model",
    url="https://your-endpoint",
    api_key="YOUR_API_KEY",
    api="custom",
    dataset="openqa",
    number=1,
    max_tokens=16,
    stream=True,
    debug=True,
)

run_perf_benchmark(args)
```

如果你的串流協定不相容 OpenAI SSE，官方文件明確建議你自行實作 `process_request(...) -> BenchmarkData`。

### 16.2 客製資料集插件

官方示意是繼承 `DatasetPluginBase`，再用 `@register_dataset("custom")` 註冊，然後在 `build_messages()` 內輸出 OpenAI messages 結構。

適合：

- 你有自家業務 prompt 集
- 你想做固定格式 request replay

---

## 17. 輸出結果怎麼看

單輪與多輪壓測最常看的有 4 類輸出：

1. `Performance Overview`
2. `Per-Request Metrics`
3. `Per-Trace Metrics`（只在多輪）
4. `Workload Throughput`（只在多輪）

### 17.1 `Performance Overview`

看這幾個：

- `RPS`
- `Gen/s`
- `Success`

它是最上層的總覽表。

### 17.2 `Per-Request Metrics`

最重要：

- `Latency (s)`
- `TTFT (ms)`
- `ITL (ms)`
- `TPOT (ms)`
- `Input Tokens`
- `Output Tokens`

怎麼解讀：

- `TTFT`：首 token 延遲，反映首包時間
- `TPOT`：單 token 生成時間，反映解碼速度
- `ITL`：token 間隔，反映輸出是否平穩
- `p99` 比 `avg` 更能看尾延遲問題

### 17.3 多輪專屬指標

- `Turns/Req`
- `Cache Hit (%)`
- `First-Turn TTFT (ms)`
- `Subsequent-Turn TTFT (ms)`

判讀：

- `Turns/Req` 越高，代表上下文累積越深
- `Cache Hit (%)` 越高，代表理論上可被 prefix cache 利用的歷史 token 比例越高
- `First-Turn TTFT` 通常遠高於 `Subsequent-Turn TTFT`

### 17.4 Workload Throughput

這張是多輪壓測很有價值的全局表，會拆成：

- `Overall`
- `Last 30s`
- `Steady (drop 20%)`

建議：

- 看穩態能力時，以 `Steady` 為主
- 看尾部抖動時，以 `Last 30s` 為主

---

## 18. 輸出檔案與結果保存

依官方文件與原始碼，結果通常會寫到：

- `outputs/<timestamp>/<model>/...`

常見輸出：

- `benchmark.log`
- `performance_summary.txt`
- HTML report
- sqlite DB
- 多輪時的 `workload_throughput.json`
- 多輪時的 `workload_timeline.json`

如果要指定輸出目錄：

```python
args = Arguments(
    ...,
    outputs_dir="./outputs",
    name="qwen25_perf_run",
)
```

如果要接可視化平台：

```python
args = Arguments(
    ...,
    visualizer="wandb",   # 或 swanlab / clearml
    name="perf_experiment_01",
)
```

---

## 19. 我建議你怎麼選壓測方法

### 19.1 如果你是新服務上線前驗收

先做 3 步：

1. `openqa` 單輪 fixed concurrency
2. `random` 並發 sweep
3. `warmup + random` 再跑一次正式結果

### 19.2 如果你要逼近線上真實流量

優先：

1. `open_loop=True`
2. 真實資料集或 `custom_multi_turn`
3. `duration` 拉長

### 19.3 如果你是聊天機器人或 Agent

優先：

1. `share_gpt_*_multi_turn`
2. `custom_multi_turn`
3. `swe_smith` 或 `trie_*`

### 19.4 如果你想直接找服務容量上限

優先：

1. `sla_auto_tune=True`
2. 先用 `p99_latency`
3. 再用 `tps=max`

---

## 20. 重要踩坑

1. `TTFT` 要可用，請顯式設 `stream=True`。
2. 速度基準請打 `/v1/completions`，不要打 chat 端點。
3. `random` / `random_embedding` / `random_rerank` / `random_multi_turn` 幾乎都要 `tokenizer_path`。
4. `open_loop=True` 時，`parallel` 不控制併發，真正控制的是 `rate`。
5. 多輪模式下，請把 `number` 理解成「對話數」，不要照參數總表那個「turn 數」理解。
6. `tokenize_prompt=True` 只適合支援 token IDs 輸入的 completions 服務，不適用 `random_vl`。
7. 若要比較不同框架的純能力，先固定 `prompt/output token`，不要混入太多資料分布變數。
8. 若要排除冷啟動，請使用 `warmup_num`，不要只跑一次就直接看 p99。

---

## 21. 一份我建議直接開跑的模板

如果你要先做一版「夠準、夠實用」的通用壓測，我建議先從這份開始：

```python
from evalscope.perf.arguments import Arguments
from evalscope.perf.main import run_perf_benchmark


args = Arguments(
    model="Qwen2.5-7B-Instruct",
    url="http://127.0.0.1:8000/v1/chat/completions",
    api="openai",
    dataset="random",
    parallel=[1, 4, 8, 16, 32],
    number=[20, 50, 100, 200, 400],
    min_prompt_length=1024,
    max_prompt_length=1024,
    min_tokens=256,
    max_tokens=256,
    prefix_length=0,
    tokenizer_path="Qwen/Qwen2.5-7B-Instruct",
    warmup_num=0.1,
    stream=True,
    extra_args={"ignore_eos": True},
    debug=False,
)

results = run_perf_benchmark(args)
print(results)
```

這份模板的好處：

- 固定 input/output token，對比公平
- 有 warmup
- 有多並發點
- 有 TTFT
- 容易擴展到 SLA 或 open-loop

---

## 22. 參考來源

官方文件：

- https://evalscope.readthedocs.io/zh-cn/latest/user_guides/stress_test/quick_start.html
- https://evalscope.readthedocs.io/zh-cn/latest/user_guides/stress_test/parameters.html
- https://evalscope.readthedocs.io/zh-cn/latest/user_guides/stress_test/examples.html
- https://evalscope.readthedocs.io/zh-cn/latest/user_guides/stress_test/multi_turn.html
- https://evalscope.readthedocs.io/zh-cn/latest/user_guides/stress_test/sla_auto_tune.html
- https://evalscope.readthedocs.io/zh-cn/latest/user_guides/stress_test/speed_benchmark.html
- https://evalscope.readthedocs.io/zh-cn/latest/user_guides/stress_test/custom.html
- https://evalscope.readthedocs.io/zh-cn/latest/user_guides/service.html

官方原始碼：

- https://raw.githubusercontent.com/modelscope/evalscope/main/evalscope/perf/arguments.py
- https://raw.githubusercontent.com/modelscope/evalscope/main/evalscope/perf/main.py

