# 多推理後端抽象層設計（vLLM / SGLang / llama.cpp / TensorRT-LLM）

> 目標:讓系統能在**同一個 group / 不同 group** 用不同的推理引擎(vLLM、SGLang、llama.cpp、
> TensorRT-LLM…),而不是寫死 vLLM。做法**不是重寫**,而是把現有那層已經很乾淨的 `Launcher`
> 抽象「補完」:新增一個 `engine` 維度、把 launcher 分派從「依 kind」改成「依 engine」、並引入
> **capabilities(能力旗標)** 讓只有 vLLM 才有的功能(sleep mode / runtime LoRA / KV transfer /
> 指標格式)對其他引擎自動退化。
>
> ⚠️ **設計原則:collapsed-first / 零行為變更。** `engine` 預設 `"vllm"`,所有現有 config 不動、
> 行為 byte-for-byte 不變,現有測試全綠 —— 與 HA 那套同樣的哲學。新引擎是**增量加上去的 launcher**,
> 不碰既有路徑。
>
> 本文是**抽象層藍圖**;各引擎的實際 CLI/啟動細節(SGLang 等)由各自的 launcher 實作,另行補上。

## 0. 為什麼這件事比想像中容易 —— 現有架構已經幫你做了一半

兩個既成事實讓「多後端」幾乎只剩「加 launcher」:

1. **Router 對後端完全是 OpenAI-compatible HTTP 反向代理**
   ([router.py](../apps/router-server/src/llm_router/router.py) proxy `/v1/chat/completions`、
   `/v1/completions`、`/v1/embeddings`、`/v1/rerank`…)。它**不知道也不在乎** `host:port` 後面跑的是
   哪個引擎 —— 只要那個 port 開的是 OpenAI 相容 server。SGLang、llama.cpp(`llama-server`)、
   TensorRT-LLM(`trtllm-serve`)都提供 OpenAI 相容端點。
   **結論:推理流量這條路徑零修改。**

2. **`Launcher` 已經是後端無關的抽象**([launchers.py](../apps/backend/app/llmops/launchers.py)):

   ```
   Launcher.keys(config)              # 此 launcher 在 config 裡定義了哪些 instance key
   Launcher.build_spec(config, key)   # config → LaunchSpec(command/env/probe_url/host/port…)
   ```

   `build_registry` 只是 `for launcher in launchers: for key in launcher.keys(...)`
   ([manager.py](../apps/backend/app/llmops/manager.py))。`LaunchSpec`
   ([instance.py](../apps/backend/app/llmops/instance.py))已經是引擎無關的「一份 command + env +
   probe_url」。`process.py` 拿到 spec 就 spawn,**完全不認識 vLLM**。

   **結論:加一個引擎 = 寫一個 launcher 產對應的 command,spawn / 健康探測 / 重啟 / fleet 視圖 / HA
   全部沿用。**

## 1. 現況的耦合點:vLLM 假設藏在哪

抽象層要拆掉的、目前「LLM 就等於 vLLM」的硬寫死:

| # | 位置 | 現況 | 問題 |
|---|---|---|---|
| C1 | [state.py](../apps/backend/app/llmops/state.py) `ModelKind`(`llm`/`embedding`)+ [manager.py](../apps/backend/app/llmops/manager.py) `_launchers: dict[ModelKind, Launcher]` | launcher 依 `ModelKind` 分派,**一個 kind 只能對一個 launcher** | vLLM 和 SGLang 都是 "llm",kind 不足以選 launcher |
| C2 | [manager.py](../apps/backend/app/llmops/manager.py) `self._launchers[ModelKind.LLM]`(建立 / 編輯 / LoRA 等多處) | 硬寫「LLM 就是那唯一一個 launcher」 | 多引擎共存時選錯 launcher |
| C3 | [schema.py](../packages/config-schema/schema.py) `EngineModelConfig` 只有 `kind`(chat/embed/rerank,**路由端點類型**,非引擎類型) | 沒有「用哪個引擎」的欄位 | config 無法表達 engine 選擇 |
| C4 | [launchers.py](../apps/backend/app/llmops/launchers.py) `build_vllm_cli_args` / `VllmLauncher` | vLLM 專屬 CLI 慣例(`serve <tag>`、`--no-` bool、kebab-case、`--lora-modules` 多值) | 各引擎 flag 名稱與慣例不同 |
| C5 | 功能耦合:sleep mode([launchers.py](../apps/backend/app/llmops/launchers.py) `--enable-sleep-mode`+`VLLM_SERVER_DEV_MODE`)、runtime LoRA([manager.py](../apps/backend/app/llmops/manager.py))、KV transfer、autoscaler 吃的 vLLM Prometheus 指標([metrics_poller](../apps/router-server/src/llm_router/metrics_poller.py)) | 假設每個 LLM 都支援 | 其他引擎沒有 → 功能會壞或誤判 |

