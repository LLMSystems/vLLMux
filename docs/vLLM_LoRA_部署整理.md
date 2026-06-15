# vLLM LoRA 部署整理

這份文件整理的是 **vLLM 如何部署帶 LoRA 的推理服務**，重點是：

- 怎麼啟動一個支援 LoRA 的 `vllm serve`
- 怎麼在啟動時直接掛載 LoRA
- 怎麼在 server 已經啟動後動態載入 / 卸載 LoRA
- 怎麼用 resolver plugin 做「按需載入」LoRA
- 常用 LoRA 相關參數該怎麼配

整理時間：`2026-06-15`  
基準版本：vLLM 官方 `latest` 文件（注意 `latest` 目前是 developer preview）

參考來源：

- [vLLM LoRA Adapters](https://docs.vllm.ai/en/latest/features/lora/)
- [vLLM LoRA Resolver Plugins](https://docs.vllm.ai/en/latest/design/lora_resolver_plugins/)
- [vLLM serve CLI Reference](https://docs.vllm.ai/en/latest/cli/serve/)
- [vLLM Security](https://docs.vllm.ai/en/latest/usage/security/)

---

## 1. 先講結論

vLLM 目前部署 LoRA，大致有 3 種方式：

1. **靜態掛載**
   在 `vllm serve` 啟動時用 `--enable-lora` 搭配 `--lora-modules` 直接把 LoRA 掛進去。
2. **動態載入 / 卸載**
   server 啟動後，透過 `/v1/load_lora_adapter` 與 `/v1/unload_lora_adapter` API 在 runtime 增減 LoRA。
3. **resolver plugin 按需載入**
   server 本身只啟 base model，但收到某個 LoRA 名稱時，再從本地目錄、HF Hub 或自訂來源自動解析並載入。

如果你的需求是：

- **固定幾個 LoRA 長期服務**：用靜態掛載最單純
- **LoRA 會常常更新 / 上下架**：用動態載入
- **LoRA 數量多，不想一次全部 preload**：用 resolver plugin

---

## 2. 前提條件

### 2.1 Base model 必須支援 LoRA

官方文件明確寫到：LoRA adapter 只能用在 **實作 `SupportsLoRA` 的 vLLM model** 上。

也就是說，不是所有模型都能直接開 LoRA。部署前要先確認：

- 這個 base model 在 vLLM 裡支援推理
- 這個 model class 有 LoRA 支援

### 2.2 核心開關是 `--enable-lora`

只要要跑 LoRA，幾乎都要先開：

```bash
--enable-lora
```

沒有這個，LoRA 相關能力不會啟用。

---

## 3. 最常用做法：啟動時靜態掛載 LoRA

這是最穩、最好理解、最適合正式部署的方式。

### 3.1 最小啟動範例

```bash
vllm serve meta-llama/Llama-3.2-3B-Instruct \
  --enable-lora \
  --lora-modules sql-lora=jeeejeee/llama32-3b-text2sql-spider
```

意思是：

- base model 是 `meta-llama/Llama-3.2-3B-Instruct`
- 額外掛一個名稱叫 `sql-lora` 的 LoRA
- 其來源是 `jeeejeee/llama32-3b-text2sql-spider`

### 3.2 `--lora-modules` 的兩種格式

官方目前保留 **舊格式** 與 **新格式**。

#### 舊格式：`name=path`

```bash
--lora-modules sql-lora=jeeejeee/llama32-3b-text2sql-spider
```

這種最短，但只有：

- `name`
- `path`

#### 新格式：JSON

```bash
--lora-modules '{"name": "sql-lora", "path": "jeeejeee/llama32-3b-text2sql-spider", "base_model_name": "meta-llama/Llama-3.2-3B-Instruct"}'
```

這是官方現在比較推薦的格式，因為可以把 `base_model_name` 一起帶進去。

這樣做的好處：

- `/v1/models` 會顯示更完整的 model lineage
- LoRA model card 會有 `parent`
- `root` 會指到 LoRA artifact 位置

如果你要做平台化管理，我會建議優先用 JSON 格式。

### 3.3 一次掛多個 LoRA

可以同時傳多個 module：

```bash
vllm serve meta-llama/Llama-3.2-3B-Instruct \
  --enable-lora \
  --max-loras 2 \
  --lora-modules \
    sql-lora=jeeejeee/llama32-3b-text2sql-spider \
    finance-lora=/models/lora/finance
```

實務上建議你同時確認：

- `--max-loras`
- `--max-lora-rank`
- `--max-cpu-loras`

不然常見狀況是 LoRA 掛得進去，但 batch / rank / CPU cache 配置不夠。

### 3.4 啟動後怎麼確認 LoRA 有沒有被服務出來

可以查：

```bash
curl http://localhost:8000/v1/models
```

官方文件顯示，LoRA 會以獨立 model 項目出現在 `/v1/models` 裡；如果你用的是新 JSON 格式，還會看到：

- `parent`：對應 base model
- `root`：LoRA 來源

### 3.5 推理時怎麼指定使用哪個 LoRA

依官方文件中的 `/v1/models` 呈現方式，實務上你會把 **LoRA 的 served name** 當成請求裡的 `model`。

例如你掛的是 `sql-lora`，那請求就可以寫：

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="dummy",
)

resp = client.chat.completions.create(
    model="sql-lora",
    messages=[
        {"role": "user", "content": "Write a SQL query to count users by country."}
    ],
)

print(resp.choices[0].message.content)
```

如果你要先確認目前 server 可用哪些 model，先打：

```bash
curl http://localhost:8000/v1/models
```

最穩。

---

## 4. Runtime 動態載入 / 卸載 LoRA

如果你的 LoRA 不是固定一批，而是會：

- 持續新增
- 持續替換
- AB test
- 租戶隔離
- 強化學習流程中不斷更新 adapter

那可以用 vLLM 的 runtime API。

### 4.1 開啟條件

官方文件明確要求：**必須同時滿足兩件事**

1. `vllm serve` 有開 `--enable-lora`
2. 環境變數有設：

```bash
VLLM_ALLOW_RUNTIME_LORA_UPDATING=True
```

例如：

```bash
export VLLM_ALLOW_RUNTIME_LORA_UPDATING=True

vllm serve meta-llama/Llama-3.2-3B-Instruct \
  --enable-lora
```

### 4.2 載入 LoRA

官方 API endpoint：

```text
POST /v1/load_lora_adapter
```

最常見 payload 會包含：

- `lora_name`
- `lora_path`

範例：

```bash
curl -X POST http://localhost:8000/v1/load_lora_adapter \
  -H "Content-Type: application/json" \
  -d '{
    "lora_name": "sql_adapter",
    "lora_path": "/models/lora/sql_adapter"
  }'
```

成功時，官方文件示例會回類似：

```text
Success: LoRA adapter 'sql_adapter' added successfully
```

### 4.3 卸載 LoRA

官方 API endpoint：

```text
POST /v1/unload_lora_adapter
```

範例：

```bash
curl -X POST http://localhost:8000/v1/unload_lora_adapter \
  -H "Content-Type: application/json" \
  -d '{
    "lora_name": "sql_adapter"
  }'
```

成功時，官方文件示例會回類似：

```text
Success: LoRA adapter 'sql_adapter' removed successfully
```

### 4.4 同名熱更新：`load_inplace`

官方文件特別提到一個很實用的能力：

```json
"load_inplace": true
```

這適合：

- RL / online learning 流程
- 同名 adapter 權重反覆更新
- 不想改 served name，但想替換內容

範例：

```bash
curl -X POST http://localhost:8000/v1/load_lora_adapter \
  -H "Content-Type: application/json" \
  -d '{
    "lora_name": "policy_adapter",
    "lora_path": "/models/lora/policy_adapter_v2",
    "load_inplace": true
  }'
