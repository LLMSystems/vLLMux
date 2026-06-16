import json
import logging
import time

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from src.llm_router.backend_runtime_state import (decr_inflight, incr_inflight,
                                                  mark_backend_failure,
                                                  mark_backend_success)
from src.llm_router.auth import authenticate
from src.llm_router.backend_selector import select_instance_least_load
from src.llm_router.lora import iter_models, resolve_model
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


def _usage_from_body(body) -> dict | None:
    """Pull an OpenAI `usage` block out of a buffered JSON response body."""
    if body is None:
        return None
    try:
        return (json.loads(body) or {}).get("usage") or None
    except Exception:
        return None


def _scan_sse_for_usage(buffer: bytes, captured: dict) -> bytes:
    """Sniff token usage out of a passing SSE stream.

    vLLM emits a final `data:` chunk carrying `usage` when the request includes
    stream_options.include_usage (which the proxy injects for streaming). We scan
    complete events out of `buffer`, stash the latest non-null usage into
    `captured["usage"]`, and return the unparsed remainder.
    """
    while b"\n\n" in buffer:
        event, buffer = buffer.split(b"\n\n", 1)
        for line in event.split(b"\n"):
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            # Cheap gate: `usage` only appears in the final chunk, so skip the
            # json.loads on every per-token delta chunk (the hot case).
            if b'"usage"' not in line:
                continue
            data = line[len(b"data:"):].strip()
            if not data or data == b"[DONE]":
                continue
            try:
                obj = json.loads(data)
            except Exception:
                continue
            if isinstance(obj, dict) and obj.get("usage"):
                captured["usage"] = obj["usage"]
    return buffer


async def _record_request(app, model_key, instance_id, path, status_code, started,
                          usage=None, error=None, api_key_name=None):
    """Persist one request log row to the shared store. Best-effort, non-blocking
    to the response. `usage` is an OpenAI usage dict (from a buffered body or a
    streamed final chunk) when available."""
    store = getattr(app.state, "store", None)
    if store is None or not model_key:
        return
    latency_ms = (time.perf_counter() - started) * 1000.0
    usage = usage or {}
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    total_tokens = usage.get("total_tokens")
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
            api_key_name=api_key_name,
        )
    except Exception:
        logger.exception("Failed to record request log")