C1–C4 是「接線」,直接;**C5 是真風險(功能對等)**,用 capabilities 解(見 §4)。

## 2. 資料模型變更:`engine` 維度

### 2.1 config schema(C3)

在 [schema.py](../packages/config-schema/schema.py) `EngineModelConfig` 新增一個欄位:

```python
class EngineModelConfig(BaseModel):
    model_config = ConfigDict(extra="allow", protected_namespaces=())
    model_tag: str
    # 用哪個推理引擎啟動這個 group。預設 vllm = 現有行為不變。
    # 注意:這跟 `kind`(chat/embed/rerank,路由端點類型)是兩個正交維度。
    engine: Literal["vllm", "sglang", "llamacpp", "trtllm"] = "vllm"
    kind: Literal["chat", "embed", "rerank"] = "chat"
    ...
```

- `extra="allow"` 已經讓**任意引擎 flag 透傳** → 各引擎專屬旗標**不需要動 schema**,直接寫在
  `model_config` 底下,由該引擎的 arg builder 解讀。
- `engine` 預設 `"vllm"` → 現有 config 不寫此欄位 = vLLM = 零變更。

> `engine` 屬於 group 層級(`model_config`)而非 instance 層級:同一 group 的所有 instance 用同一個引擎
> (它們是同一個模型的多副本)。要混引擎就開不同 group。

### 2.2 ModelKind vs engine 的關係

- `ModelKind`(`llm`/`embedding`)維持原意:**路由 + 探針的大分類**,決定 router 怎麼對待它。
- `engine` 是**「llm 這類用什麼程式去起」**的子維度。
- `embedding` 目前只有一個 launcher(router-server 的 embedding server),暫不引入 engine 選擇;
  未來若要 SGLang/其他做 embedding 再說。**本次抽象聚焦 `kind=llm` 底下的多引擎。**

### 2.3 通用參數正規化:typed 共用欄位,各 launcher 翻譯成自家旗標

**決定:常見概念用一組 engine 無關的 typed 欄位表達,由各 launcher 翻成自家 CLI 旗標。** 因為不同引擎
對同一概念的旗標名不同(下表),若讓使用者直接寫引擎旗標,dashboard UI 就無法統一渲染、換引擎就要重學。

| 概念(EngineModelConfig typed 欄位) | vLLM 旗標 | SGLang 旗標 | 備註 |
|---|---|---|---|
| `max_model_len` | `--max-model-len` | `--context-length` | 直接對應 |
| `gpu_memory_utilization` | `--gpu-memory-utilization` | `--mem-fraction-static` | **語意非逐字等價**(SGLang 是「靜態配給權重+KV pool 的顯存比例」);UI 要註明 |
| `tensor_parallel_size` | `--tensor-parallel-size` | `--tp-size`(alias `--tensor-parallel-size`) | 直接對應 |
| `dtype` | `--dtype` | `--dtype` | 同名 |
| `model_tag`(served name) | 位置參數 | `--model-path` + `--served-model-name <model_tag>` | SGLang 預設 served name = model_path,顯式帶 model_tag 讓 router forward_name 穩定 |

