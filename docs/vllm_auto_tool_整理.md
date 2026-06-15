# vLLM `--enable-auto-tool-choice` 與 `--tool-call-parser` 最新用法整理

本文整理的是 **vLLM 最新 developer preview 文件** 與 **`main` 分支目前內建 parser registry**，重點是：

- `vllm serve` 開啟 automatic tool calling 的正確方式
- `--tool-call-parser` 各 parser 應該怎麼選
- 常見 parser 的最新啟動指令
- 目前內建 parser 的完整清單

查核時間：`2026-06-15`

主要來源：

- [Tool Calling](https://docs.vllm.ai/en/latest/features/tool_calling/)
- [OpenAI Chat Completion Client With Tools](https://docs.vllm.ai/en/latest/examples/tool_calling/openai_chat_completion_client_with_tools/)
- [cli_args API](https://docs.vllm.ai/en/latest/api/vllm/entrypoints/openai/cli_args/)
- [tool_parsers API](https://docs.vllm.ai/en/latest/api/vllm/tool_parsers/)
- [Qwen3.5 & Qwen3.6 Usage Guide](https://docs.vllm.ai/projects/recipes/en/latest/Qwen/Qwen3.5.html)
- vLLM `main` 分支 `vllm/tool_parsers/__init__.py`

注意：

- `latest` 文件頁本身標明是 **developer preview docs**
- 穩定版文件可能落後於 `latest`
- 本文以 **2026-06-15 當下的 `latest` 文件 + `main` 分支 source** 為準

---

## 1. 先講結論

如果你要讓 vLLM 在 OpenAI-compatible server 裡支援 `tool_choice="auto"`，最核心的是：

```bash
vllm serve <MODEL> \
  --enable-auto-tool-choice \
  --tool-call-parser <PARSER>
```

常常還要再加：

```bash
  --chat-template <TEMPLATE>
```

有些 reasoning / thinking 模型還要再加：

```bash
  --reasoning-parser <REASONING_PARSER>
```

最重要的理解：

1. **Named function calling** 通常不用 `--enable-auto-tool-choice` 也能工作。
2. **Auto tool choice** 才需要 `--enable-auto-tool-choice` + `--tool-call-parser`。
3. parser 要跟模型的 **工具呼叫輸出格式** 對上，不是看模型品牌隨便猜。

---

## 2. `--enable-auto-tool-choice` 到底做了什麼

官方文件的意思很直接：

- `--enable-auto-tool-choice`
  - 告訴 vLLM：允許模型自己判斷要不要呼叫工具
- `--tool-call-parser`
  - 告訴 vLLM：模型吐出來的 tool-call 文字要用哪個 parser 轉成 OpenAI `tool_calls` 格式
- `--tool-parser-plugin`
  - 如果內建 parser 不夠，用你自己的 parser plugin
- `--chat-template`
  - 某些模型雖然支援 tool calling，但必須換工具專用 chat template 才會正常

最通用的啟動骨架：

```bash
vllm serve <MODEL> \
  --enable-auto-tool-choice \
  --tool-call-parser <PARSER> \
  --chat-template <TEMPLATE_IF_NEEDED>
```

---

## 3. 最小可用請求範例

這邊用 OpenAI Python client 打 vLLM：

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="dummy",
)

tools = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string"},
                    "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
                },
                "required": ["location"],
            },
        },
    }
]

resp = client.chat.completions.create(
    model="YOUR_MODEL",
    messages=[
        {"role": "user", "content": "幫我查台北今天天氣"},
    ],
    tools=tools,
    tool_choice="auto",
)

print(resp.choices[0].message)
```

如果你要串流：

```python
stream = client.chat.completions.create(
    model="YOUR_MODEL",
    messages=[{"role": "user", "content": "幫我查台北今天天氣"}],
    tools=tools,
    tool_choice="auto",
    stream=True,
)

for chunk in stream:
    print(chunk)
