以下整理成可直接拿去用的 vLLM Sleep Mode 部署筆記，基於 `vLLM v0.23.0`。來源主要是官方文件與 release / PyPI 資訊。[PyPI](https://pypi.org/project/vllm/) [Sleep Mode](https://docs.vllm.ai/en/stable/features/sleep_mode/) [Security](https://docs.vllm.ai/en/stable/usage/security/) [Online Serving](https://docs.vllm.ai/en/stable/serving/online_serving/) [LLM API](https://docs.vllm.ai/en/stable/api/vllm/entrypoints/llm/)

**1. 最常用場景：暫時釋放 VRAM，之後還是跑同一個模型**

這種情況用 `level=1` 最合適。

啟動 server：
```bash
VLLM_SERVER_DEV_MODE=1 vllm serve Qwen/Qwen3-0.6B \
  --enable-sleep-mode \
  --host 0.0.0.0 \
  --port 8000
```

進入 sleep：
```bash
curl -X POST 'http://127.0.0.1:8000/sleep?level=1'
```

喚醒：
```bash
curl -X POST 'http://127.0.0.1:8000/wake_up'
```

檢查是否仍在睡眠：
```bash
curl -X GET 'http://127.0.0.1:8000/is_sleeping'
```

適用時機：
- 同一張 GPU 上要暫時讓別的工作吃顯存
- 不想停容器、不想重啟 vLLM
- 稍後還要繼續跑同一個模型

注意：
- `level=1` 會把 weights 搬到 CPU RAM，所以主機記憶體要夠大
- KV cache 會被丟掉，醒來後不會保留之前的 cache

**2. RLHF / 權重更新 / 想把 GPU 幾乎清空**

這種情況才用 `level=2`。

官方建議流程：
```bash
curl -X POST 'http://127.0.0.1:8000/sleep?level=2'

curl -X POST 'http://127.0.0.1:8000/wake_up?tags=weights'

curl -X POST 'http://127.0.0.1:8000/collective_rpc' \
  -H 'Content-Type: application/json' \
  -d '{"method":"reload_weights"}'

curl -X POST 'http://127.0.0.1:8000/wake_up?tags=kv_cache'
```

這個流程的意思是：
- 先深度睡眠，把 weights + KV cache 都丟掉
- 先只把 weights 需要的記憶體區域叫回來
- 用 `reload_weights` 把權重重新載回去
- 最後再把 KV cache 區域叫回來

適用時機：
- RLHF / online weight update
- 想避免 weight update 當下同時分配 KV cache 造成 OOM
- 要切模型或更新模型狀態

不建議的時機：
- 單純「省 VRAM 等一下再回來跑同模型」
- 量化模型、LoRA、特殊配置很多的服務環境，除非你已經實測穩定

**3. 啟動參數到底要加哪些**

必要條件只有兩個：

```bash
VLLM_SERVER_DEV_MODE=1
--enable-sleep-mode
```

缺一不可。
- 只加 `--enable-sleep-mode` 不夠，sleep endpoint 不會出現
- 只設 `VLLM_SERVER_DEV_MODE=1` 也不夠，沒有 enable sleep mode

CLI 參數本身的官方定義是：
- `--enable-sleep-mode`
- `--no-enable-sleep-mode`

官方也明寫這個功能目前支援：
- `CUDA`
- `ROCm`

另外，sleep mode 會自動啟用 `cumem allocator`。

**4. 可用的 endpoint**

啟用後，常用的是這些：

- `POST /sleep`
- `POST /wake_up`
- `GET /is_sleeping`
- `POST /collective_rpc`

常見呼叫形式：
```bash
curl -X POST 'http://127.0.0.1:8000/sleep?level=1'
curl -X POST 'http://127.0.0.1:8000/wake_up'
curl -X POST 'http://127.0.0.1:8000/wake_up?tags=weights'
curl -X GET  'http://127.0.0.1:8000/is_sleeping'
```

補充：
- `wake_up` 的 `tags` 官方文件主要示範 `weights`、`kv_cache`
- API 參考文件另外提到還有 `scheduling`，那比較偏 Python / engine 控制語意，不是一般部署最常用路徑

**5. Sleep level 怎麼選**

`level=1`
- weights 搬到 CPU
- KV cache 丟掉
- 適合同模型恢復
- 比較像「熱待命」

`level=2`
- weights 和 KV cache 都丟掉
- CPU 壓力較小
- 適合 reload weights / 切模型 / RLHF
- 流程比較脆弱，通常要搭配 `reload_weights`

我的實務建議很簡單：
- 要穩：先用 `level=1`
- 要做訓練整合或權重切換：再研究 `level=2`

**6. Docker 用法**

`docker run` 例子：

```bash
docker run --gpus all --rm -it \
  -p 8000:8000 \
  -e VLLM_SERVER_DEV_MODE=1 \
  vllm/vllm-openai:latest \
  --model Qwen/Qwen3-0.6B \
  --enable-sleep-mode \
  --port 8000
```

重點：
- `VLLM_SERVER_DEV_MODE=1` 是環境變數
- `--enable-sleep-mode` 是容器內 vLLM 啟動參數
- 兩者位置不要搞反

如果是 `docker-compose.yml`，概念也是一樣：

```yaml
services:
  vllm:
    image: vllm/vllm-openai:latest
    environment:
      - VLLM_SERVER_DEV_MODE=1
    command:
      - --model
      - Qwen/Qwen3-0.6B
      - --enable-sleep-mode
      - --port
      - "8000"
    ports:
      - "8000:8000"
```

**7. Kubernetes 用法**

K8s Pod / Deployment 內要同時放：

- env:
```yaml
env:
  - name: VLLM_SERVER_DEV_MODE
    value: "1"
```

- args:
```yaml
args:
  - --model
  - Qwen/Qwen3-0.6B
  - --enable-sleep-mode
  - --port
  - "8000"
```

但 K8s 最重要的不是 YAML，而是安全邊界：
- 不要把 `/sleep`、`/wake_up`、`/collective_rpc` 直接暴露給外部
- 最好只允許內部管理平面或 sidecar / operator 打這些 endpoint
- 若走 Ingress / Gateway，請明確 block 非必要路徑

**8. 安全性：這個功能不能當成一般 production API 對外開**

這點官方講得非常重。

因為 `VLLM_SERVER_DEV_MODE=1` 會打開 development endpoints，包括：
- `/sleep`
- `/wake_up`
- `/is_sleeping`
- `/collective_rpc`
- `/server_info`
- 各種 reset cache endpoint

其中最危險的是：
- `/collective_rpc`：可做任意 RPC
- `/sleep`：任何打得到的人都能讓服務進入拒絕服務狀態

所以正式環境建議：
- 只在內網管理網段開放
- 反向代理只 allowlist 真的要公開的 endpoint
- 使用者流量只給 `/v1/...` 類介面
- Sleep 控制改由內部控制面呼叫

**9. 已知坑與風險**

雖然官方文件有正式寫進去，但 `level=2` 相關路徑目前仍有一些已知風險，尤其這幾類最值得注意：
- wake 後輸出亂碼 / gibberish
- quantized model 搭配 `reload_weights`
- LoRA 場景
- 某些多卡或特定平台組合

可參考：
- [sleep level 2 causes gibberish outputs #29341](https://github.com/vllm-project/vllm/issues/29341)
- [reload_weights fails on quantized weights #28606](https://github.com/vllm-project/vllm/issues/28606)
- [level-2 sleep/wake with LoRA crash #39934](https://github.com/vllm-project/vllm/issues/39934)

所以如果你是上線導向，我的建議是：
- `level=1` 可優先導入
- `level=2` 必須先用你的模型、量化方式、LoRA 配置、TP/DP 配置完整壓測

**10. 最後給你一份選型建議**

如果你的需求是「模型閒置時釋放顯存，但之後還要快速恢復服務」：
- 用 `level=1`

如果你的需求是「RLHF / 權重熱更新 / 切換模型 / 盡量降低 CPU 記憶體壓力」：
- 用 `level=2`
- 但一定要配 `wake_up?tags=weights -> collective_rpc reload_weights -> wake_up?tags=kv_cache`

如果你的需求是「正式對外 API 服務，希望很穩」：
- 不要直接把 dev endpoints 對外暴露
- 最好把 sleep 控制藏在內部運維流程