實作:每個 launcher 有自己的 **arg builder**(取代 `build_vllm_cli_args` 對所有引擎通用的假設)。builder 的
職責 = 「翻譯這張表的 typed 欄位 + 透傳 `extra="allow"` 的引擎原生旗標 + 套用該引擎的 bool 慣例」。
vLLM 的 builder 對這些 typed 欄位是 **identity 翻譯**(欄位名 kebab-case 後就是旗標),所以**現有行為不變**。

> bool 慣例各引擎不同:vLLM 是 `BooleanOptionalAction`(`--flag` / `--no-flag`);SGLang 是 `store_true`
> (只有 `--flag`,且很多旗標名本身就是負向如 `--disable-radix-cache`)。**不可**跨引擎沿用同一套 bool 邏輯。

## 3. Launcher 分派重構(C1 + C2)

### 3.1 Launcher Protocol 加上 `engine` 與 `capabilities`

```python
class Launcher(Protocol):
    kind: ModelKind          # 既有:路由/探針大分類
    engine: str              # 新增:"vllm" / "sglang" / "llamacpp" / "trtllm"
    capabilities: frozenset[str]   # 新增:見 §4

    def keys(self, config) -> list[str]: ...
    def build_spec(self, config, config_path, key) -> LaunchSpec: ...
```

### 3.2 註冊表從「依 kind」改成「依 (kind, engine)」

現在([manager.py](../apps/backend/app/llmops/manager.py)):

```python
self._launchers: dict[ModelKind, Launcher] = {l.kind: l for l in launchers}
...
launcher = self._launchers[ModelKind.LLM]          # C2:寫死
```

改成:

```python
# (kind, engine) → launcher。embedding 用哨兵 engine 名("default")保持單一。
self._launchers: dict[tuple[ModelKind, str], Launcher] = {
    (l.kind, l.engine): l for l in launchers
}

def _launcher_for(self, inst) -> Launcher:
    return self._launchers[(inst.kind, inst.engine)]
```

- `build_registry` 在列舉 key 時,launcher 自己知道自己負責哪些 group(`keys()` 只回傳
  `model_config.engine == self.engine` 的 group)→ **不同引擎的 launcher 自然分工,不重疊**。
- C2 那幾處 `self._launchers[ModelKind.LLM]` 改成 `self._launcher_for(inst)`。

### 3.3 `keys()` 依 engine 過濾

每個 LLM launcher 只認領 `engine` 等於自己的 group:

```python
class VllmLauncher:
    kind = ModelKind.LLM
    engine = "vllm"
    def keys(self, config):
        out = []
        for model_tag, eng in config.LLM_engines.items():
            if getattr(eng.settings, "engine", "vllm") != self.engine:
                continue            # 不是我的引擎,跳過
            for inst in eng.instances:
                out.append(f"{model_tag}::{inst.id}")
        return out
```

→ collapsed 情況(全 vLLM):`VllmLauncher` 認領全部、其他 launcher 認領 0 個 = 今天的行為。

### 3.4 ModelInstance 帶上 engine

[instance.py](../apps/backend/app/llmops/instance.py) `ModelInstance` 加 `engine: str = "vllm"`,
`observed_dict()` 一併輸出(讓 dashboard / fleet 視圖顯示引擎、HA 入庫帶上)。建 instance 處
([manager.py](../apps/backend/app/llmops/manager.py))從 `engine.settings.engine` 帶入。

## 4. Capabilities:功能對等的關鍵設計(C5)

**這是讓多引擎乾淨共存、避免一堆 `if engine == "vllm"` 散落各處的核心。** 把「只有某些引擎支援的功能」
宣告成 launcher 的能力集,呼叫端依**能力**而非**引擎名**做 gate。

### 4.1 能力清單(初版)

