import json
import logging
import time

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from src.llm_router.backend_runtime_state import (decr_inflight, incr_inflight,
                                                  mark_backend_failure,
                                                  mark_backend_success)
from src.llm_router.backend_selector import select_instance_least_load
from src.llm_router.overlay import load_config_with_overlay

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/reload")
async def reload_config(request: Request):
    """Re-read config.yaml + the dynamic-model overlay so newly-added models
    become routable without a restart. Routing/metrics read app.state.config
    live, so swapping it is enough."""
    path = getattr(request.app.state, "config_path", None)
    if not path:
        raise HTTPException(status_code=500, detail="config_path not set on app.state")
    request.app.state.config = load_config_with_overlay(path)
    groups = list(request.app.state.config.get("LLM_engines", {}).keys())
    logger.info("Config reloaded via /reload: %d groups", len(groups))
    return {"status": "reloaded", "groups": groups}


async def _record_request(app, model_key, instance_id, path, status_code, started,
                          body=None, error=None):
    """Persist one request log row to the shared store. Best-effort, non-blocking
    to the response. Tokens are parsed from a buffered JSON `usage` block when
    present (absent for streaming responses)."""
    store = getattr(app.state, "store", None)
    if store is None or not model_key:
        return
    latency_ms = (time.perf_counter() - started) * 1000.0
    prompt_tokens = completion_tokens = total_tokens = None
    if body is not None:
        try:
            usage = (json.loads(body) or {}).get("usage") or {}
            prompt_tokens = usage.get("prompt_tokens")
            completion_tokens = usage.get("completion_tokens")
            total_tokens = usage.get("total_tokens")
        except Exception:
            pass
    try:
        await store.record_request(
            model_key=model_key,
            path=path,
            status_code=status_code,
            latency_ms=latency_ms,
            instance_id=instance_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            error=error,
        )
    except Exception:
        logger.exception("Failed to record request log")


async def _proxy_to_backend(request: Request, upstream_path: str) -> Response:
    """Forward an OpenAI-style request to the least-loaded backend instance.

    Shared by /v1/chat/completions and /v1/completions, which differ only in the
    upstream path. Handles model lookup + tag rewrite, load-aware instance
    selection, inflight accounting, backend health marking, and both streaming
    (SSE) and buffered responses.
    """
    instance = None
    model_key = None
    instance_id = None
    stream_ctx = None
    started = time.perf_counter()
    try:
        config = request.app.state.config
        request_json = await request.json()
        model_key = request_json.get("model")
        if not model_key:
            raise HTTPException(status_code=400, detail="Missing 'model' field in request body")

        model_cfg = config.get("LLM_engines", {}).get(model_key)
        if not model_cfg:
            raise HTTPException(status_code=404, detail=f"Model '{model_key}' not found.")

        # The client addresses the engine by its group key; the backend vLLM is
        # served under its model_tag, so rewrite before forwarding.
        request_json["model"] = model_cfg["model_config"]["model_tag"]

        instance = await select_instance_least_load(
            app=request.app, model_key=model_key, model_cfg=model_cfg
        )
        instance_id = instance["id"]
        incr_inflight(request.app, model_key, instance_id)

        host = instance.get("host", "localhost")
        port = instance["port"]
        target_url = f"http://{host}:{port}{upstream_path}"

        client = request.app.state.http_client
        stream_ctx = client.stream("POST", target_url, json=request_json)
        response = await stream_ctx.__aenter__()

        if response.status_code < 500:
            mark_backend_success(request.app, model_key, instance_id)
        else:
            mark_backend_failure(
                request.app,
                model_key,
                instance_id,
                error=f"Received status code {response.status_code}",
                cooldown_seconds=10.0,
            )

        content_type = response.headers.get("content-type", "")

        if "text/event-stream" in content_type:
            async def event_stream():
                try:
                    async for chunk in response.aiter_raw():
                        yield chunk
                except Exception as e:
                    mark_backend_failure(
                        request.app,
                        model_key,
                        instance_id,
                        error=str(e),
                        cooldown_seconds=10.0,
                    )
                    raise
                finally:
                    decr_inflight(request.app, model_key, instance_id)
                    await stream_ctx.__aexit__(None, None, None)
                    await _record_request(
                        request.app, model_key, instance_id, upstream_path,
                        response.status_code, started,
                    )

            return StreamingResponse(
                event_stream(),
                status_code=response.status_code,
                media_type="text/event-stream",
            )

        content = await response.aread()
        await stream_ctx.__aexit__(None, None, None)
        decr_inflight(request.app, model_key, instance_id)
        await _record_request(
            request.app, model_key, instance_id, upstream_path,
            response.status_code, started, body=content,
        )
        return Response(
            content=content,
            status_code=response.status_code,
            media_type=content_type or "application/json",
        )
    except HTTPException:
        raise
    except Exception as e:
        if instance is not None and model_key is not None:
            mark_backend_failure(
                request.app,
                model_key,
                instance["id"],
                error=str(e),
                cooldown_seconds=10.0,
            )
            decr_inflight(request.app, model_key, instance["id"])
        await _record_request(
            request.app, model_key, instance_id, upstream_path, None, started, error=str(e)
        )
        logger.exception("Unexpected error proxying to %s", upstream_path)
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/metrics")
async def get_metrics(request: Request):
    metrics_dict = {
        model: {
            name: metric.to_dict()
            for name, metric in instances.items()
        }
        for model, instances in request.app.state.metrics_cache.items()
    }
    return JSONResponse(content=metrics_dict)

@router.post("/v1/chat/completions")
async def proxy_chat_completion(request: Request):
    return await _proxy_to_backend(request, "/v1/chat/completions")


@router.get("/v1/models")
async def list_models(request: Request):
    config = request.app.state.config
    engines = config.get("LLM_engines", {})

    model_list = [
        {"id": model_key, "object": "model"}
        for model_key in engines.keys()
    ]

    return JSONResponse(
        content={
            "object": "list",
            "data": model_list
        }
    )

@router.post("/v1/completions")
async def proxy_completion(request: Request):
    return await _proxy_to_backend(request, "/v1/completions")


@router.post("/v1/embeddings")
async def proxy_embeddings(request: Request):
    try:
        config = request.app.state.config
        embedding_cfg = config.get("embedding_server", {})
        host = embedding_cfg.get("host", "localhost")
        port = embedding_cfg.get("port", 8003)
        target_url = f"http://{host}:{port}/v1/embeddings"

        body = await request.body()
        headers = dict(request.headers)
        headers.pop("host", None)
        headers.pop("content-length", None)
        
        client = request.app.state.http_client
        resp = await client.post(
                target_url,
                content=body,
                headers=headers
            )

        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=dict(resp.headers)
        )

    except httpx.RequestError as e:
        raise HTTPException(
            status_code=503, detail=f"Cannot connect to embedding server: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Unexpected error: {str(e)}"
        )