```

語意上就是：

- 如果 `policy_adapter` 還沒載入，就載入
- 如果已經有同名 adapter，就原地替換成新的權重

### 4.5 安全性警告

這點非常重要。官方 security 文件明確說：

- runtime LoRA loading **不是安全操作**
- 不應暴露給不受信任的終端使用者
- `/v1/load_lora_adapter` 和 `/v1/unload_lora_adapter` 應只允許受信任管理者存取

所以如果你要在正式環境開這個能力，建議至少做到：

- 用 reverse proxy 擋住這兩個 endpoint
- 只允許內網或管理平面存取
- 不要直接暴露到 public internet

如果你的場景只是固定幾個 LoRA，通常還是 **靜態掛載** 更安全。

---

## 5. 大量 LoRA 的做法：resolver plugin 按需載入

如果你有很多 LoRA，不想在 server 啟動時一次全部載入，可以用 LoRA resolver plugin。

這個模式的思路是：

- server 只先啟 base model
- 收到某個 LoRA 請求時
- 再去指定來源找到該 adapter
- 找到後動態載入

官方文件目前有講到：

- filesystem resolver
- Hugging Face Hub resolver
- 自訂 resolver

### 5.1 本地 filesystem resolver

官方文件給的環境變數核心是：

```bash
export VLLM_ALLOW_RUNTIME_LORA_UPDATING=true
export VLLM_PLUGINS=lora_filesystem_resolver
export VLLM_LORA_RESOLVER_CACHE_DIR=/path/to/lora/adapters
```

然後啟動：

```bash
vllm serve your-base-model \
  --enable-lora
