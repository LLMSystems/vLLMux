import asyncio
import json
import time

import httpx

NUM_REQUESTS = 10
MAX_TOKENS = 512
TARGET_URL = "http://0.0.0.0:8947/v1/chat/completions"
MODEL_NAME = "Qwen2.5-1.5B-Instruct"

HEADERS = {"Content-Type": "application/json"}

async def send_request(client):
    payload = {
        "model": MODEL_NAME,
        "messages": [{"role": "user", "content": "介紹一下太陽系"}],
        "temperature": 0.7,
        "max_tokens": MAX_TOKENS,
        "stream": True
    }

    start_time = time.time()
    first_token_time = None
    token_count = 0

    try:
        async with client.stream("POST", TARGET_URL, headers=HEADERS, json=payload) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data_str = line[len("data: "):]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        delta = data["choices"][0].get("delta", {})
                        if "content" in delta:
                            token_count += 1
                            if first_token_time is None:
                                first_token_time = time.time()
                    except json.JSONDecodeError:
                        continue

        end_time = time.time()
        latency = end_time - start_time
        first_latency = first_token_time - start_time if first_token_time else None
        return latency, token_count, first_latency, True

    except Exception:
        return 0, 0, None, False
    
async def run_concurrent_requests():
    async with httpx.AsyncClient(timeout=None) as client:
        tasks = [send_request(client) for _ in range(NUM_REQUESTS)]
        results = await asyncio.gather(*tasks)
    
    latencies = [result[0] for result in results if result[3]]
    token_counts = [result[1] for result in results if result[3]]
    first_latencies = [result[2] for result in results if result[3]]
    
    if latencies:
        total_tokens = sum(token_counts)
        total_time = max(latencies)  # 批次總耗時
        avg_latency = sum(latencies) / len(latencies)
        avg_tokens = total_tokens / len(token_counts)
        avg_token_per_sec = total_tokens / sum(latencies)
        total_token_throughput = total_tokens / total_time

        print(f"\n並發請求數: {NUM_REQUESTS}")
        print(f"成功數: {len(latencies)}")
        print(f"平均請求延遲: {avg_latency:.2f} 秒")
        print(f"平均首字延遲: {sum(first_latencies) / len(first_latencies):.2f} 秒")
        print(f"平均回傳 tokens: {avg_tokens:.2f}")
        print(f"總共生成 {total_tokens} 個 token")
        print(f"平均 tokens/sec（每請求）: {avg_token_per_sec:.2f}")
        print(f"總吞吐量（整批 max latency）: {total_token_throughput:.2f} tokens/sec")
    else:
        print("所有請求都失敗了")
        
if __name__ == "__main__":
    asyncio.run(run_concurrent_requests())