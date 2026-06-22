# vLLM 模型 Serving 負載平衡整理

更新日期：2026-06-20

## 範圍

本文只整理 **模型 serving 相關** 的 vLLM 負載平衡與路由方式，不包含一般 Web/API 集群常見的 round robin、least connections 等通用型負載平衡觀念，除非它們在 vLLM 官方部署方式中有直接出現。

另外要先區分三件事：

1. **Request routing / load balancing**
把一個推理請求送到哪一個 vLLM instance。
2. **Deployment topology**
多個 vLLM engine、API server、節點之間如何分工。
3. **Model-internal balancing**
像 MoE expert 的內部負載均衡，這不是入口流量分配，但仍屬模型 serving 的效能優化。

## 一覽表

| 類別 | 方法 | vLLM 層級 | 核心目的 | 最適合的情境 |
|---|---|---|---|---|
| Request routing | Round-robin | Production Stack Router | 平均分配請求 | 同質 GPU、無明顯快取重用 |
| Request routing | Session-based routing | Production Stack Router | 讓同一 session 盡量回同一台 | 多輪對話、會話狀態重用 |
| Request routing | Prefix-aware routing | Production Stack Router | 提高 prefix cache / KV cache 命中率 | 共用 system prompt、模板化 prompt |
| Request routing | KV cache aware routing | Production Stack Router | 把請求送去快取命中機率最高的 replica | 多輪聊天、重複上下文 |
| Request routing / architecture | Disaggregated-prefill routing | Production Stack Router | 拆開 prefill 與 decode 階段 | 長上下文、高吞吐場景 |
| Deployment topology | Internal load balancing | vLLM Core data parallel deployment | 在單個 API server 內依 engine queue 分流 | 同機或同節點多 engine |
| Deployment topology | Hybrid load balancing | vLLM Core data parallel deployment | 先做本地分流，再交給上游 LB | 多節點、想兼顧 locality |
| Deployment topology | External load balancing | vLLM Core data parallel deployment | 交給外部 LB/ingress/router | 已有既有平台或 service mesh |
| Model-internal balancing | EPLB (Expert Parallel Load Balancer) | MoE / Expert Parallel | 降低 expert 負載傾斜 | MoE 模型 serving |

## 1. Production Stack 的模型路由策略

這一層最接近一般所說的「vLLM 負載平衡」。

### 1.1 Round-robin

**做法**
依序把請求輪流送到不同的 vLLM server。

**優點**
- 最簡單，容易理解與除錯。
- 不需要額外蒐集 cache 狀態或 session 資訊。
- 在所有 replica 規格相近、流量型態接近時，效果通常可接受。

**缺點**
- 完全不知道 prompt 長短差異。
- 不會利用 prefix cache 或 KV cache。
- 多輪對話可能反覆打到不同 instance，讓 cache 命中率下降。

**適用情境**
- 模型請求彼此差異不大。
- 重點是快速上線，而不是極致效能。
- 前期壓測或功能驗證。

### 1.2 Session-based routing

**做法**
同一個 session 的請求盡量送到同一台 server。官方文件也提到，當請求沒有 session id 時，會選擇 `lowest qps` 的 server。

**優點**
- 多輪對話通常有更好的快取重用效果。
- 對 conversational AI 很直覺。
- 比單純 round-robin 更符合聊天產品的流量特性。

**缺點**
- 大客戶或熱門 session 可能變成熱點。
- session 分布不均時，整體負載可能不平均。
- 需要應用層正確傳 session id。

**適用情境**
- Chatbot。
- Agent 對話。
- 任何明顯以「會話」為單位的互動型應用。

### 1.3 Prefix-aware routing

**做法**
把具有相同或高度相似 prompt prefix 的請求送到相同 instance，以提升 prefix cache 命中率。

**優點**
- 對共用 system prompt、RAG 模板、few-shot 模板特別有效。
- 可以降低 prefill 成本。
- 在高重複前綴工作負載中，通常比 round-robin 更有效率。

**缺點**
- 如果 prefix 分布很偏，容易形成特定 replica 熱點。
- 如果請求之間幾乎沒有共享 prefix，收益有限。
- 需要 router 能識別 prefix 關聯。

**適用情境**
- 大量固定 system prompt。
- 統一模板式企業問答。
- Prompt 結構高度規範化的服務。

### 1.4 KV cache aware routing

**做法**
根據各個 instance 的 KV cache 狀態，把請求送去最可能有 cache 命中的地方。

**優點**
- 比 prefix-aware 更精細，因為它看的是實際快取狀態，不只是 prefix 相似度。
- 對長對話和重複上下文更有幫助。
- 有機會明顯降低延遲與 GPU 重算成本。

**缺點**
- 實作與觀測成本更高。
- 需要 router 能拿到各 instance 的 cache 資訊。
- 如果 cache 狀態更新不即時，判斷品質會下降。

**適用情境**
- 多輪聊天。
- 需要高 cache reuse 的企業內部助理。
- 相同上下文頻繁重訪的工作負載。

### 1.5 Disaggregated-prefill routing

**做法**
將 `prefill` 與 `decode` 階段拆開，由不同資源池處理。Router 不只是分流請求，而是要協調請求進入哪個 prefill worker、哪個 decode worker。

**優點**
- 能針對 prefill 與 decode 使用不同最佳化策略。
- 長上下文場景通常更有機會提升整體吞吐。
- 有助於更細緻地利用不同 GPU 資源。