```

### 5.2 目錄結構

官方文件的概念是：每個 adapter 一個資料夾，例如：

```text
/path/to/lora/adapters/
  sql_adapter/
    adapter_config.json
    adapter_model.safetensors
```

`adapter_config.json` 至少會是典型 PEFT LoRA 設定，例如：

```json
{
  "peft_type": "LORA",
  "base_model_name_or_path": "your-base-model-name",
  "r": 16,
  "lora_alpha": 32,
  "target_modules": ["q_proj", "v_proj"]
}
```

### 5.3 請求時的行為

這種模式下，如果某個 LoRA 還沒在記憶體裡：

1. vLLM 看到請求要用某個 LoRA 名稱
2. resolver 去 `VLLM_LORA_RESOLVER_CACHE_DIR/<lora_name>/` 找
3. 找到後動態載入
4. 後續請求就能直接用

這很適合：

- 一台 server 要服務很多租戶 LoRA
- LoRA 數量很多，但熱 LoRA 只有少數
- 想降低啟動時間與 preload 記憶體佔用

### 5.4 多個 resolver 並用

官方文件也提到可以同時開多個 resolver：

```bash
export VLLM_PLUGINS=lora_filesystem_resolver,lora_s3_resolver
```

也就是說你可以做成：

- 先找本地 cache
- 找不到再找遠端來源

### 5.5 自訂 resolver

如果你們 LoRA 是放在：

- 自家 model registry
- S3 / OSS
- 資料庫
- 內部 artifact 平台

可以自己實作 resolver，核心是對接：

- `LoRAResolver`
- `LoRAResolverRegistry`
- 回傳 `LoRARequest`

這部分比較偏平台開發，但官方設計上是支援的。

---

## 6. `vllm serve` 啟 LoRA 時最常用的參數

以下是部署最常會調的 LoRA 相關參數。

### 6.1 `--enable-lora`

```bash
--enable-lora
```

是否啟用 LoRA 支援。沒有這個，其他 LoRA 設定都不會生效。

### 6.2 `--max-loras`

```bash
--max-loras 1
```

官方定義：**單一 batch 中可同時處理的 LoRA 數量上限**。  
預設值：`1`

如果你的服務會混跑不同 LoRA 請求，這個值可能要提高。

### 6.3 `--max-lora-rank`

```bash
--max-lora-rank 64
```

官方可選值：

- `1`
- `8`
- `16`
- `32`
- `64`
- `128`
- `256`
- `320`
- `512`

預設值：`16`

官方文件特別提醒：

- 這個值應該設成「你會服務的 LoRA 中最大 rank」
- 設太高會浪費記憶體，也可能影響效能

例如你所有 LoRA rank 是 `16 / 32 / 64`，那就設 `64`，不要習慣性拉到 `256`。

### 6.4 `--lora-dtype`

```bash
--lora-dtype auto
```

LoRA 權重的 dtype。`auto` 時會跟 base model dtype 對齊。  
預設值：`auto`

### 6.5 `--max-cpu-loras`

```bash
--max-cpu-loras 8
```

可保留在 CPU memory 的 LoRA 數量上限。  
官方要求：**必須大於等於 `max_loras`**

這在動態載入 / resolver 場景很重要，因為不是所有 LoRA 都會長駐 GPU。

### 6.6 `--fully-sharded-loras`

```bash
--fully-sharded-loras
```

官方說明是：

- 預設情況下，只有一半的 LoRA 計算會跟 tensor parallel 一起 shard
- 開這個後，改成 fully sharded
- 在高 sequence length / 高 rank / 高 TP size 場景下，可能更快

如果你在大模型、多卡 TP、長上下文情境跑 LoRA，這個值得 benchmark。

### 6.7 `--lora-target-modules`

```bash
--lora-target-modules o_proj qkv_proj down_proj
```

這個可以限制只對特定 module suffix 套 LoRA。  
官方文件舉例有：

- `o_proj`
- `qkv_proj`

這個參數適合：

- 你很清楚 adapter 的 target module
- 想縮小 LoRA 套用範圍
- 想做效能 / 記憶體調優

### 6.8 `--specialize-active-lora`

```bash
--specialize-active-lora
```

這是比較進階的效能選項。  
官方說明：會依照 active LoRA 數量建立不同的 CUDA graph，可能改善 LoRA 使用模式變化大的情況，但代價是：

- 啟動時間更久
- 記憶體用量更高

如果你只是一般單 LoRA 服務，通常不用先碰它。

---

## 7. 多模態與 MoE 相關 LoRA

這一段不是所有人都會用到，但如果你跑的是多模態或 MoE，很重要。

### 7.1 多模態 tower / connector LoRA

官方 `serve` CLI 有：

```bash
--enable-tower-connector-lora
```

用途是開啟：

- vision tower
- connector

上的 LoRA 支援。

官方目前標註這是 **experimental feature**，而且只支援部分多模態模型，例如某些 Qwen VL 系列。

### 7.2 `--default-mm-loras`

官方支援對多模態 model 設預設 LoRA：

```bash
vllm serve ibm-granite/granite-speech-3.3-2b \
  --max-model-len 2048 \
  --enable-lora \
  --default-mm-loras '{"audio":"ibm-granite/granite-speech-3.3-2b"}' \
  --max-lora-rank 64