```

---

## 4. 我建議怎麼選 parser

如果你不是要研究 parser 實作，而是想先把服務跑起來，用這張表最快。

| 模型家族 / 場景 | 建議 parser | 是否常要自訂 chat template | 備註 |
| --- | --- | --- | --- |
| Hermes 系列 | `hermes` | 通常不用，必要時可自訂 | 通用且穩定，Qwen2.5 也常借這個格式 |
| Mistral 工具呼叫 | `mistral` | 常要 | 官方特別提醒要用 vLLM 提供的 template |
| Llama 3.1 / 3.2 JSON 工具呼叫 | `llama3_json` | 常要 | JSON 風格 |
| Llama 4 JSON 工具呼叫 | `llama4_json` | 常要 | 與 `llama3_json` 共用 parser 類 |
| Llama 4 Pythonic 工具呼叫 | `llama4_pythonic` | 常要 | Llama 4 現在更建議這個 |
| 一般 Pythonic 工具呼叫模型 | `pythonic` | 常要 | 支援 python list/function-call 風格 |
| xLAM | `xlam` | 依 Llama/Qwen 版本不同 | Llama 與 Qwen 用不同 template |
| Qwen2.5 / QwQ | `hermes` | 通常不用 | 官方說 tokenizer_config 已含 Hermes-style tool use |
| Qwen3-Coder | `qwen3_xml` 或 `qwen3_coder` | 視模型而定 | 兩個名字在目前 source 都指到同一 parser 類 |
| DeepSeek-V3 / R1 | `deepseek_v3` | 要 | 對應專用 template |
| DeepSeek-V3.1 | `deepseek_v31` | 要 | 對應專用 template |
| GLM-4.5 / 4.6 | `glm45` | 通常不用額外 template | XML / tag 風格 |
| GLM-4.7 | `glm47` | 通常不用額外 template | 與 4.5 有格式差異 |
| Hunyuan-A13B | `hunyuan_a13b` | HF 內建 | reasoning 時可再配 reasoning parser |
| Cohere Command A Reasoning | `cohere_command3` | 視模型 | 要先安裝 `cohere_melody` |
| OpenAI OSS (`gpt-oss-*`) | `openai` | 通常不用 | 走 Harmony / OpenAI OSS 解析鏈 |
| FunctionGemma | `functiongemma` | 要 | 用專用 `tool_chat_template_functiongemma.jinja` |
| Gemma 4 | `gemma4` | 視模型 | 自訂非 JSON serialization |
| Granite 3.x / 4.x | `granite` / `granite4` / `granite-20b-fc` | 常要 | 版本差異很大 |

---

## 5. 最常用 parser 的最新啟動方式

### 5.1 Hermes

適合：

- `NousResearch/Hermes-2-Pro-*`
- `NousResearch/Hermes-3-*`
- Qwen2.5 / QwQ 這類 Hermes-style 工具格式模型

啟動：

```bash
vllm serve NousResearch/Hermes-2-Pro-Llama-3-8B \
  --enable-auto-tool-choice \
  --tool-call-parser hermes
```

Qwen2.5 / QwQ：

```bash
vllm serve Qwen/Qwen2.5-7B-Instruct \
  --enable-auto-tool-choice \
  --tool-call-parser hermes
```

官方 `latest` 文件明確寫到：Qwen2.5 的 `tokenizer_config.json` 已經內建 Hermes-style tool use 支援，因此可直接用 `hermes`。

### 5.2 Mistral

適合：

- `mistralai/Mistral-7B-Instruct-v0.3`
- 其他相容 Mistral function-calling 模型

最重要的注意事項：

- vLLM 官方特別提醒：**Mistral 不要直接信任模型原生 chat template**
- 要用 vLLM 提供的 Mistral tool template

官方格式：

```bash
vllm serve mistralai/Mistral-7B-Instruct-v0.3 \
  --chat-template examples/tool_chat_template_mistral.jinja \
  --enable-auto-tool-choice \
  --tool-call-parser mistral
```

如果你比較在意 parallel tool calling 的穩定性，文件提到可用較好的版本：

```bash
vllm serve mistralai/Mistral-7B-Instruct-v0.3 \
  --chat-template examples/tool_chat_template_mistral_parallel.jinja \
  --enable-auto-tool-choice \
  --tool-call-parser mistral