**缺點**
- 系統設計更複雜。
- 需要處理跨階段的資料傳遞與協調。
- 如果流量特徵不適合，額外開銷可能吃掉收益。

**適用情境**
- 長 prompt。
- 大規模 LLM 服務。
- 吞吐量導向的線上推理平台。

## 2. vLLM Core 的部署層負載平衡

這些方法比較像「多 engine / 多節點部署方式」，不是單一 routing algorithm，但在實際 serving 中很重要。

### 2.1 Internal load balancing

**做法**
在單個 API server 內部，依各個 engine 的執行或等待狀態來分配請求。

**優點**
- 不必完全依賴外部負載平衡器。
- 對單節點或單入口多 engine 架構很直接。
- 可以利用內部對 engine queue 的可見性做更合理分流。

**缺點**
- 主要解決的是單入口內部的分配。
- 跨節點全域最佳化能力有限。

**適用情境**
- 同一節點多個 data parallel engine。
- 想降低外部 LB 複雜度。

### 2.2 Hybrid load balancing

**做法**
每個節點先在本地對本地 engine 做內部分流，再由上層負載平衡器把流量導向不同節點。

**優點**
- 同時兼顧本地 queue 可見性與多節點擴展。
- 在 locality 與全域分流之間取得平衡。

**缺點**
- 觀測與調校比 internal/external 單獨使用更複雜。
- 若上層與下層策略不協調，效果可能不理想。

**適用情境**
- 多節點 vLLM 叢集。
- 想兼顧節點內效率與叢集擴展性。

### 2.3 External load balancing

**做法**
把每個 vLLM server 或 data parallel rank 暴露給外部負載平衡器，由 Nginx、ingress、service mesh 或自訂 router 決定分流。

**優點**
- 容易接到既有雲平台與基礎設施。
- 方便統一治理 TLS、權限、觀測、限流。
- 與其他服務平台整合成本較低。

**缺點**
- 外部 LB 通常不了解模型內部快取與 token 成本。
- 若只用通用型演算法，對 LLM 不一定最優。

**適用情境**
- 已有成熟平台團隊。
- Kubernetes / service mesh / ingress-based 架構。

## 3. 模型內部的負載平衡：EPLB

### 3.1 EPLB (Expert Parallel Load Balancer)

**做法**
在 MoE 模型的 expert parallel serving 中，重新調整 expert mapping，避免少數 expert 過熱造成某些 GPU 或 worker 壓力過高。

**優點**
- 直接處理 MoE 的核心瓶頸之一。
- 有助於降低 expert 負載不均。
- 能改善 MoE serving 的穩定性與資源利用率。

**缺點**
- 只適用於 MoE / expert parallel 場景。
- 不是入口 request routing，不能取代前面的 router。

**適用情境**
- Mixtral、DeepSeek-MoE 等 MoE 模型部署。

## 4. 實務上的選擇建議

### 如果你是做聊天型應用

優先順序通常是：

1. `Session-based routing`
2. `KV cache aware routing`
3. `Prefix-aware routing`

原因是聊天服務往往最依賴上下文重用，而不是單純的平均分流。

### 如果你是做模板化企業問答

優先考慮：

1. `Prefix-aware routing`
2. `KV cache aware routing`

因為這類服務常有固定 system prompt、固定 RAG 包裝格式，prefix 重複率高。

### 如果你是做高吞吐、長上下文服務

優先考慮：

1. `Disaggregated-prefill routing`
2. `KV cache aware routing`
3. `Hybrid load balancing`

這樣比較能把 prefill 與 decode 的資源特性分開處理。

### 如果你只是先把服務跑起來

先從：

1. `Round-robin`
2. `External load balancing`

開始最省事，之後再根據延遲、吞吐、cache hit rate 升級。

## 5. 一句話總結

如果只看 vLLM 模型 serving：

- **最基本** 的做法是 `round-robin`。
- **聊天服務最常見的優化方向** 是 `session-based` 與 `KV cache aware`。
- **有共用 prompt 模板時**，`prefix-aware` 往往很值得。
- **長上下文與高吞吐平台**，要特別看 `disaggregated-prefill`。
- **MoE 模型** 則另外有 `EPLB` 處理 expert 層級的負載不均。

## 6. 官方來源

- vLLM Data Parallel Deployment  
  https://docs.vllm.ai/en/stable/serving/data_parallel_deployment/
- vLLM Nginx Deployment Example  
  https://docs.vllm.ai/en/stable/deployment/nginx/
- vLLM Production Stack Router Logic API  
  https://docs.vllm.ai/projects/production-stack/en/vllm-stack-0.1.2/dev_guide/dev_api/router-logic.html
- vLLM Production Stack Router JSON Config  
  https://docs.vllm.ai/projects/production-stack/en/vllm-stack-0.1.2/user_manual/router/json.html
- vLLM Prefix-aware Routing  
  https://docs.vllm.ai/projects/production-stack/en/latest/use_cases/prefix-aware-routing.html
- vLLM KV Cache Aware Routing  
  https://docs.vllm.ai/projects/production-stack/en/vllm-stack-0.1.8/use_cases/kv-cache-aware-routing.html
- vLLM Disaggregated Prefill  
  https://docs.vllm.ai/projects/production-stack/en/latest/use_cases/disaggregated-prefill.html
- vLLM Semantic Router Integration  
  https://docs.vllm.ai/projects/production-stack/en/latest/use_cases/semantic-router-integration.html
- vLLM Expert Parallel Deployment / EPLB  
  https://docs.vllm.ai/en/latest/serving/expert_parallel_deployment/
