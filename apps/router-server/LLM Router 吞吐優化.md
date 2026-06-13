# LLM Router Server 吞吐優化

1.  **背景與目標**

    > 初始 Router Server 架構簡單，缺乏針對高併發優化，易導致延遲高、吞吐不足。

2.  **瓶頸觀察**

    > -   高併發下延遲飆高
    > -   每次請求重新建立連線
    > -   無法有效利用多核

3.  **開發時程 & 優化紀錄**

    > -   2025/05/15 : 修復流式回應延遲問題以及更新踏坑文檔

        修復 : 首字延遲恢復正常狀態

    > -   2025/05/18 : 共用 AsyncClient：解決 connection overhead

        優化 : 復用 connection pool，大幅降低連線建立成本與延遲，併發 > 10 情境下明顯提升吞吐量與首字延遲，吞吐提升 150% 左右

    > -   2025/05/18 : uvloop：優化 asyncio 排程

        優化 : 優化 : 更快的排程與底層 IO 處理，併發 > 100 情境下並降低 stream 回應時間、縮短首字延遲 30% 左右

    > -   2025/05/18 : gunicorn：啟用多 worker 支援並行處理

## 性能對比

**參數設定**

1. `max_tokens` : 512
2. 對比在不同併發下的吞吐

### LLM Router Server vs VLLM Server 壓測對照表（合併欄位）

| 並發請求數 | 平均請求延遲 (秒)           | 平均首字延遲 (秒)          | 平均回傳 tokens                | 平均 tokens/sec（每請求）      | 總吞吐量 tokens/sec              |
| ---------- | --------------------------- | -------------------------- | ------------------------------ | ------------------------------ | -------------------------------- |
| 1          | Router: 2.79<br>VLLM: 1.20  | Router: 0.07<br>VLLM: 0.20 | Router: 356.00<br>VLLM: 130.00 | Router: 127.53<br>VLLM: 108.12 | Router: 127.53<br>VLLM: 108.12   |
| 5          | Router: 3.16<br>VLLM: 3.20  | Router: 0.53<br>VLLM: 0.23 | Router: 285.80<br>VLLM: 318.80 | Router: 90.37<br>VLLM: 99.59   | Router: 333.11<br>VLLM: 375.47   |
| 10         | Router: 3.69<br>VLLM: 2.74  | Router: 0.71<br>VLLM: 0.30 | Router: 306.00<br>VLLM: 247.30 | Router: 82.90<br>VLLM: 90.41   | Router: 588.39<br>VLLM: 653.53   |
| 20         | Router: 4.18<br>VLLM: 3.34  | Router: 1.30<br>VLLM: 0.40 | Router: 275.80<br>VLLM: 278.35 | Router: 65.99<br>VLLM: 83.46   | Router: 857.46<br>VLLM: 995.27   |
| 50         | Router: 5.96<br>VLLM: 3.44  | Router: 2.65<br>VLLM: 0.47 | Router: 280.04<br>VLLM: 246.82 | Router: 47.00<br>VLLM: 71.85   | Router: 1666.86<br>VLLM: 2263.42 |
| 100        | Router: 10.75<br>VLLM: 5.89 | Router: 5.12<br>VLLM: 0.68 | Router: 288.28<br>VLLM: 268.43 | Router: 26.83<br>VLLM: 45.56   | Router: 2129.36<br>VLLM: 3057.23 |

## 優化一 : 共用 AsyncClient : Router 與子 LLM Server 之間的連線行為

**原始寫法**

```python=
async def proxy():
    async with httpx.AsyncClient(timeout=None) as client:
        await client.post(...)
```

**問題 : 每次都建立新 client，等同於「重建 connection pool + socket」**

1. 導致每個請求都會
    - 建立新的 socket pool
    - 建立 TCP 連線（三次握手）
    - 建立 DNS 查詢
    - TLS 握手（HTTPS）
    - 系統資源會大量消耗，產生嚴重延遲

**共用 AsyncClient 解決的是「後續延遲」，不是第一次( cold start connection overhead )**

**優化寫法**

```python=
app.state.http_client = httpx.AsyncClient(timeout=None)
```

1. `httpx.AsyncClient` 會內部建立連線池
2. 共用 client → 請求之間可以重用已存在的 TCP 連線( Router 與子 LLM Server 之間 )
3. 不用每次都做 TCP/TLS

### 優化後結果

**無優化 vs 優化 http_client**