```

### 5.3 Llama 3.1 / 3.2 JSON

適合：

- `meta-llama/Llama-3.1-*`
- `meta-llama/Llama-3.2-*`

啟動：

```bash
vllm serve meta-llama/Llama-3.1-8B-Instruct \
  --enable-auto-tool-choice \
  --tool-call-parser llama3_json \
  --chat-template examples/tool_chat_template_llama3.1_json.jinja
```

Llama 3.2：

```bash
vllm serve meta-llama/Llama-3.2-3B-Instruct \
  --enable-auto-tool-choice \
  --tool-call-parser llama3_json \
  --chat-template examples/tool_chat_template_llama3.2_json.jinja
```

官方文件提醒：

- 這裡講的是 **JSON-based tool calling**
- Llama 3.2 另有 pythonic 風格，請看 `pythonic`
- Llama 4 則更建議 `llama4_pythonic`

### 5.4 Llama 4 Pythonic

適合：

- `meta-llama/Llama-4-*`

啟動：

```bash
vllm serve meta-llama/Llama-4-Scout-17B-16E-Instruct \
  --enable-auto-tool-choice \
  --tool-call-parser llama4_pythonic \
  --chat-template examples/tool_chat_template_llama4_pythonic.jinja
```

如果你只是 generic pythonic parser，也可用 `pythonic`，但 Llama 4 官方現在更明顯偏向 `llama4_pythonic`。

### 5.5 Generic Pythonic

適合：

- `meta-llama/Llama-3.2-*`
- `Team-ACE/ToolACE-8B`
- `fixie-ai/ultravox-v0_4-ToolACE-8B`
- 某些會輸出 Python list / `func(arg=...)` 風格的模型

啟動：

```bash
vllm serve Team-ACE/ToolACE-8B \
  --enable-auto-tool-choice \
  --tool-call-parser pythonic \
  --chat-template examples/tool_chat_template_toolace.jinja
```

Llama 3.2：

```bash
vllm serve meta-llama/Llama-3.2-3B-Instruct \
  --enable-auto-tool-choice \
  --tool-call-parser pythonic \
  --chat-template examples/tool_chat_template_llama3.2_pythonic.jinja
```

限制：

1. 模型最好不要在同一段 generation 裡同時輸出自然語言和 tool calls
2. 小模型的 tool-call 格式容易不穩

### 5.6 xLAM

適合：

- `Salesforce/Llama-xLAM-*`
- `Salesforce/xLAM-*`
- `Salesforce/Qwen-xLAM-*`

Llama-based xLAM：

```bash
vllm serve Salesforce/Llama-xLAM-2-8B-fc-r \
  --enable-auto-tool-choice \
  --tool-call-parser xlam \
  --chat-template examples/tool_chat_template_xlam_llama.jinja
```

Qwen-based xLAM：

```bash
vllm serve Salesforce/Qwen-xLAM-32B-fc-r \
  --enable-auto-tool-choice \
  --tool-call-parser xlam \
  --chat-template examples/tool_chat_template_xlam_qwen.jinja
```

### 5.7 Qwen3-Coder

官方 `latest` tool calling guide 對 Qwen3-Coder 的 section 寫的是：

- parser 名稱：`qwen3_xml`

啟動：

```bash
vllm serve Qwen/Qwen3-Coder-30B-A3B-Instruct \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_xml
```

但同時，官方 recipe 頁又寫：

- `Qwen3.5 / Qwen3.6` 開 tool calling 時可用 `qwen3_coder`

而 `main` 分支目前 source 顯示：

- `qwen3_coder`
- `qwen3_xml`

這兩個名字都指向同一個 `Qwen3EngineToolParser` 類。

所以實務建議：

- **Qwen3-Coder**：優先跟 `latest` tool calling guide 走，先用 `qwen3_xml`
- **如果是新版 Qwen3.x recipe / 專用場景**：可測 `qwen3_coder`

### 5.8 DeepSeek-V3 / R1

啟動：

```bash
vllm serve deepseek-ai/DeepSeek-V3-0324 \
  --enable-auto-tool-choice \
  --tool-call-parser deepseek_v3 \
  --chat-template examples/tool_chat_template_deepseekv3.jinja
```

R1 0528：

```bash
vllm serve deepseek-ai/DeepSeek-R1-0528 \
  --enable-auto-tool-choice \
  --tool-call-parser deepseek_v3 \
  --chat-template examples/tool_chat_template_deepseekr1.jinja
