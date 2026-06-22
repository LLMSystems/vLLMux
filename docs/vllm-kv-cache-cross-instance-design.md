# 跨 instance 共享 KV Cache — 設計與可行性（單機多 instance / Docker）

更新日期：2026-06-20
狀態：設計提案（尚未實作）
適用環境：**單機、單/多 GPU、多個 vLLM instance、Docker Compose**（非 K8s、非多節點）

> 前置閱讀：[vllm-kv-cache-sharing-howto.md](vllm-kv-cache-sharing-howto.md)
> 本文是該 howto **§3 OffloadingConnector** 在本專案的落地設計。

---

## 1. 結論先講

- **目標**：讓同一個模型群組的多個 vLLM instance（例如 `Qwen3-0.6B::qwen3`、`qwen3-2`）能**跨 process 重用彼此算過的 KV block**，而不是各自從零 prefill。
- **選定方案**：howto **§3 `OffloadingConnector` + 共享 `root_dir`**。理由見 §3。
- **為什麼不是 §4 LMCache / §5 Mooncake**：那兩個分別為 K8s 多 replica、多節點分散式設計，對「單機多 process」是過度工程（多一個 server / RDMA / metadata server 要顧）。本專案的 vLLM instance 是 backend 在**同一容器內 spawn 的子行程**，共享檔案系統，§3 的「共享目錄」幾乎零基礎設施成本。
- **重要前提**：單機 instance 內的 `Automatic Prefix Caching` 在 vLLM V1（本專案 0.23.0）**已預設開啟**，已有 Grafana 命中率面板。本設計處理的是**跨 instance** 那一層，與 APC 互補、不衝突。

---

## 2. 範圍與非目標

**做：**
- 單機多 instance 透過共享磁碟（Docker named volume）做 KV block offload + 跨 process 重用。
- launcher / config / compose / 測試的對應改動。

**不做（本文範圍外）：**
- K8s 多 replica（→ 未來若改架構再評估 LMCache）。
- 多節點分散式 KV pool（→ Mooncake）。
- prefill/decode 分離（→ MultiConnector）。
- GPU memory 直接共享同一塊 KV buffer（vLLM 不走這條；offload 是 store 型）。

---

## 3. 為什麼 §3 最適合本專案

| 考量 | 本專案現況 | 對 §3 的影響 |
|---|---|---|
| instance 啟動方式 | backend 以 `subprocess`（`["vllm", ...]` + env）在**同容器內**拉起（見 `launchers.py` / `process.py`）| 所有 instance 天然共享容器檔案系統 → 共享 `root_dir` 只是一個容器內路徑 |
| 網路 | backend/router/prometheus 共享 netns | 不需要額外網路檔案系統（NFS/PVC），單機 volume 即可 |
| 設定流 | `model_config` 走 `EngineModelConfig(extra="allow")` | KV connector 設定可以 per-group 帶進來（需 launcher 特判，見 §5）|
| 觀測 | 已抓 `vllm:prefix_cache_hits_total / queries_total` | 命中率面板可直接沿用 + 擴充 offload 指標 |

§4/§5 的能力（remote / 分散式）在單機都用不到,只徒增維運面。

---

## 4. 架構設計

```
                 backend container (shared netns)
   ┌───────────────────────────────────────────────────────────┐
   │  backend (lifecycle)                                        │
   │     │ spawn (subprocess, env=PYTHONHASHSEED=0)              │
   │     ├── vllm serve …  qwen3   :8002 ─┐                      │
   │     ├── vllm serve …  qwen3-2 :8004 ─┤  都掛同一個          │
   │     └── vllm serve …  qwen3-N        ┘  root_dir=/kv_cache  │
   │                                  ▼                          │
   │                       /kv_cache  (named volume)             │
   │                    digest 目錄: <model+block+dtype+tp hash> │
   └───────────────────────────────────────────────────────────┘
```

- **共享層**：一個 Docker named volume `llmops-kv-cache` 掛到 backend 的 `/kv_cache`。所有 instance 的 vLLM 都把 `secondary_tiers[].root_dir` 指到 `/kv_cache`。
- **命中前提（howto §3 明列）**：所有共享同一 store 的 process 必須設**相同 `PYTHONHASHSEED`**，否則相同內容產生不同 block hash → 跨 process 命不中。
- **隔離**：vLLM 會用 `model + block_size + dtype + parallelism` 算出 digest 子目錄；改任一項就不會互通（這正是我們要的，避免污染）。

---

## 5. Codebase 改動點

### 5.1 launcher（`apps/backend/app/llmops/launchers.py`）

兩件事：

**(a) 注入 `PYTHONHASHSEED=0`**
在 `VllmLauncher.build_spec` 組 `env` 的區塊（目前設 `CUDA_VISIBLE_DEVICES` / `VLLM_ALLOW_RUNTIME_LORA_UPDATING` 的地方）加：
```python
# 跨 instance KV 共享要求所有 process 用相同 hash seed，否則 block hash 不一致
if merged.get("kv_transfer_config"):
    env.setdefault("PYTHONHASHSEED", "0")
```