| 並發請求數 | 平均請求延遲 (秒)                         | 平均首字延遲 (秒)                        | 平均回傳 tokens                                | 平均 tokens/sec（每請求）                      | 總吞吐量 tokens/sec                               |
| ---------- | ----------------------------------------- | ---------------------------------------- | ---------------------------------------------- | ---------------------------------------------- | ------------------------------------------------- |
| 1          | 無優化: 2.79<br>優化: 2.66<br>VLLM: 1.20  | 無優化: 0.07<br>優化: 0.08<br>VLLM: 0.20 | 無優化: 356.00<br>優化: 313.00<br>VLLM: 130.00 | 無優化: 127.53<br>優化: 117.69<br>VLLM: 108.12 | 無優化: 127.53<br>優化: 117.69<br>VLLM: 108.12    |
| 5          | 無優化: 3.16<br>優化: 3.91<br>VLLM: 3.20  | 無優化: 0.53<br>優化: 0.24<br>VLLM: 0.23 | 無優化: 285.80<br>優化: 398.80<br>VLLM: 318.80 | 無優化: 90.37<br>優化: 101.92<br>VLLM: 99.59   | 無優化: 333.11<br>優化: 440.66<br>VLLM: 375.47    |
| 10         | 無優化: 3.69<br>優化: 3.15<br>VLLM: 2.74  | 無優化: 0.71<br>優化: 0.28<br>VLLM: 0.30 | 無優化: 306.00<br>優化: 282.50<br>VLLM: 247.30 | 無優化: 82.90<br>優化: 89.78<br>VLLM: 90.41    | 無優化: 588.39<br>優化: 661.30<br>VLLM: 653.53    |
| 20         | 無優化: 4.18<br>優化: 3.23<br>VLLM: 3.34  | 無優化: 1.30<br>優化: 0.43<br>VLLM: 0.40 | 無優化: 275.80<br>優化: 273.45<br>VLLM: 278.35 | 無優化: 65.99<br>優化: 84.66<br>VLLM: 83.46    | 無優化: 857.46<br>優化: 1117.44<br>VLLM: 995.27   |
| 50         | 無優化: 5.96<br>優化: 3.83<br>VLLM: 3.44  | 無優化: 2.65<br>優化: 0.51<br>VLLM: 0.47 | 無優化: 280.04<br>優化: 280.20<br>VLLM: 246.82 | 無優化: 47.00<br>優化: 73.24<br>VLLM: 71.85    | 無優化: 1666.86<br>優化: 2276.47<br>VLLM: 2263.42 |
| 100        | 無優化: 10.75<br>優化: 6.28<br>VLLM: 5.89 | 無優化: 5.12<br>優化: 0.81<br>VLLM: 0.68 | 無優化: 288.28<br>優化: 284.74<br>VLLM: 268.43 | 無優化: 26.83<br>優化: 45.31<br>VLLM: 45.56    | 無優化: 2129.36<br>優化: 3169.79<br>VLLM: 3057.23 |

---

## 優化二 : 啟用 uvloop

**問題 : Python 的 `asyncio` 預設 event loop 是純 Python 實作，I/O 操作效率不高**

1. 在高併發下，事件排程的延遲會上升
2. 因為 Router server 主要要處理大量 streaming response
    - FastAPI 透過 `async` 處理請求
    - 如果異步本身排程差，會直接拖慢異步請求的首字延遲與 throughput

**解決方案 : 使用 `uvloop` ，一個使用 Cython + C 實作的事件排程器**

### 優化後結果

無優化 vs http_client 優化 vs http_client + uvloop）

