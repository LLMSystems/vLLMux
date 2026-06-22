# vLLM 共享或重用 KV Cache：如何使用

更新日期：2026-06-20

## 先講結論

如果你的問題是：

**「同一個模型的多個 vLLM instance，可不可以共用同一份 KV cache？」**

答案是：

- **可以做到共享或重用效果**
- **但通常不是多個 process 直接共用同一塊 GPU 上的同一份 KV buffer**
- 官方主流做法是：
  - `LMCache` 遠端共享 KV cache
  - `MooncakeStoreConnector` 分散式共享 KV cache pool
  - `OffloadingConnector` 把 KV block 寫到共享檔案系統，再讓其他 instance 重用
  - `Automatic Prefix Caching`、`KV-cache-aware routing` 這類「不硬共享，但提高命中率」的方法

所以實務上要分成兩種需求：

1. **真的要跨 instance 重用 KV**
2. **不一定要共享，只要讓相同 prefix 能重複利用**

下面我整理成「怎麼選」和「怎麼啟用」。

## 1. 先選方案

| 需求 | 建議方案 | 難度 | 說明 |
|---|---|---|---|
| 單一 instance 內重用相同 prefix | `Automatic Prefix Caching` | 低 | 最簡單，不跨 instance |
| 多個 instance 共用共享磁碟或 PVC | `OffloadingConnector` + shared `root_dir` | 中 | 類似共享 KV block 檔案 |
| Kubernetes 上多個 vLLM replica 共享 KV | `LMCache` + Production Stack | 中高 | 官方有完整 tutorial |
| 多節點、分散式共享 KV pool | `MooncakeStoreConnector` | 高 | 比較偏正式分散式架構 |
| Prefill/Decode 分離且也想共享 KV | `MultiConnector` + Mooncake | 高 | 進階 serving 架構 |

## 2. 最簡單方案：Automatic Prefix Caching

這個方案 **不是跨 instance 共享**，但如果你的真正目的是「同樣前綴不要重算」，這通常是第一步。

### 適合什麼情境

- 同一台 vLLM server 反覆收到相同 system prompt
- 多輪對話
- 長文件反覆問答

### 怎麼開

官方文件寫法是：

```python
enable_prefix_caching=True
```

或在 serving 配置中啟用 prefix caching。

### 優點

- 最簡單
- 幾乎不需要額外基礎設施
- 對長 prompt 很有幫助

### 限制

- 只在 **同一個 instance 內** 生效
- 只能減少 `prefill` 成本，不能減少 `decode` 成本
- 如果新請求沒有共享 prefix，就沒有收益