| capability | 意義 | 誰在乎 |
|---|---|---|
| `sleep` | 支援 `/sleep`+`/wake_up`(暖待命,VRAM 釋放但 process 存活) | autoscaler 的暖待命層、sleep API |
| `runtime_lora` | 支援執行期 LoRA 掛載/卸載端點 | LoRA 管理 UI / API([manager.py](../apps/backend/app/llmops/manager.py)) |
| `kv_transfer` | 支援跨實例 KV cache 共享 | KV transfer 設定 |
| `metrics_vllm` | 暴露 vLLM 格式 Prometheus 指標(waiting queue 等) | autoscaler 的擴縮訊號、[metrics_poller](../apps/router-server/src/llm_router/metrics_poller.py) |
| `lora_modules` | 啟動時可帶 `--lora-modules` 靜態 adapter | launcher arg 組裝 |

```python
class VllmLauncher:
    capabilities = frozenset({"sleep", "runtime_lora", "kv_transfer", "metrics_vllm", "lora_modules"})

class SglangLauncher:
    capabilities = frozenset({"metrics_sglang"})   # 例:不同指標格式;無 sleep/lora
```

### 4.2 呼叫端如何 gate

- **autoscaler**([autoscaler.py](../apps/backend/app/llmops/autoscaler.py)):暖待命層(ready→asleep→
  stopped)只在 group 的引擎有 `sleep` 能力時啟用;否則自動退化成 `ready ↔ stopped` 直接擴縮。
- **sleep API / LoRA API / KV transfer 設定**:目標 group 引擎缺對應 capability 時,API 回 409/友善錯誤,
  dashboard 對該 group 隱藏/灰掉這些動作。
- **metrics_poller / autoscaler 訊號**:依 `metrics_*` 能力選對應的指標解析器;無相容指標的引擎,
  擴縮退化成「只看 router 端可觀測的訊號(例如 in-flight / 佇列)」或維持固定副本。
- **LaunchSpec 帶 capabilities**:reconciler / autoscaler 不必回查 launcher,直接看 spec。

> 設計準則:**呼叫端永遠問「這個實例有沒有 X 能力」,絕不問「它是不是 vLLM」。** 新增引擎時只需宣告
> 它的能力集,所有 gate 自動正確。

## 5. 封裝模型:對稱式 per-engine image + engine 變成 node 能力

**決定:每個 engine 一顆 backend image,結構與現有 vLLM 對稱。** 因為 vLLM / SGLang 各自死釘特定
torch/CUDA/flashinfer,塞同一顆 image 極易版本打架;且 launcher 是**在 backend 容器內 spawn 子行程**
([process.py](../apps/backend/app/llmops/process.py)),所以「backend 能起哪些 engine」= 它容器裡裝了什麼。

```
engine.Dockerfile          FROM vllm/vllm-openai:latest   + backend code  →  能起 vllm
engine-sglang.Dockerfile   FROM lmsysorg/sglang:latest    + backend code  →  能起 sglang
```

兩顆 image 都含**同一份 backend FastAPI 程式**,只是 base image(= 可用的 engine CLI)不同。單機 collapsed
仍是今天的樣子(跑 vLLM image);要混 engine 就**多跑一個用 sglang image 的 backend 副本**。

這把 `engine` 從「group 屬性」自然提升成 **node 能力**:

- node-agent 心跳時宣告自己能跑哪些 engine(由 image 決定,例:`engines=["sglang"]`)。
- scheduler placement 時,只把某 engine 的 group 排到**宣告支援該 engine 的 node**。
- 這牽涉 [node_agent.py](../apps/backend/app/llmops/node_agent.py) / [scheduler.py](../apps/backend/app/llmops/scheduler.py) /
  `nodes` 表,屬於 [ha-per-node-actuation-design](ha-per-node-actuation-design_zh-CN.md) 那塊;**本階段先不做多節點排程**,
  單機驗證「sglang image 能起 sglang 模型 + router 能路由」即可。

### 5.1 各引擎 launcher 概要