| 並發請求數 | 平均請求延遲 (秒)                                         | 平均首字延遲 (秒)                                        | 平均回傳 tokens                                                  | 平均 tokens/sec（每請求）                                        | 總吞吐量 tokens/sec                                                  |
| ---------- | --------------------------------------------------------- | -------------------------------------------------------- | ---------------------------------------------------------------- | ---------------------------------------------------------------- | -------------------------------------------------------------------- |
| 1          | 無優化: 2.79<br>http: 2.66<br>VLLM: 1.20<br>uvloop: 1.83  | 無優化: 0.07<br>http: 0.08<br>VLLM: 0.20<br>uvloop: 0.02 | 無優化: 356.00<br>http: 313.00<br>VLLM: 130.00<br>uvloop: 228.00 | 無優化: 127.53<br>http: 117.69<br>VLLM: 108.12<br>uvloop: 124.91 | 無優化: 127.53<br>http: 117.69<br>VLLM: 108.12<br>uvloop: 124.91     |
| 5          | 無優化: 3.16<br>http: 3.91<br>VLLM: 3.20<br>uvloop: 2.37  | 無優化: 0.53<br>http: 0.24<br>VLLM: 0.23<br>uvloop: 0.26 | 無優化: 285.80<br>http: 398.80<br>VLLM: 318.80<br>uvloop: 221.60 | 無優化: 90.37<br>http: 101.92<br>VLLM: 99.59<br>uvloop: 93.37    | 無優化: 333.11<br>http: 440.66<br>VLLM: 375.47<br>uvloop: 328.98     |
| 10         | 無優化: 3.69<br>http: 3.15<br>VLLM: 2.74<br>uvloop: 2.72  | 無優化: 0.71<br>http: 0.28<br>VLLM: 0.30<br>uvloop: 0.07 | 無優化: 306.00<br>http: 282.50<br>VLLM: 247.30<br>uvloop: 263.30 | 無優化: 82.90<br>http: 89.78<br>VLLM: 90.41<br>uvloop: 96.81     | 無優化: 588.39<br>http: 661.30<br>VLLM: 653.53<br>uvloop: 693.92     |
| 20         | 無優化: 4.18<br>http: 3.23<br>VLLM: 3.34<br>uvloop: 3.15  | 無優化: 1.30<br>http: 0.43<br>VLLM: 0.40<br>uvloop: 0.33 | 無優化: 275.80<br>http: 273.45<br>VLLM: 278.35<br>uvloop: 268.45 | 無優化: 65.99<br>http: 84.66<br>VLLM: 83.46<br>uvloop: 85.23     | 無優化: 857.46<br>http: 1117.44<br>VLLM: 995.27<br>uvloop: 999.42    |
| 50         | 無優化: 5.96<br>http: 3.83<br>VLLM: 3.44<br>uvloop: 4.09  | 無優化: 2.65<br>http: 0.51<br>VLLM: 0.47<br>uvloop: 0.46 | 無優化: 280.04<br>http: 280.20<br>VLLM: 246.82<br>uvloop: 295.22 | 無優化: 47.00<br>http: 73.24<br>VLLM: 71.85<br>uvloop: 72.24     | 無優化: 1666.86<br>http: 2276.47<br>VLLM: 2263.42<br>uvloop: 2352.19 |
| 100        | 無優化: 10.75<br>http: 6.28<br>VLLM: 5.89<br>uvloop: 5.94 | 無優化: 5.12<br>http: 0.81<br>VLLM: 0.68<br>uvloop: 0.58 | 無優化: 288.28<br>http: 284.74<br>VLLM: 268.43<br>uvloop: 276.30 | 無優化: 26.83<br>http: 45.31<br>VLLM: 45.56<br>uvloop: 46.55     | 無優化: 2129.36<br>http: 3169.79<br>VLLM: 3057.23<br>uvloop: 3171.11 |

## 優化三 : 使用 Gunicorn 多 worker

**問題 : 單一 FastAPI `uvicorn` 實例只有一個事件循環（單核）**
原始寫法

```bash=
uvicorn main:app --host 0.0.0.0 --port 8947
```

這樣的話:

-   一個進程
-   一個 `asyncio` event loop
-   單核 CPU 處理所有請求
-   高併發下切 context 效率低

**解決方法 : `main.py` 中寫的 `app = create_app(...)`，每個 worker 都會各自執行一次**

```bash=
gunicorn main:app \
  -k uvicorn.workers.UvicornWorker \ # 每個 worker 是一個 async event loop
  -w 4 \ # 4 個 worker
  -c gunicorn.conf.py
```

原則上，有以下優點:

-   每個 worker 都在自己 CPU 上做 async 處理
-   多核心、進程隔離、可崩潰容錯

### Gunicorn 多 worker 原理

**Gunicorn 採用的是「pre-fork 多進程架構」：**

```plaintext=
Parent（gunicorn master）
   ├─ Worker 1 → 初始化 app → 執行 lifespan (httpx.AsyncClient())
   ├─ Worker 2 → 初始化 app → 執行 lifespan (httpx.AsyncClient())
   ├─ Worker 3 → 初始化 app → 執行 lifespan (httpx.AsyncClient())
   └─ Worker 4 → 初始化 app → 執行 lifespan (httpx.AsyncClient())
```

**Track Process**
假設`worker=4`，會有:

| 類型          | 數量 | 說明                                       |
| ------------- | ---- | :----------------------------------------- |
| Master 主進程 | 1    | 負責 fork worker、管理生死、監控           |
| Worker 子進程 | 4    | 實際處理 HTTP 請求（各跑一份 FastAPI app） |
| 總共          | 5    |                                            |

```plaintext=
Gunicorn Master Process (PID=1000)
│
├── Worker 1 (PID=1001)  → FastAPI + uvicorn worker
├── Worker 2 (PID=1002)  → FastAPI + uvicorn worker
├── Worker 3 (PID=1003)  → FastAPI + uvicorn worker
└── Worker 4 (PID=1004)  → FastAPI + uvicorn worker
```

### 優化後結果

**無優化 vs http_client 優化 vs uvloop + http_client vs uvloop + http_client + gunicorn）**