```

### 5.9 DeepSeek-V3.1

```bash
vllm serve deepseek-ai/DeepSeek-V3.1 \
  --enable-auto-tool-choice \
  --tool-call-parser deepseek_v31 \
  --chat-template examples/tool_chat_template_deepseekv31.jinja
```

### 5.10 GLM-4.5 / 4.6

適合：

- `zai-org/GLM-4.5`
- `zai-org/GLM-4.5-Air`
- `zai-org/GLM-4.6`

```bash
vllm serve zai-org/GLM-4.5 \
  --enable-auto-tool-choice \
  --tool-call-parser glm45
```

### 5.11 GLM-4.7

適合：

- `zai-org/GLM-4.7`
- `zai-org/GLM-4.7-Flash`

```bash
vllm serve zai-org/GLM-4.7 \
  --enable-auto-tool-choice \
  --tool-call-parser glm47
```

### 5.12 Hunyuan-A13B

非 reasoning：

```bash
vllm serve tencent/Hunyuan-A13B-Instruct \
  --enable-auto-tool-choice \
  --tool-call-parser hunyuan_a13b
```

reasoning 版本：

```bash
vllm serve tencent/Hunyuan-A13B-Instruct \
  --enable-auto-tool-choice \
  --tool-call-parser hunyuan_a13b \
  --reasoning-parser hunyuan_a13b
```

### 5.13 Cohere Command A Reasoning

適合：

- `CohereLabs/command-a-reasoning-08-2025`

先安裝：

```bash
pip install cohere_melody
```

再啟動：

```bash
vllm serve CohereLabs/command-a-reasoning-08-2025 \
  --enable-auto-tool-choice \
  --tool-call-parser cohere_command3 \
  --reasoning-parser cohere_command3
```

### 5.14 OpenAI OSS (`gpt-oss-*`)

適合：

- `openai/gpt-oss-20b`
- `openai/gpt-oss-120b`

```bash
vllm serve openai/gpt-oss-20b \
  --enable-auto-tool-choice \
  --tool-call-parser openai
```

### 5.15 Kimi-K2

```bash
vllm serve moonshotai/Kimi-K2-Instruct \
  --enable-auto-tool-choice \
  --tool-call-parser kimi_k2
```

### 5.16 Granite

Granite 4.0：

```bash
vllm serve ibm-granite/granite-4.0-h-small \
  --enable-auto-tool-choice \
  --tool-call-parser granite4
```

Granite 3.0：

```bash
vllm serve ibm-granite/granite-3.0-8b-instruct \
  --enable-auto-tool-choice \
  --tool-call-parser granite \
  --chat-template examples/tool_chat_template_granite.jinja
```

Granite 20B Function Calling：

```bash
vllm serve ibm-granite/granite-20b-functioncalling \
  --enable-auto-tool-choice \
  --tool-call-parser granite-20b-fc \
  --chat-template examples/tool_chat_template_granite_20b_fc.jinja
```

### 5.17 FunctionGemma

```bash
vllm serve google/functiongemma-270m-it \
  --enable-auto-tool-choice \
  --tool-call-parser functiongemma \
  --chat-template examples/tool_chat_template_functiongemma.jinja
```

### 5.18 Gemma 4

```bash
vllm serve <YOUR_GEMMA4_MODEL> \
  --enable-auto-tool-choice \
  --tool-call-parser gemma4
```

官方 API 文件特別指出：Gemma 4 的工具呼叫不是 JSON，而是自訂 serialization，所以 parser 會走「accumulate -> parse -> diff」的 streaming 策略。

### 5.19 InternLM

```bash
vllm serve internlm/internlm2_5-7b-chat \
  --enable-auto-tool-choice \
  --tool-call-parser internlm \
  --chat-template examples/tool_chat_template_internlm2_tool.jinja
```

### 5.20 Jamba

```bash
vllm serve ai21labs/AI21-Jamba-1.5-Mini \
  --enable-auto-tool-choice \
  --tool-call-parser jamba
```

### 5.21 LongCat

```bash
vllm serve meituan-longcat/LongCat-Flash-Chat \
  --enable-auto-tool-choice \
  --tool-call-parser longcat