**(b) 支援 `--kv-transfer-config`（巢狀 JSON / dict 值）**
現在 `build_vllm_cli_args` 對 `list` 才 `json.dumps`，**dict 值會掉進 `else` 分支被 `str()`**（產生帶單引號的非法 JSON）。需新增 dict 分支：
```python
elif isinstance(value, dict):
    cli_args.append(key_flag)
    cli_args.append(json.dumps(value, ensure_ascii=False))
```
這樣 `kv_transfer_config: {...}` 會正確變成 `--kv-transfer-config '{...}'`。
（`kv_transfer_config` 是純 CLI flag，不需加入 `_SKIP_CLI_KEYS`。）

### 5.2 config schema / config.yaml

per-group `model_config` 內新增（靠 `extra="allow"` 直接帶過，或在 `EngineModelConfig` 顯式加欄位以利驗證）：
```yaml
Qwen3-0.6B:
  model_config:
    kv_transfer_config:
      kv_connector: "OffloadingConnector"
      kv_role: "kv_both"
      kv_connector_extra_config:
        spec_name: "TieringOffloadingSpec"
        cpu_bytes_to_use: 4294967296      # 4GB；依正式機 RAM 調整
        block_size: 16
        eviction_policy: "lru"
        secondary_tiers:
          - type: "fs"
            root_dir: "/kv_cache"
            n_read_threads: 32
            n_write_threads: 16
```
> 建議顯式加欄位（型別安全 + 前端 AddModelDialog 能呈現），而非只靠 `extra="allow"`。

### 5.3 docker-compose（`deploy/docker-compose.yaml`）

```yaml
services:
  backend:
    volumes:
      - llmops-kv-cache:/kv_cache      # 新增：跨 instance 共享 KV store
volumes:
  llmops-kv-cache:                     # 新增 named volume
```

### 5.4 測試

- `test_launchers.py`：新增
  - dict 值 → `--kv-transfer-config '<json>'`（且 JSON 合法、可被 `json.loads` 還原）。
  - 設了 `kv_transfer_config` 時 env 帶 `PYTHONHASHSEED=0`；沒設時不帶。
- type-check（mypy / vue-tsc 若動到前端欄位）。

---

## 6. 與路由層的關係（互補，可選後續）

- **共享層**（本文）解決「KV 能不能跨 instance 重用」。
- **路由層**（howto §7.2 KV-cache-aware routing）解決「請求送到哪台命中率最高」。你**已有 pluggable 路由 registry + 已抓 `vllm:gpu_prefix_cache_hit_rate`**，可在共享層上線後，新增一個 `kv_cache_aware` 策略（送往實際命中率最高的 instance），作為 `prefix_affinity`（盲 hash）的升級版。
- 兩者疊加效益最大；但建議**先共享層、後路由層**，分開驗證。

---

## 7. 風險與限制

| 風險 | 說明 | 緩解 |
|---|---|---|
| 共享 FS 效能 | offload/load KV block 走磁碟，命中成本非零 | volume 用 SSD；`n_read/write_threads` 調校；只對長 prompt 有正收益 |
| RAM 佔用 | `cpu_bytes_to_use` 是 CPU tier 緩衝,吃系統 RAM | 依正式機 RAM 設定;**測試機（~2GB headroom）不要開** |
| 小模型收益低 | 0.6B + `max_model_len=500`,prefill 成本本就極低 | 正式機上大模型 / 長 context 才明顯;測試機僅驗證功能正確性 |
| 非 GPU 直接共享 | 這是 store 型 offload,不是同塊 VRAM buffer | 符合 vLLM 官方主流做法,接受 |
| 配置漂移 | 改 model/block/dtype/tp 會換 digest 目錄,舊 cache 失效 | 預期行為;升級模型時清理 volume |

---

## 8. 落地與驗證步驟（PoC 順序）

1. **launcher 改動 + 測試**（dict flag、PYTHONHASHSEED），先確保 CLI 產出正確、不影響既有行為。
2. **compose 加 volume**，`make up`。
3. **單一群組（Qwen3-0.6B）開兩 instance**，config 加 `kv_transfer_config`，正式機上跑。
4. **驗證跨 process 命中**：
   - 對 instance A 送一個長 system prompt 請求（暖 cache）。
   - 同 prompt 送 instance B。
   - 看 B 的 `vllm:prefix_cache_hits_total` 是否上升、TTFT 是否下降。
   - 確認 `/kv_cache` 出現 digest 目錄與 block 檔。
5. **負向測試**：故意改 `block_size` 或 `dtype`,確認 digest 目錄不同、不互通。
6.（後續）上線 `kv_cache_aware` 路由策略。

---

## 9. 官方參考

- [KV Offloading Usage Guide](https://docs.vllm.ai/en/latest/features/kv_offloading_usage/)（§3 主要依據）
- [Automatic Prefix Caching](https://docs.vllm.ai/en/latest/features/automatic_prefix_caching/)（單機層,已預設開）
- [KVTransferConfig API](https://docs.vllm.ai/en/stable/api/vllm/config/kv_transfer/)
- [KV Cache Aware Routing](https://docs.vllm.ai/projects/production-stack/en/vllm-stack-0.1.8/use_cases/kv-cache-aware-routing.html)（§6 路由層後續）