```

這個參數的語意是：

- 當某種 modality 出現時
- 自動套用對應 LoRA

但官方也明確提醒：

- 目前 **一個 prompt 只支援一個 LoRA adapter**
- 如果同一請求同時帶多個 modality，而每個 modality 都對應不同 LoRA，`default_mm_loras` 不會按你直覺那樣全部一起套

另外，官方文件也寫到：

- default multimodal LoRA 目前只適用於 `.generate` 與 chat completions

### 7.3 MoE LoRA 混合格式

如果你服務的是 MoE 模型，而且 adapter 格式可能混有：

- 2D format
- 3D format

那官方提供：

```bash
--enable-mixed-moe-lora-format
```

它的作用是讓同一個 deployment 可以同時服務 2D / 3D MoE LoRA adapter。

但官方也特別警告：

- 你必須正確知道 adapter 的 layout
- 若 `is_3d_lora_weight` 宣告錯誤，可能不會當場報錯
- 但輸出會變成錯的

所以這個選項只建議在你非常清楚權重格式時使用。

---

## 8. 可直接測的範例模型與 LoRA 模組

這裡我只放 **我能從官方 vLLM 文件或官方 Hugging Face 模型卡直接對上的組合**，這樣相容性把握最高。

### 8.1 最適合文字 smoke test

**Base model**

- `meta-llama/Llama-3.2-3B-Instruct`

**LoRA**

- `jeeejeee/llama32-3b-text2sql-spider`

**為什麼推薦**

- 這組就是 vLLM 官方 LoRA 文件主範例
- `adapter_config.json` 可對到 `base_model_name_or_path=meta-llama/Llama-3.2-3B-Instruct`
- adapter 的 `r=8`，所以 `--max-lora-rank 8` 就能先跑最小驗證

**注意**

- `meta-llama/Llama-3.2-3B-Instruct` 在 Hugging Face 通常需要先取得 Meta/Llama 存取權
- 比較適合拿來驗證 text-only LoRA 流程，不是最自由下載的組合

**啟動範例**

```bash
vllm serve meta-llama/Llama-3.2-3B-Instruct \
  --enable-lora \
  --max-lora-rank 8 \
  --lora-modules \
    '{"name":"sql-lora","path":"jeeejeee/llama32-3b-text2sql-spider","base_model_name":"meta-llama/Llama-3.2-3B-Instruct"}'
```

**測試請求**

```bash
curl http://localhost:8000/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "sql-lora",
    "prompt": "Write a SQL query to count users by country.",
    "max_tokens": 128,
    "temperature": 0
  }'