async def _proxy_to_backend(request: Request, upstream_path: str, api_key_name=None) -> Response:
    """Forward an OpenAI-style request to the least-loaded backend instance.

    Shared by /v1/chat/completions and /v1/completions, which differ only in the
    upstream path. Handles model lookup + tag rewrite, load-aware instance
    selection, inflight accounting, backend health marking, transparent failover
    to another instance on a dead/5xx backend, and both streaming (SSE) and
    buffered responses.
    """
    model_key = None
    instance_id = None
    stream_ctx = None
    # Cleanup ownership flags. While True, *this* frame is responsible for closing
    # stream_ctx / decrementing inflight in the finally block. The streaming path
    # hands both off to the response generator and clears them.
    owns_stream = False
    inflight_counted = False
    started = time.perf_counter()
    try:
        config = request.app.state.config
        request_json = await request.json()
        requested = request_json.get("model")
        if not requested:
            raise HTTPException(status_code=400, detail="Missing 'model' field in request body")

        # Resolve the requested name to a routable group. A base group rewrites to
        # its model_tag; a LoRA served name keeps its name (so vLLM selects the
        # adapter) but routes over the base group's instances + metrics.
        resolved = resolve_model(config, requested)
        if not resolved:
            raise HTTPException(status_code=404, detail=f"Model '{requested}' not found.")
        model_key = resolved["route_key"]
        model_cfg = resolved["model_cfg"]
        request_json["model"] = resolved["forward_name"]

        # For streaming, ask the backend to emit a final usage chunk so token
        # counts can be logged (otherwise streamed requests log no tokens).
        if request_json.get("stream"):
            opts = request_json.get("stream_options")
            opts = opts if isinstance(opts, dict) else {}
            opts.setdefault("include_usage", True)
            request_json["stream_options"] = opts

        client = request.app.state.http_client
        instances = model_cfg.get("instances", [])
        # At most one failover hop per extra instance, capped so a bad request
        # storming every backend stays bounded.
        max_attempts = max(1, min(len(instances), 3))
        tried: set[str] = set()
        response = None

        for attempt in range(max_attempts):
            instance = await select_instance_least_load(
                app=request.app, model_key=model_key, model_cfg=model_cfg, exclude=tried,
            )
            instance_id = instance["id"]
            tried.add(instance_id)

            host = instance.get("host", "localhost")
            port = instance["port"]
            target_url = f"http://{host}:{port}{upstream_path}"

            incr_inflight(request.app, model_key, instance_id)
            inflight_counted = True
            try:
                stream_ctx = client.stream("POST", target_url, json=request_json)
                response = await stream_ctx.__aenter__()
                owns_stream = True
            except Exception as e:
                # Transport error before any byte reached the client: safe to
                # fail over to another instance.
                mark_backend_failure(
                    request.app, model_key, instance_id, error=str(e), cooldown_seconds=10.0,
                )
                decr_inflight(request.app, model_key, instance_id)
                inflight_counted = False
                stream_ctx = None
                if attempt < max_attempts - 1:
                    continue
                raise HTTPException(status_code=503, detail="All backends unavailable")

            # A 5xx means the backend failed but sent no usable body yet, so we
            # can still fail over — unless this was our last attempt, in which
            # case we surface the 5xx to the client.
            if response.status_code >= 500 and attempt < max_attempts - 1:
                mark_backend_failure(
                    request.app, model_key, instance_id,
                    error=f"Received status code {response.status_code}",
                    cooldown_seconds=10.0,
                )
                await stream_ctx.__aexit__(None, None, None)
                owns_stream = False
                stream_ctx = None
                decr_inflight(request.app, model_key, instance_id)
                inflight_counted = False
                continue

            break  # got a response we'll serve (2xx/4xx, or final-attempt 5xx)

        if response.status_code < 500:
            mark_backend_success(request.app, model_key, instance_id)
        else:
            mark_backend_failure(
                request.app, model_key, instance_id,
                error=f"Received status code {response.status_code}",
                cooldown_seconds=10.0,
            )

        content_type = response.headers.get("content-type", "")

        if "text/event-stream" in content_type:
            # Hand off cleanup to the generator: it closes stream_ctx and
            # decrements inflight in its own finally, even if the client
            # disconnects mid-stream.
            owns_stream = False
            inflight_counted = False
            captured: dict = {"usage": None}
            served_instance = instance_id

            async def event_stream():
                buffer = b""
                try:
                    async for chunk in response.aiter_raw():
                        yield chunk
                        # Sniff token usage out of the passing stream (best effort).
                        buffer += chunk
                        buffer = _scan_sse_for_usage(buffer, captured)
                except Exception as e:
                    mark_backend_failure(
                        request.app,
                        model_key,
                        served_instance,
                        error=str(e),
                        cooldown_seconds=10.0,
                    )
                    raise
                finally:
                    decr_inflight(request.app, model_key, served_instance)
                    await stream_ctx.__aexit__(None, None, None)
                    await _record_request(
                        request.app, model_key, served_instance, upstream_path,
                        response.status_code, started, usage=captured["usage"],
                        api_key_name=api_key_name,
                    )

            return StreamingResponse(
                event_stream(),
                status_code=response.status_code,
                media_type="text/event-stream",
            )

        content = await response.aread()
        # Log via a background task so the SQLite write happens *after* the
        # response is sent — keeping the DB off the client's critical path.
        return Response(
            content=content,
            status_code=response.status_code,
            media_type=content_type or "application/json",
            background=BackgroundTask(
                _record_request,
                request.app, model_key, instance_id, upstream_path,
                response.status_code, started, _usage_from_body(content),
                api_key_name=api_key_name,
            ),
        )
    except HTTPException:
        raise
    except Exception as e:
        if instance_id is not None and model_key is not None:
            mark_backend_failure(
                request.app,
                model_key,
                instance_id,
                error=str(e),
                cooldown_seconds=10.0,
            )
        await _record_request(
            request.app, model_key, instance_id, upstream_path, None, started, error=str(e),
            api_key_name=api_key_name,
        )
        logger.exception("Unexpected error proxying to %s", upstream_path)
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        # Buffered-success and every error path land here. (The streaming path
        # cleared both flags, so this is a no-op for it.) __aexit__ is only safe
        # once __aenter__ returned, which is exactly what owns_stream tracks.
        if owns_stream and stream_ctx is not None:
            await stream_ctx.__aexit__(None, None, None)
        if inflight_counted:
            decr_inflight(request.app, model_key, instance_id)

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
    key_name = await authenticate(request)
    return await _proxy_to_backend(request, "/v1/chat/completions", api_key_name=key_name)


@router.get("/v1/models")
async def list_models(request: Request):
    config = request.app.state.config
    return JSONResponse(
        content={
            "object": "list",
            "data": iter_models(config),
        }
    )

@router.post("/v1/completions")
async def proxy_completion(request: Request):
    key_name = await authenticate(request)
    return await _proxy_to_backend(request, "/v1/completions", api_key_name=key_name)


@router.post("/v1/embeddings")
async def proxy_embeddings(request: Request):
    key_name = await authenticate(request)
    started = time.perf_counter()
    model_key = None
    try:
        config = request.app.state.config
        embedding_cfg = config.get("embedding_server", {})
        host = embedding_cfg.get("host", "localhost")
        port = embedding_cfg.get("port", 8003)
        target_url = f"http://{host}:{port}/v1/embeddings"

        body = await request.body()
        try:
            model_key = (json.loads(body) or {}).get("model")
        except Exception:
            model_key = None
        headers = dict(request.headers)
        headers.pop("host", None)
        headers.pop("content-length", None)

        client = request.app.state.http_client
        # The shared client has read=None (for long LLM generations); embeddings
        # are fast, so give this path a real bound instead.
        resp = await client.post(
                target_url,
                content=body,
                headers=headers,
                timeout=60.0,
            )

        # Don't forward upstream content-encoding/content-length: httpx may have
        # already decoded the body, so the original headers would no longer match.
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "application/json"),
            # Log after responding (off the client's path) — attributes the
            # request to its API key and captures token usage like the LLM path.
            background=BackgroundTask(
                _record_request, request.app, model_key, None, "/v1/embeddings",
                resp.status_code, started, _usage_from_body(resp.content),
                None, key_name,
            ),
        )

    except httpx.RequestError as e:
        await _record_request(
            request.app, model_key, None, "/v1/embeddings", 503, started,
            error=str(e), api_key_name=key_name,
        )
        raise HTTPException(
            status_code=503, detail=f"Cannot connect to embedding server: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Unexpected error: {str(e)}"
        )