| 引擎 | 啟動(容器內) | base image | OpenAI 相容 | 探針 | 難度 |
|---|---|---|---|---|---|
| **vLLM**(現有) | `vllm serve <tag> --flags` | `vllm/vllm-openai` | ✅ | `/health` | — |
| **SGLang** | `python3 -m sglang.launch_server --model-path <tag> …` | `lmsysorg/sglang` | ✅ | `/health` | 低(第一個) |
| **llama.cpp** | `llama-server -m <gguf> …` | `ghcr.io/ggml-org/llama.cpp` | ✅ | `/health` | 低 |
| **TensorRT-LLM** | 先離線 `trtllm-build` 再 `trtllm-serve` | `nvcr.io/.../tritonserver` | ✅ | — | 高(暫緩) |

每個 launcher 就是一個 class:`kind=LLM`、`engine=…`、`capabilities=…`、`keys()`(依 engine 過濾)、
`build_spec()`(產該引擎的 command + 設對的 `probe_url` + env)+ **自己的 arg builder**。

### 5.2 SGLang 具體規格(依 [sglang_related_info.md](sglang_related_info.md),v0.5.14)

- **command(容器內)**:`python3 -m sglang.launch_server`(對齊官方容器 entrypoint;host 端 `sglang serve` 等價)。
- **arg builder**(SGLang 專屬,見 §2.3 翻譯表):
  - `--model-path <model_tag>`、`--served-model-name <model_tag>`、`--host`、`--port`
  - typed 翻譯:`max_model_len→--context-length`、`gpu_memory_utilization→--mem-fraction-static`、
    `tensor_parallel_size→--tp-size`、`dtype→--dtype`
  - bool = `store_true`(只 `--flag`,**無** `--no-`);其餘 `extra="allow"` 旗標 kebab-case 後透傳
  - 並行旗標用 canonical `--tp-size` / `--dp-size`(不要假設 `--tp`/`--dp` 存在)
- **probe_url**:`/health`(SGLang 預設 `/health` 會做 1-token 生成檢查,Starting 時回 503 → 正好當 readiness)。
  例外:若部署設了 `SGLANG_ENABLE_HEALTH_ENDPOINT_GENERATION=false`,readiness 改用 `/health_generate`。
- **GPU**:沿用 `CUDA_VISIBLE_DEVICES`(同 vLLM 路徑);同機多實例切片再配 `--base-gpu-id`/`--gpu-id-step`。
- **capabilities** = `{runtime_lora, lora_modules}`(已實作);`metrics_sglang` 待 parser 完成才宣告:
  - `runtime_lora` ✅(已 live 驗證):`POST /load_lora_adapter` / `/unload_lora_adapter`(啟動需
    `--enable-lora`,建議帶 `--max-lora-rank` / `--lora-target-modules`);`_post_lora` 依引擎選端點路徑
  - `lora_modules` ✅:靜態 `--lora-paths NAME=PATH`(非 vLLM 的 `--lora-modules` JSON 形式)
  - `sleep` ❌:SGLang 無 vLLM 式 `/sleep`/`/wake_up`(只有 `--sleep-on-idle`,降 CPU 非釋放 VRAM)→
    autoscaler 暖待命層對 SGLang group 自動退化成 `stop`
  - `metrics_sglang` ✅(已 live 驗證):`--enable-metrics` + router 依引擎解析 `sglang:*` → autoscaler 可擴縮
  - `kv_transfer` ❌
- **容器需求**:`--ipc=host` 或大 `--shm-size`(SGLang 對 shared memory 敏感);掛 HF cache。
- **router 零修改**:chat/completions、completions、streaming、final usage chunk(`choices=[]` + usage)、
  `/v1/models`、`/tokenize`、`/detokenize` 全有 → 計費(靠 final usage chunk)可接。

### 5.3 監控(Prometheus / Grafana):指標進得去,現有 vLLM dashboard 不會亮

- **Prometheus 抓取 ✅**(需兩個前提):SGLang 啟動要帶 **`--enable-metrics`** 才有 `/metrics`(標準
  Prometheus 格式);且要把 SGLang 實例**寫進 scrape targets**(現在 backend 的 file_sd 只寫 vLLM 實例,
  見 [metrics_poller](../apps/router-server/src/llm_router/metrics_poller.py) `write_prometheus_targets`)。