```

### 8.2 最適合測多模態 LoRA server

**Base model**

- `ibm-granite/granite-speech-3.3-2b`

**LoRA**

- `speech=ibm-granite/granite-speech-3.3-2b`

這組看起來有點特別，但它是 **IBM 官方模型卡直接提供的 vLLM 用法**。  
也就是說，這個模型本身就有官方建議的 LoRA 啟動方式，不需要你另外找一個外部 adapter。

**為什麼推薦**

- Hugging Face 模型卡直接有 `Usage with vLLM`
- 相對容易取得，模型卡顯示授權是 `Apache-2.0`
- 很適合拿來驗證：
  - `--enable-lora`
  - `--lora-modules`
  - 音訊輸入
  - chat completions / multimodal 路徑

**注意**

- 這不是純文字 smoke test，而是音訊/語音場景
- 需要準備 wav 檔，或照官方範例走 `AudioAsset("mary_had_lamb")`

**啟動範例**

```bash
vllm serve ibm-granite/granite-speech-3.3-2b \
  --max-model-len 2048 \
  --enable-lora \
  --lora-modules speech=ibm-granite/granite-speech-3.3-2b \
  --max-lora-rank 64
```

**適合驗證的點**

- server 是否能正常把 `speech` 註冊成 LoRA model
- `/v1/models` 是否出現 `speech`
- audio request 是否能正確走到對應 LoRA 路徑

### 8.3 最適合測 `--enable-mixed-moe-lora-format`

**Base model**

- `Qwen/Qwen3.6-35B-A3B`

**LoRA**

- `jeeejeee/qwen36-35ba3b-2d-weights-poken-lora`
- `jeeejeee/qwen36-35ba3b-moe-all-linear-poken-lora`

這組是 vLLM 官方文件拿來展示 **2D / 3D MoE LoRA 混掛** 的範例。

**啟動範例**

```bash
vllm serve Qwen/Qwen3.6-35B-A3B \
  --enable-lora \
  --enable-mixed-moe-lora-format \
  --tensor-parallel-size 4 \
  --enable-expert-parallel \
  --lora-modules \
    '{"name":"lora-2d","path":"jeeejeee/qwen36-35ba3b-2d-weights-poken-lora","is_3d_lora_weight":false}' \
    '{"name":"lora-3d","path":"jeeejeee/qwen36-35ba3b-moe-all-linear-poken-lora","is_3d_lora_weight":true}'
```

**注意**

- 這組是功能驗證型，不是輕量測試型
- `Qwen3.6-35B-A3B` 是 35B total / 3B activated 的 MoE 模型，硬體門檻高很多
- 主要目的是驗證：
  - `--enable-mixed-moe-lora-format`
  - `is_3d_lora_weight`
  - MoE LoRA 的部署流程

### 8.4 我會怎麼選測試順序

如果你只是想先確認 vLLM LoRA server 有沒有成功跑起來，我建議順序是：

1. `meta-llama/Llama-3.2-3B-Instruct` + `jeeejeee/llama32-3b-text2sql-spider`
2. `ibm-granite/granite-speech-3.3-2b` + `speech=ibm-granite/granite-speech-3.3-2b`
3. `Qwen/Qwen3.6-35B-A3B` + 2D/3D MoE LoRA 兩組

原因很簡單：

- 第 1 組最適合驗證 text-only LoRA 主流程
- 第 2 組最適合驗證多模態 / audio LoRA 路徑
- 第 3 組最適合驗證進階 MoE 功能，但最吃資源

### 8.5 如果你只想要一組最穩的 demo

如果你目前只想要一組「最像教科書範例」的 LoRA server 測試組合，就先用這組：

```bash
vllm serve meta-llama/Llama-3.2-3B-Instruct \
  --enable-lora \
  --max-lora-rank 8 \
  --lora-modules sql-lora=jeeejeee/llama32-3b-text2sql-spider
```

因為它同時滿足：

- vLLM 官方文件直接示範
- LoRA adapter 的 base model 可以對得上
- 指令最短
- request 也最簡單

---

## 9. 實務部署範本

### 9.1 單 base model + 固定 LoRA

最推薦起手式：

```bash
vllm serve meta-llama/Llama-3.2-3B-Instruct \
  --host 0.0.0.0 \
  --port 8000 \
  --enable-lora \
  --max-loras 1 \
  --max-lora-rank 64 \
  --lora-modules \
    '{"name":"sql-lora","path":"/models/lora/sql","base_model_name":"meta-llama/Llama-3.2-3B-Instruct"}'