```

### 5.22 MiniMax M1

```bash
vllm serve MiniMaxAI/MiniMax-M1-40k \
  --enable-auto-tool-choice \
  --tool-call-parser minimax \
  --chat-template examples/tool_chat_template_minimax_m1.jinja
```

### 5.23 Olmo 3

```bash
vllm serve allenai/Olmo-3-7B-Instruct \
  --enable-auto-tool-choice \
  --tool-call-parser olmo3
```

### 5.24 GigaChat 3

```bash
vllm serve ai-sage/GigaChat3-10B-A1.8B \
  --enable-auto-tool-choice \
  --tool-call-parser gigachat3
```

### 5.25 Apertus

官方 `latest` 文件建議使用 examples 裡的 template：

```bash
vllm serve swiss-ai/Apertus-8B-Instruct-2509 \
  --enable-auto-tool-choice \
  --tool-call-parser apertus \
  --chat-template /vllm-workspace/examples/tool_chat_template_apertus.jinja
```

---

## 6. `qwen3_xml` 和 `qwen3_coder` 現在到底差在哪

這個是目前最容易混淆的點之一。

我在 2026-06-15 查到的狀態是：

1. `latest` 的 tool calling 功能頁，把 **Qwen3-Coder** 寫成 `qwen3_xml`
2. 官方 recipes 的 Qwen3.5/3.6 頁，示範的是 `qwen3_coder`
3. `main` 分支目前 `vllm/tool_parsers/__init__.py` 裡，這兩個名字都註冊到同一個類：
   - `qwen3_coder -> Qwen3EngineToolParser`
   - `qwen3_xml -> Qwen3EngineToolParser`

所以我的建議是：

- 文檔對齊：先用 `qwen3_xml`
- recipe 對齊：看到 Qwen3.x recipe 時，不要意外 `qwen3_coder`
- 實作上：兩者在當前 source 是同一條 parser engine

---

## 7. `pythonic`、`llama4_pythonic`、`llama3_json` 怎麼選

可以這樣記：

- 你看到模型輸出是 **JSON 工具呼叫**：先考慮 `llama3_json` / `llama4_json`
- 你看到模型輸出是 **`foo(arg=...)` 或 `[foo(...), bar(...)]`**：先考慮 `pythonic`
- 你是 **Llama 4**：優先試 `llama4_pythonic`

快速決策：

| 情況 | 建議 |
| --- | --- |
| Llama 3.1 工具呼叫 | `llama3_json` |
| Llama 3.2 JSON 模式 | `llama3_json` |
| Llama 3.2 Pythonic 模式 | `pythonic` |
| Llama 4 | `llama4_pythonic` 優先 |
| ToolACE / 類 ToolACE | `pythonic` |

---

## 8. 常見坑

### 8.1 `tool_choice="auto"` 沒反應

先檢查：

1. 有沒有加 `--enable-auto-tool-choice`
2. 有沒有指定正確 `--tool-call-parser`
3. tools schema 是否合法
4. chat template 是否真的支援工具格式

### 8.2 模型會輸出工具，但 vLLM 沒 parse 成 `tool_calls`

通常是：

- parser 選錯
- chat template 選錯
- 模型輸出的格式跟 parser 預期不一樣

### 8.3 Mistral 看起來最常壞

因為官方文件已經明說：

- Mistral 的原始 chat template 跟 vLLM tool-call IDs 有兼容問題
- 請直接用 vLLM 提供的 `tool_chat_template_mistral*.jinja`

### 8.4 Pythonic parser 不是萬能

官方文件直接列限制：

1. 模型不要混自然語言和 tool calls 同時輸出
2. 小模型工具格式常常不穩

### 8.5 reasoning 模型要分開看

官方 reasoning docs 提到：

- reasoning content 和 tool calling 可以同時存在
- 但 tool calling 只會從 `content` 解析，不會去 `reasoning` 裡抓工具呼叫

所以如果是 thinking / reasoning 模型，常常要同時配：

```bash
--tool-call-parser <...> --reasoning-parser <...>
```

---

## 9. 自訂 parser

如果內建 parser 不夠，用：

```bash
vllm serve <MODEL> \
  --enable-auto-tool-choice \
  --tool-parser-plugin /path/to/your_parser.py \
  --tool-call-parser your_parser_name