- **現有 vLLM Grafana dashboard ❌ 不能直接用**:panel query 全是 `vllm:*`,SGLang 吐的是 `sglang:*`
  (名稱不同),所以 SGLang 實例的數據進得了 Prometheus、但現有 dashboard 的圖是空的。概念 1:1 對得上
  (queue=`sglang:num_queue_reqs`、running=`sglang:num_running_reqs`、throughput=`sglang:gen_throughput`、
  TTFT=`sglang:time_to_first_token_seconds`、cache=`sglang:cache_hit_rate`),所以解法是**另做一份並列的
  SGLang dashboard**(或加 dashboard variable 切引擎)。
- 這跟 autoscaler 訊號是**同一塊**(都吃 `sglang:*`),所以一起做(見步驟 6,已完成)。

> **已完成(步驟 6)**:autoscaler 訊號靠 router 的指標 client 依引擎解析 `sglang:*` → 正規化成同一
> `{waiting,running,kv}`,所以 load-monitor / autoscaler **零改**就能擴縮 SGLang。並附一份 SGLang Grafana
> dashboard。下面這段保留為「當初的權衡記錄」:
>
> ~~autoscaling + 監控 首版不含~~(後來在步驟 6 補上):SGLang 指標自成一格(`sglang:num_queue_reqs` /
> `num_running_reqs` …,或 `/v1/loads` JSON)。`metrics_sglang` 解析器 + scrape target(`--enable-metrics`)
> + SGLang Grafana dashboard
> (屆時才宣告 `metrics_sglang` capability 真正生效)。

## 6. 落地步驟(每步可獨立 commit、跑全測確保零行為變更)

1. ✅ **抽象層骨架(不新增引擎)** — 已完成、已驗證
   - schema 加 `engine`(預設 vllm);`Launcher` Protocol 加 `engine`/`capabilities`;
     `VllmLauncher`/`EmbeddingLauncher` 標上 `engine`/`capabilities`。
   - `_launchers` 改 `(kind, engine)` keyed;C2 各處改 `_launcher_for(inst)`;`keys()` 依 engine 過濾。
   - `ModelInstance`/`LaunchSpec`/`ModelView` 帶 `engine`,fleet 視圖 + HA 入庫一併輸出。
   - **驗收:全 vLLM,backend 386 / router 119 / schema 5 全綠;docker live 跑 vLLM 模型 → READY → 推理 OK。**
2. ✅ **capability gating** — 已完成
   - LoRA 依 `CAP_RUNTIME_LORA` gate;create/update 對未註冊 launcher 的 engine 回乾淨錯誤;
     autoscaler sleep 層 / sleep API 既有的 `sleep_enabled` 判斷 → 非 vLLM 自動退化成 `stop`。
   - 測試:一個無能力的假 launcher,確認 gate 正確拒絕 + dispatch 正確。
3. ✅ **SGLang image** — 已完成:[engine-sglang.Dockerfile](../deploy/engine-sglang.Dockerfile)
   (`FROM lmsysorg/sglang` + 同一份 backend code;砍掉 vllm,sglang/torch 來自 base 不動)。
   驗證:image 不含 vllm、backend FastAPI 在其中正常 import/boot。
4. ✅ **`SglangLauncher`**(§5.2)— 已完成、已 live 驗證:arg builder(typed 翻譯 + store_true bool +
   `--served-model-name`)+ `/health` probe。docker 實測(**store 用 Postgres**,證明 store 與引擎解耦):
   reconciler 經 SglangLauncher spawn `python -m sglang.launch_server` → READY → router proxy 推理
   (非串流 + 串流 final usage chunk)OK。
5. ✅ **capability 回歸** — 已驗證:SGLang group 的 `/sleep` 被擋(409,無 sleep 能力)、API 顯示
   `engine=sglang`、autoscaler 對它退化(固定副本)。