```

適用：

- 服務固定 domain adapter
- production 想簡單穩定

### 9.2 單 base model + LoRA 常常更新

```bash
export VLLM_ALLOW_RUNTIME_LORA_UPDATING=True

vllm serve meta-llama/Llama-3.2-3B-Instruct \
  --host 0.0.0.0 \
  --port 8000 \
  --enable-lora \
  --max-loras 1 \
  --max-lora-rank 64 \
  --max-cpu-loras 16
```

然後再用：

- `/v1/load_lora_adapter`
- `/v1/unload_lora_adapter`

做營運期管理。

### 9.3 大量租戶 LoRA

```bash
export VLLM_ALLOW_RUNTIME_LORA_UPDATING=true
export VLLM_PLUGINS=lora_filesystem_resolver
export VLLM_LORA_RESOLVER_CACHE_DIR=/srv/lora_registry

vllm serve meta-llama/Llama-3.2-3B-Instruct \
  --host 0.0.0.0 \
  --port 8000 \
  --enable-lora \
  --max-loras 1 \
  --max-lora-rank 64 \
  --max-cpu-loras 64
```

適用：

- 多租戶
- LoRA 數量很多
- 不想全部 preload

---

## 10. 部署建議

### 10.1 如果你是第一次上線 LoRA

建議順序：

1. 先用靜態掛載確認 base model + adapter 能正常跑
2. 再看是否真的需要 runtime loading
3. LoRA 數量變多後，再考慮 resolver plugin

這樣 debug 成本最低。

### 10.2 `max_lora_rank` 不要亂設高

這是官方文件有特別提醒的點。  
設過大會浪費資源，最好跟實際 adapter rank 對齊。

### 10.3 動態載入能力不要對外開放

這不是一般 public API。  
如果你開了：

- `/v1/load_lora_adapter`
- `/v1/unload_lora_adapter`

就要把它當成管理面 API。

### 10.4 先用 `/v1/models` 當檢查點

不管是靜態掛載還是動態載入，只要你懷疑：

- LoRA 有沒有成功註冊
- served name 是什麼
- model lineage 對不對

先看：

```bash
curl http://localhost:8000/v1/models
```

通常最快。

---

## 11. 一頁式速查

### 11.1 啟動固定 LoRA server

```bash
vllm serve <BASE_MODEL> \
  --enable-lora \
  --max-lora-rank <MAX_RANK> \
  --lora-modules <LORA_NAME>=<LORA_PATH>
```

### 11.2 啟動可 runtime 載入 LoRA 的 server

```bash
export VLLM_ALLOW_RUNTIME_LORA_UPDATING=True

vllm serve <BASE_MODEL> \
  --enable-lora
```

### 11.3 runtime 載入 LoRA

```bash
curl -X POST http://localhost:8000/v1/load_lora_adapter \
  -H "Content-Type: application/json" \
  -d '{
    "lora_name": "my_lora",
    "lora_path": "/path/to/my_lora"
  }'
```

### 11.4 runtime 卸載 LoRA

```bash
curl -X POST http://localhost:8000/v1/unload_lora_adapter \
  -H "Content-Type: application/json" \
  -d '{
    "lora_name": "my_lora"
  }'
```

### 11.5 filesystem resolver

```bash
export VLLM_ALLOW_RUNTIME_LORA_UPDATING=true
export VLLM_PLUGINS=lora_filesystem_resolver
export VLLM_LORA_RESOLVER_CACHE_DIR=/path/to/lora/adapters

vllm serve <BASE_MODEL> \
  --enable-lora
```

---

## 12. 我會怎麼選

如果是我來規劃：

- **小型、固定 LoRA 業務**：靜態掛載
- **LoRA 版本常更新**：runtime API
- **大量租戶 / 大量 adapter**：resolver plugin

核心原因不是功能多寡，而是：

- 啟動流程是否簡單
- 記憶體是否可控
- 管理面是否安全
- LoRA 數量是否會爆炸

如果你要，我下一份也可以直接幫你補：

- `docker run` 版本
- `docker-compose` 版本
- Kubernetes / Helm 部署版本
- Nginx 反向代理保護 `/v1/load_lora_adapter` 的範例
