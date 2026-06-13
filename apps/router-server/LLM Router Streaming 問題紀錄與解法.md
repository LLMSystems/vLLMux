# LLM Router Streaming 問題紀錄與解法

**設計 LLM Router Server 遇到的流式回應延遲問題，紀錄分析，供參考**

## 問題背景

寫完 LLM Router Server 在測試流式輸出的時候發現路由轉發的時候，子進程 Server 可以正常流式輸出，但是 Router Server 卻出現異常，如下:

-   首字延遲極高
-   首字之後，Token 之間幾乎無時間差
-   看起來沒有正常流式返回

---

**子進程 Server 數據紀錄**

-   總共收到 108 個 token
-   總時間: 1.31 秒
-   首字延遲: 0.17 秒
-   平均 token 間隔: 0.011 秒

**Router Server 數據紀錄**

-   總共收到 108 個 token
-   總時間: 1.12 秒
-   首字延遲: 1.10 秒
-   平均 token 間隔: 0.000 秒

---

## 經排查後定位問題

```python=
upstream_response = await client.post(target_url, json=request_json, timeout=None)

async def stream_generator(upstream_response):
    async for chunk in upstream_response.aiter_text():
        yield chunk
```

問題 :

-   雖然寫法是 await，且用 `stream_generator`迭代，但是其實`upstream_response`是對子進程 Server 返回的所有數據進行 chunk 處理，所以雖然客戶端會流式處理，但是其實沒有實時流式處理
-   `client.post(...)`會等資料讀完之後才會進入`StreamingResponse`，應該要改用`client.stream("POST", ...)`，僅建立連線，後續進`StreamingResponse`真正流式返回

## 修改後 (正常流式返回)

```python=
client = httpx.AsyncClient(timeout=None)
stream_ctx = client.stream("POST", target_url, json=request_json)
response = await stream_ctx.__aenter__()

content_type = response.headers.get("content-type", "")
if "text/event-stream" in content_type:
    async def event_stream():
        try:
            async for chunk in response.aiter_raw():
                yield chunk
        finally:
            await stream_ctx.__aexit__(None, None, None)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

## 思考

**現在我 Server 的角色是作為中繼站，所以在流式的情況下，下游給我`generator`，我需要一點一點的把`generator`返回給客戶**

-   如果用 `async with`，當我執行`return StreamingResponse(...)`的時候，會馬上結束我跟下游的連線，我就沒辦法建立客戶-中繼站-下游的`generator`
-   所以要手動控制連線，要等到`StreamingResponse`呼叫完`event_stream()`之後，才可以關掉連線
    -   你先進入 stream 的 context，然後把 `response.aiter_raw()` 包成 generator
    -   給 FastAPI 的 `StreamingResponse()` 回傳
    -   執行 `async for` 時，資料才真正傳輸，此時 stream 還在
    -   在 generator 的 `finally` 區塊裡 `__aexit__()`，安全關閉

```python=
client = httpx.AsyncClient()
stream_ctx = client.stream("POST", url, json=data)
response = await stream_ctx.__aenter__()  # 手動進入

async def event_stream():
    try:
        async for chunk in response.aiter_raw():
            yield chunk
    finally:
        await stream_ctx.__aexit__(None, None, None)  # 手動退出

return StreamingResponse(event_stream(), media_type="text/event-stream")
```