官方來源：
- [Automatic Prefix Caching](https://docs.vllm.ai/en/latest/features/automatic_prefix_caching/)

## 3. 共用共享磁碟：OffloadingConnector

這是目前很值得注意的做法，因為官方文件已經明寫：  
**多個 vLLM instance 可以透過相同 `root_dir` 共享 KV cache block。**

### 適合什麼情境

- 多個 vLLM process 掛同一個 shared PVC / NFS / 共用檔案系統
- 你不想先上 LMCache 或 Mooncake
- 想先用比較容易理解的方式驗證「跨 process KV 重用」

### 關鍵前提

所有共享同一個 store 的 instance 都要設相同的：

```bash
PYTHONHASHSEED=0
```

官方文件明確說明，若不固定 `PYTHONHASHSEED`，不同 process 會為相同內容產生不同 block hash，導致無法跨 process 命中。

### 範例

```bash
PYTHONHASHSEED=0 vllm serve <model> \
  --kv-transfer-config '{
    "kv_connector": "OffloadingConnector",
    "kv_role": "kv_both",
    "kv_connector_extra_config": {
      "spec_name": "TieringOffloadingSpec",
      "cpu_bytes_to_use": 10737418240,
      "block_size": 16,
      "eviction_policy": "lru",
      "secondary_tiers": [
        {
          "type": "fs",
          "root_dir": "/mnt/kv_cache",
          "n_read_threads": 32,
          "n_write_threads": 16
        }
      ]
    }
  }'
```

### 使用重點

- 每個 instance 都指到 **同一個** `root_dir`
- 每個 instance 都設同樣的 `PYTHONHASHSEED`
- 同模型、相容配置下才有意義
- 改了模型、block size、parallelism、dtype，會產生不同 digest 目錄，不會共用

### 優點

- 不用額外引入 LMCache server
- 架構相對直觀
- 很適合先做 PoC

### 缺點

- 共享檔案系統效能會影響命中成本
- 這是 offload/store 型共享，不是 GPU memory 直接共享
- 正式多節點大規模部署通常還是會想看 LMCache 或 Mooncake

官方來源：
- [KV Offloading Usage Guide](https://docs.vllm.ai/en/latest/features/kv_offloading_usage/)

## 4. 官方正式共享方案：LMCache

這是 vLLM Production Stack 官方直接提供的「跨 instances 共享 KV cache」教學。

### 適合什麼情境

- Kubernetes 上多個 vLLM replicas
- 想做 remote shared KV cache
- 想照官方 tutorial 落地

### 官方核心概念

官方文件的描述是：  
把大的 KV cache 從 GPU memory 移到 `remote shared storage`，讓多個 vLLM 節點能共享，提高 cache hit rate，並可能提升容錯性。

### 用法 1：Production Stack Helm 教學

官方 tutorial：
- [Sharing KV Cache Across Instances](https://docs.vllm.ai/projects/production-stack/en/latest/use_cases/sharing-kv-cache.html)

大致步驟：

1. 準備 Kubernetes + GPU 環境
2. 使用官方的 `values-06-remote-shared-storage.yaml`
3. 設定 `replicaCount: 2`
4. 啟動 `CacheserverSpec`
5. 用 Helm 部署
6. 驗證兩個 vLLM replica 經由 LMCache 共享 KV

官方這份配置重點包括：

- `replicaCount: 2`
- `v1: 1`
- 啟動 `CacheserverSpec`

### 用法 2：LMCache MP 模式

如果你不是走完整 Production Stack，也可以看 vLLM 官方 example。  
官方把 **`LMCacheMPConnector` + standalone lmcache server** 說成：

- 推薦用於 `distributed KV storage`
- 推薦用於 `sharing KV cache across instances`

### 最簡化範例

先啟動 LMCache server：

```bash
lmcache server --host localhost --port 5555 --l1-size-gb 5
```

再啟動 vLLM：

```bash
vllm serve <model> \
  --port 8000 \
  --kv-offloading-size 5 \
  --kv-offloading-backend lmcache \
  --disable-hybrid-kv-cache-manager
```

上面這組參數是 vLLM 官方 example 裡直接給的 shortcut。

### 等價顯式設定

```bash
vllm serve <model> \
  --kv-transfer-config '{
    "kv_connector":"LMCacheMPConnector",
    "kv_role":"kv_both",
    "kv_connector_extra_config":{
      "lmcache.mp.host":"tcp://localhost",
      "lmcache.mp.port":5555
    }
  }'
```

### 優點

- 官方明確支援多 instance 共享
- 適合 Kubernetes 與正式部署
- 比只靠 shared filesystem 更像完整共享層

### 缺點

- 多一個 LMCache server 要維護
- 架構複雜度比 prefix caching / shared root_dir 高
- 官方 example 也提到某些模式需要 `--disable-hybrid-kv-cache-manager`

官方來源：
- [Sharing KV Cache Across Instances](https://docs.vllm.ai/projects/production-stack/en/latest/use_cases/sharing-kv-cache.html)
- [LMCache Examples](https://docs.vllm.ai/en/latest/examples/disaggregated/lmcache/)

## 5. 分散式共享池：MooncakeStoreConnector

如果你要的是更偏「分散式共享 KV 池」的架構，Mooncake 是官方文件中很明確的一條路。

### 官方定位

官方文件直接寫：

- `MooncakeStoreConnector` 使用 `MooncakeDistributedStore` 作為 `shared KV cache pool`
- 支援 `prefix caching across instances`
- 支援 single-node / multi-node

### 適合什麼情境

- 多節點
- 想做分散式 KV 快取池
- 需要 CPU / disk offload 與跨 instance 共用

### 步驟 1：安裝 Mooncake

```bash
uv pip install mooncake-transfer-engine
```

### 步驟 2：啟動 master

```bash
mooncake_master --port 50051
```

### 步驟 3：建立 `mooncake_config.json`

```json
{
  "mode": "embedded",
  "metadata_server": "P2PHANDSHAKE",
  "master_server_address": "127.0.0.1:50051",
  "global_segment_size": "80GB",
  "local_buffer_size": "4GB",
  "protocol": "rdma",
  "device_name": "",
  "enable_offload": false
}
```

然後設定：

```bash
export MOONCAKE_CONFIG_PATH=/path/to/mooncake_config.json
```

### 步驟 4：啟動 vLLM

```bash
MOONCAKE_CONFIG_PATH=mooncake_config.json \
vllm serve meta-llama/Llama-3.1-8B-Instruct \
  --kv-transfer-config '{"kv_connector":"MooncakeStoreConnector","kv_role":"kv_both"}'
```

### 很重要的坑：固定 `PYTHONHASHSEED`

官方文件明確要求所有共享同一個 Mooncake store 的 process 設同樣的 hash seed：

```bash
PYTHONHASHSEED=0 vllm serve ...
```

不然相同 prompt 在不同 process 會得到不同 block hash，跨 process prefix hit 會失效。

### `kv_role` 怎麼選

- `kv_producer`: 主要負責把 KV 存進 pool
- `kv_consumer`: 主要從 pool 載入 KV
- `kv_both`: 同時可存可讀

單純一般共享場景，通常先從：

```json
"kv_role": "kv_both"
```

開始最簡單。

### 進階：用 `cache_prefix` 隔離命名空間

如果你有多個不同部署共用同一個 Mooncake master，可以在 `kv_connector_extra_config` 裡設：

```json
"cache_prefix": "prod-a"
```

這樣不同部署不會互相污染。

### 優點

- 官方支援的分散式共享池
- 可以跨 instance 做 prefix block 共享
- 架構比單純 shared filesystem 更完整

### 缺點

- 導入成本更高
- 比 LMCache / APC 更需要網路與儲存調校

官方來源：
- [MooncakeStoreConnector Usage Guide](https://docs.vllm.ai/en/latest/features/mooncake_store_connector_usage/)

## 6. Prefill/Decode 分離時怎麼用

如果你的架構是 `disaggregated prefill-decode`，官方建議用 `MultiConnector`，把：

- `MooncakeConnector`：做 prefiller 和 decoder 間的 point-to-point KV transfer
- `MooncakeStoreConnector`：做 shared KV cache pool

組合起來。

### Prefiller 節點範例

```bash
MOONCAKE_CONFIG_PATH=mooncake_config.json \
VLLM_MOONCAKE_BOOTSTRAP_PORT=50052 \
vllm serve meta-llama/Llama-3.1-8B-Instruct \
  --port 8100 \
  --kv-transfer-config '{
    "kv_connector": "MultiConnector",
    "kv_role": "kv_producer",
    "kv_connector_extra_config": {
      "connectors": [
        {
          "kv_connector": "MooncakeConnector",
          "kv_role": "kv_producer"
        },
        {
          "kv_connector": "MooncakeStoreConnector",
          "kv_role": "kv_both"
        }
      ]
    }
  }'
```

### Decoder 節點範例

```bash
MOONCAKE_CONFIG_PATH=mooncake_config.json \
VLLM_MOONCAKE_BOOTSTRAP_PORT=50053 \
vllm serve meta-llama/Llama-3.1-8B-Instruct \
  --port 8200 \
  --kv-transfer-config '{
    "kv_connector": "MultiConnector",
    "kv_role": "kv_consumer",
    "kv_connector_extra_config": {
      "connectors": [
        {
          "kv_connector": "MooncakeConnector",
          "kv_role": "kv_consumer"
        },
        {
          "kv_connector": "MooncakeStoreConnector",
          "kv_role": "kv_consumer"
        }
      ]
    }
  }'
```

### 注意

這種模式還需要一個 proxy 來協調 request 到 prefiller / decoder。

官方來源：
- [MooncakeStoreConnector Usage Guide](https://docs.vllm.ai/en/latest/features/mooncake_store_connector_usage/)

## 7. 如果你不想真的共享，還有兩個很好用的替代方案

### 7.1 Prefix-aware routing

這個做法不是共享 store，而是把相同 prefix 的請求送到同一台 instance，提高原本 local cache 的命中率。

適合：

- 多個 replica
- 有重複 system prompt
- 不想先上共享快取層

### 7.2 KV-cache-aware routing

這個做法會把請求送到 **KV 命中率最高** 的 instance。

它的好處是：

- 比 prefix-aware 更聰明
- 就算 prefix 一樣，但某台 cache 已經被 evict，也不會盲目送回原本那台

官方來源：
- [KV Cache Aware Routing](https://docs.vllm.ai/projects/production-stack/en/vllm-stack-0.1.8/use_cases/kv-cache-aware-routing.html)

## 8. 建議的落地順序

如果你是第一次做，建議按這個順序：

1. **單機先開 `Automatic Prefix Caching`**
2. **如果要跨 process，共享 PVC，先試 `OffloadingConnector`**
3. **如果要正式多 replica 共享，再看 `LMCache`**
4. **如果要多節點共享池，再評估 `MooncakeStoreConnector`**
5. **如果你是進階高吞吐架構，再做 `disaggregated prefill/decode`**

這樣比較不會一開始就把系統複雜度拉太高。

## 9. 最後幫你做一句判斷

如果你的目標是：

- **「先驗證能不能跨 instance 重用 KV」**  
  先用 `OffloadingConnector + shared root_dir + PYTHONHASHSEED=0`

- **「Kubernetes 上正式做共享 KV cache」**  
  優先看 `LMCache`

- **「做分散式共享池與進階調度」**  
  看 `MooncakeStoreConnector`

- **「其實只想少算重複 prefix」**  
  先開 `Automatic Prefix Caching`，再配 `KV-cache-aware routing`

## 10. 官方文件

- [Automatic Prefix Caching](https://docs.vllm.ai/en/latest/features/automatic_prefix_caching/)
- [KV Offloading Usage Guide](https://docs.vllm.ai/en/latest/features/kv_offloading_usage/)
- [Sharing KV Cache Across Instances](https://docs.vllm.ai/projects/production-stack/en/latest/use_cases/sharing-kv-cache.html)
- [LMCache Examples](https://docs.vllm.ai/en/latest/examples/disaggregated/lmcache/)
- [MooncakeStoreConnector Usage Guide](https://docs.vllm.ai/en/latest/features/mooncake_store_connector_usage/)
- [KV Cache Aware Routing](https://docs.vllm.ai/projects/production-stack/en/vllm-stack-0.1.8/use_cases/kv-cache-aware-routing.html)
- [KVTransferConfig API](https://docs.vllm.ai/en/stable/api/vllm/config/kv_transfer/)