| 並發請求數 | 平均請求延遲 (秒)                                                           | 平均首字延遲 (秒)                                                          | 平均回傳 tokens                                                                      | 平均 tokens/sec（每請求）                                                            | 總吞吐量 tokens/sec                                                                       |
| ---------- | --------------------------------------------------------------------------- | -------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------- |
| 1          | 無優化: 2.79<br>http: 2.66<br>VLLM: 1.20<br>uvloop: 1.83<br>gunicorn: 1.67  | 無優化: 0.07<br>http: 0.08<br>VLLM: 0.20<br>uvloop: 0.02<br>gunicorn: 0.02 | 無優化: 356.00<br>http: 313.00<br>VLLM: 130.00<br>uvloop: 228.00<br>gunicorn: 212.00 | 無優化: 127.53<br>http: 117.69<br>VLLM: 108.12<br>uvloop: 124.91<br>gunicorn: 126.76 | 無優化: 127.53<br>http: 117.69<br>VLLM: 108.12<br>uvloop: 124.91<br>gunicorn: 126.76      |
| 5          | 無優化: 3.16<br>http: 3.91<br>VLLM: 3.20<br>uvloop: 2.37<br>gunicorn: 2.64  | 無優化: 0.53<br>http: 0.24<br>VLLM: 0.23<br>uvloop: 0.26<br>gunicorn: 0.10 | 無優化: 285.80<br>http: 398.80<br>VLLM: 318.80<br>uvloop: 221.60<br>gunicorn: 259.00 | 無優化: 90.37<br>http: 101.92<br>VLLM: 99.59<br>uvloop: 93.37<br>gunicorn: 97.98     | 無優化: 333.11<br>http: 440.66<br>VLLM: 375.47<br>uvloop: 328.98<br>gunicorn: 341.60      |
| 10         | 無優化: 3.69<br>http: 3.15<br>VLLM: 2.74<br>uvloop: 2.72<br>gunicorn: 3.22  | 無優化: 0.71<br>http: 0.28<br>VLLM: 0.30<br>uvloop: 0.07<br>gunicorn: 0.07 | 無優化: 306.00<br>http: 282.50<br>VLLM: 247.30<br>uvloop: 263.30<br>gunicorn: 323.50 | 無優化: 82.90<br>http: 89.78<br>VLLM: 90.41<br>uvloop: 96.81<br>gunicorn: 100.54     | 無優化: 588.39<br>http: 661.30<br>VLLM: 653.53<br>uvloop: 693.92<br>gunicorn: 654.16      |
| 20         | 無優化: 4.18<br>http: 3.23<br>VLLM: 3.34<br>uvloop: 3.15<br>gunicorn: 3.74  | 無優化: 1.30<br>http: 0.43<br>VLLM: 0.40<br>uvloop: 0.33<br>gunicorn: 0.39 | 無優化: 275.80<br>http: 273.45<br>VLLM: 278.35<br>uvloop: 268.45<br>gunicorn: 301.30 | 無優化: 65.99<br>http: 84.66<br>VLLM: 83.46<br>uvloop: 85.23<br>gunicorn: 80.48      | 無優化: 857.46<br>http: 1117.44<br>VLLM: 995.27<br>uvloop: 999.42<br>gunicorn: 1120.63    |
| 50         | 無優化: 5.96<br>http: 3.83<br>VLLM: 3.44<br>uvloop: 4.09<br>gunicorn: 4.01  | 無優化: 2.65<br>http: 0.51<br>VLLM: 0.47<br>uvloop: 0.46<br>gunicorn: 0.49 | 無優化: 280.04<br>http: 280.20<br>VLLM: 246.82<br>uvloop: 295.22<br>gunicorn: 288.96 | 無優化: 47.00<br>http: 73.24<br>VLLM: 71.85<br>uvloop: 72.24<br>gunicorn: 72.13      | 無優化: 1666.86<br>http: 2276.47<br>VLLM: 2263.42<br>uvloop: 2352.19<br>gunicorn: 2281.93 |
| 100        | 無優化: 10.75<br>http: 6.28<br>VLLM: 5.89<br>uvloop: 5.94<br>gunicorn: 6.27 | 無優化: 5.12<br>http: 0.81<br>VLLM: 0.68<br>uvloop: 0.58<br>gunicorn: 0.57 | 無優化: 288.28<br>http: 284.74<br>VLLM: 268.43<br>uvloop: 276.30<br>gunicorn: 286.34 | 無優化: 26.83<br>http: 45.31<br>VLLM: 45.56<br>uvloop: 46.55<br>gunicorn: 45.67      | 無優化: 2129.36<br>http: 3169.79<br>VLLM: 3057.23<br>uvloop: 3171.11<br>gunicorn: 3219.35 |