```

官方文件建議可參考 `Hermes2ProToolParser` 來寫 plugin。

---

## 10. 2026-06-15 當下 `main` 分支內建 parser registry 完整清單

這份清單來自 `vllm/tool_parsers/__init__.py`，比功能頁更完整。  
目前 registry 共有 **43** 個 parser name：

```text
deepseek_v3
deepseek_v31
deepseek_v32
deepseek_v4
cohere_command3
cohere_command4
ernie45
glm45
glm47
granite-20b-fc
granite
granite4
hermes
poolside_v1
hunyuan_a13b
hy_v3
internlm
jamba
lfm2
kimi_k2
llama3_json
llama4_json
llama4_pythonic
longcat
mimo
minimax_m2
minimax
minicpm5
mistral
olmo3
openai
phi4_mini_json
pythonic
qwen3_coder
qwen3_xml
seed_oss
step3
step3p5
xlam
gigachat3
functiongemma
gemma4
apertus
```

其中：

- **功能頁已明確寫法 / 推薦命令的 parser**：像 `hermes`, `mistral`, `llama3_json`, `llama4_pythonic`, `xlam`, `deepseek_v3`, `deepseek_v31`, `glm45`, `glm47`, `qwen3_xml`, `openai`, `kimi_k2`, `hunyuan_a13b`, `cohere_command3`, `functiongemma`, `gemma4`, `olmo3`, `gigachat3`, `apertus`
- **registry 已存在、但功能頁沒有完整展開最新用法的 parser**：像 `deepseek_v32`, `deepseek_v4`, `cohere_command4`, `ernie45`, `poolside_v1`, `hy_v3`, `lfm2`, `mimo`, `minimax_m2`, `minicpm5`, `phi4_mini_json`, `seed_oss`, `step3`, `step3p5`

如果你要用後面這批 parser，建議：

1. 先看模型官方卡片或 recipe 是否明示該 parser
2. 再對照 `vllm/tool_parsers/*.py` 的實作與樣例格式
3. 最後用小量 request 驗證是否真的能穩定產出 `tool_calls`

---

## 11. 我建議的實務策略

### 11.1 如果你是第一次把 tool calling 跑起來

先選官方文件寫得最完整的一組：

- Hermes：`hermes`
- Mistral：`mistral`
- Llama 3.1：`llama3_json`
- Llama 4：`llama4_pythonic`
- xLAM：`xlam`

### 11.2 如果你是 Qwen

- Qwen2.5 / QwQ：先用 `hermes`
- Qwen3-Coder：先用 `qwen3_xml`
- 如果 recipe 明寫 `qwen3_coder`，可以再測一次 `qwen3_coder`

### 11.3 如果你是 reasoning 模型

優先看是否要同時加 `--reasoning-parser`：

- `hunyuan_a13b`
- `cohere_command3`
- 某些 DeepSeek / Qwen / GLM 類模型也要留意 reasoning 與 tool parser 是否配套

### 11.4 如果你是冷門或新模型

優先順序：

1. 先查 `latest` Tool Calling 頁有沒有明寫
2. 再查 `main` 的 tool parser registry 有沒有註冊名
3. 再看是否需要 `--chat-template`
4. 最後才考慮自己寫 `--tool-parser-plugin`

---

## 12. 一份通用模板

如果你只是要先驗證某個 parser 能不能工作，這份最萬用：

```bash
vllm serve <MODEL> \
  --enable-auto-tool-choice \
  --tool-call-parser <PARSER> \
  --chat-template <TEMPLATE_IF_NEEDED>
```

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="dummy")

tools = [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get weather",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {"type": "string"}
            },
            "required": ["location"]
        }
    }
}]

resp = client.chat.completions.create(
    model="<MODEL>",
    messages=[{"role": "user", "content": "What's the weather in Taipei?"}],
    tools=tools,
    tool_choice="auto",
)

print(resp)
```

如果這份都不會回 `tool_calls`，優先懷疑：

- parser 不對
- template 不對
- 模型本身工具格式不穩