5b. ✅ **SGLang runtime LoRA** — 已完成、已 live 驗證:`capabilities={runtime_lora, lora_modules}`;
   arg builder 在有 LoRA 設定時帶 `--enable-lora` + 靜態 `--lora-paths NAME=PATH`;`_post_lora` 依引擎
   選端點(SGLang `/load_lora_adapter`,無 `/v1`)。docker 實測:經我們的 API 熱掛載真實 adapter
   (qwen3-test-lora)→ sglang `/v1/models` 出現該 adapter → 對 adapter 推理 OK → 卸載後消失。
   全測 398 綠。
6. ✅ **SGLang 監控 + autoscaling** — 已完成、已 live 驗證(都吃 `sglang:*`):
   - launcher 一律帶 `--enable-metrics`(vLLM 預設就有,SGLang 要旗標)。
   - router 的指標 client 改成**依引擎解析**([vllm_metrics_client.py](../apps/router-server/src/llm_router/vllm_metrics_client.py)
     `METRIC_NAMES_BY_ENGINE`):把 `sglang:num_queue_reqs`/`num_running_reqs`/`token_usage` 正規化成
     **同一個 `{waiting,running,kv}` 形狀**,所以 load-monitor / autoscaler / routing **零改**就能擴縮 SGLang。
     poller 依 group 的 engine 選 parser。宣告 `metrics_sglang` capability。
   - Prometheus file_sd target 加上 `engine` label(供 dashboard 篩選);SGLang 實例本來就會被寫入(engine 無關)。
   - 並列的 **SGLang Grafana dashboard**([deploy/grafana/dashboards/sglang/overview.json](../deploy/grafana/dashboards/sglang/overview.json),
     8 panel,query 用 `sglang:*`)。
   - **Live 驗證**:24 並發請求時 router `/metrics` 顯示 `running=24`、`token_usage` 0.09→0.25(由 `sglang:*`
     正規化而來)→ autoscaler 訊號成立。
7.(可選)**llama.cpp** / (可選/暫緩)**TensorRT-LLM**。

## 7. 風險與不做什麼

- **不重寫 router**:它已是引擎無關的 OpenAI proxy。唯一要測的是各引擎對 `/v1/models`、`/tokenize`、
  `/detokenize`、streaming `usage` 的支援差異(router 已有 fallback,逐一驗證即可)。
- **不在 embedding 引入 engine 維度**(本次):聚焦 `kind=llm`。
- **不假設功能對等**:任何 vLLM 專屬功能一律走 capabilities,缺能力就退化,**絕不**讓非 vLLM group 因為
  缺 sleep/lora 端點而當機或被 autoscaler 誤判。
- **混引擎只在 group 之間**:同 group 同引擎(同模型多副本),避免 instance 級別混搭的複雜度。

## 8. 相關檔案索引

- [launchers.py](../apps/backend/app/llmops/launchers.py) — Launcher Protocol + Vllm/Embedding 實作(主戰場)
- [manager.py](../apps/backend/app/llmops/manager.py) — `_launchers` 分派、建立/編輯/LoRA(C2)
- [instance.py](../apps/backend/app/llmops/instance.py) — `LaunchSpec` / `ModelInstance`(加 engine/capabilities)
- [state.py](../apps/backend/app/llmops/state.py) — `ModelKind`
- [schema.py](../packages/config-schema/schema.py) — `EngineModelConfig`(加 engine 欄位)
- [autoscaler.py](../apps/backend/app/llmops/autoscaler.py) — sleep 層 capability gate
- [metrics_poller.py](../apps/router-server/src/llm_router/metrics_poller.py) — 指標解析依引擎
- [router.py](../apps/router-server/src/llm_router/router.py) — OpenAI proxy(預期零修改)
- [process.py](../apps/backend/app/llmops/process.py) — spawn 子行程(决定 engine 必須在 backend 容器內)
- [engine.Dockerfile](../deploy/engine.Dockerfile) — vLLM image;SGLang 對稱新增 `engine-sglang.Dockerfile`
- [sglang_related_info.md](sglang_related_info.md) — SGLang v0.5.14 啟動/旗標/端點/capability 調研(SglangLauncher 依據)
