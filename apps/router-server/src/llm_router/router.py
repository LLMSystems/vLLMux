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
from src.llm_router.routing_strategies import (DEFAULT_STRATEGY, STRATEGIES,
                                               select_instance)
from src.llm_router.lora import build_route_chain, iter_models, resolve_model
from src.llm_router.overlay import load_config_with_overlay

logger = logging.getLogger(__name__)

router = APIRouter()

# Paths only a chat (kind=="chat") group can serve — used to 404 a pooling group
# early. /tokenize + /detokenize are deliberately excluded: every model has a
# tokenizer regardless of kind.
_CHAT_ONLY_PATHS = (
    "/v1/chat/completions",
    "/v1/completions",
    "/v1/messages",
    "/v1/messages/count_tokens",
)
# Paths where we inject stream_options.include_usage (an OpenAI-only knob).
_OPENAI_STREAM_PATHS = ("/v1/chat/completions", "/v1/completions")


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


@router.get("/health")
async def health():
    """Liveness probe: the process is up. No auth, no dependencies — for k8s
    `livenessProbe` / load-balancer health checks. Restart the pod only if this
    stops answering."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request):
    """Readiness probe: the router has a loaded config and finished startup, so it
    can resolve + route. Returns 503 until then so a load balancer holds traffic
    during boot or a reload window (no auth — for k8s `readinessProbe`)."""
    state = request.app.state
    config = getattr(state, "config", None)
    started = hasattr(state, "http_client")  # set in lifespan once startup is done
    if not config or not started:
        return JSONResponse(
            status_code=503,
            content={"status": "not ready",
                     "reason": "config not loaded" if not config else "starting"},
        )
    return {"status": "ready", "groups": len(config.get("LLM_engines", {}))}


@router.get("/routing")
async def get_routing(request: Request):
    """Current global routing strategy + the selectable catalogue.

    Note: a per-group `model_config.routing_strategy` still overrides this global
    value for that group; this endpoint reads/sets the global default only."""
    return {
        "strategy": getattr(request.app.state, "routing_strategy", DEFAULT_STRATEGY),
        "available": sorted(STRATEGIES),
        "default": DEFAULT_STRATEGY,
    }


@router.post("/routing")
async def set_routing(request: Request):
    """Hot-swap the global routing strategy (takes effect on the next request, no
    reload). Not persisted — restarts fall back to LLMOPS_ROUTING_STRATEGY."""
    body = await request.json()
    name = body.get("strategy")
    if name not in STRATEGIES:
        raise HTTPException(
            status_code=400,
            detail=f"unknown strategy {name!r}; valid: {sorted(STRATEGIES)}",
        )
    request.app.state.routing_strategy = name
    logger.info("Routing strategy set to %s via /routing", name)
    return {"strategy": name}


def _normalize_usage(usage) -> dict | None:
    """Normalize a usage block to OpenAI keys (prompt/completion/total_tokens).

    Passes an OpenAI-shaped usage straight through; maps an Anthropic-shaped one
    (`input_tokens`/`output_tokens`, as the /v1/messages family returns) onto the
    OpenAI keys so the telemetry store only ever sees one shape.
    """
    if not isinstance(usage, dict):
        return None
    if any(k in usage for k in ("prompt_tokens", "completion_tokens", "total_tokens")):
        return usage
    if "input_tokens" in usage or "output_tokens" in usage:
        pt = usage.get("input_tokens")
        ct = usage.get("output_tokens")
        tt = (pt or 0) + (ct or 0) if (pt is not None or ct is not None) else None
        return {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": tt}
    return None


def _usage_from_body(body) -> dict | None:
    """Pull a usage block out of a buffered JSON response body.

    Covers OpenAI (`usage:{...}`), Anthropic Messages (`usage:{input_tokens,
    output_tokens}`) and Anthropic count_tokens (bare top-level `input_tokens`).
    Returns the raw dict; callers normalize via _normalize_usage.
    """
    if body is None:
        return None
    try:
        obj = json.loads(body) or {}
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    usage = obj.get("usage")
    if usage is None and "input_tokens" in obj:
        usage = obj  # count_tokens returns input_tokens at the top level
    return usage if isinstance(usage, dict) else None


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
            if not isinstance(obj, dict):
                continue
            # OpenAI puts usage at the top level of the final chunk; Anthropic
            # splits it across message_start (message.usage.input_tokens) and
            # message_delta (usage.output_tokens) — merge so we keep both halves.
            for u in (obj.get("usage"), (obj.get("message") or {}).get("usage")):
                if isinstance(u, dict):
                    merged = dict(captured["usage"] or {})
                    merged.update({k: v for k, v in u.items() if v is not None})
                    captured["usage"] = merged
    return buffer


def _resolve_strategy(app, model_cfg: dict) -> str:
    """Pick the routing strategy: per-group override > global env > default.

    The per-group override rides the group's `model_config` (EngineModelConfig is
    `extra="allow"`, so `routing_strategy` passes through with no schema change).
    The global default is read once into app.state.routing_strategy at startup.
    """
    mc = model_cfg.get("model_config") or {}
    return (
        mc.get("routing_strategy")
        or getattr(app.state, "routing_strategy", None)
        or DEFAULT_STRATEGY
    )


def _session_key(request: Request, body: dict) -> str | None:
    """Affinity key for session_affinity: X-Session-Id header, else OpenAI `user`."""
    sid = request.headers.get("x-session-id")
    if sid:
        return sid
    user = body.get("user")
    return user if isinstance(user, str) and user else None


def _content_text(content) -> str:
    """Flatten an OpenAI message `content` (str or multimodal parts) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            p.get("text", "")
            for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return ""


def _prompt_prefix(body: dict, limit: int = 512) -> str | None:
    """Affinity key for prefix_affinity: the leading prompt text, bounded.

    Chat: `role:content` of the first messages until `limit` chars. Completions:
    the `prompt` (or its first element). Bounded so the hot path stays cheap.
    """
    msgs = body.get("messages")
    if isinstance(msgs, list) and msgs:
        out: list[str] = []
        total = 0
        for m in msgs:
            if not isinstance(m, dict):
                continue
            piece = f"{m.get('role', '')}:{_content_text(m.get('content'))}"
            out.append(piece)
            total += len(piece)
            if total >= limit:
                break
        return ("\n".join(out)[:limit]) or None
    prompt = body.get("prompt")
    if isinstance(prompt, str):
        return prompt[:limit] or None
    if isinstance(prompt, list) and prompt and isinstance(prompt[0], str):
        return prompt[0][:limit] or None
    return None


async def _record_request(app, model_key, instance_id, path, status_code, started,
                          usage=None, error=None, api_key_name=None):
    """Persist one request log row to the shared store. Best-effort, non-blocking
    to the response. `usage` is an OpenAI usage dict (from a buffered body or a
    streamed final chunk) when available."""
    store = getattr(app.state, "store", None)
    if store is None or not model_key:
        return
    latency_ms = (time.perf_counter() - started) * 1000.0
    usage = _normalize_usage(usage) or {}
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
        # Guard the chat/generate endpoints against a pooling group (the pooling
        # endpoints validate kind themselves in _dispatch_pooling): a kind!=chat
        # group has no chat/completions/messages. /tokenize + /detokenize are
        # intentionally exempt — every model (incl. pooling) has a tokenizer.
        if upstream_path in _CHAT_ONLY_PATHS and resolved.get("kind", "chat") != "chat":
            raise HTTPException(
                status_code=404,
                detail=f"Model '{requested}' is a pooling model (kind={resolved['kind']}); "
                       f"use /v1/embeddings or /v1/rerank.",
            )
        # Route chain: the resolved primary group, then its fallback groups —
        # tried in order when a whole group is unavailable (all instances down /
        # asleep), so a request degrades to another model instead of failing.
        chain = build_route_chain(config, resolved)

        # For streaming, ask the backend to emit a final usage chunk so token
        # counts can be logged (otherwise streamed requests log no tokens).
        # stream_options is OpenAI-only — skip it for /v1/messages, whose
        # Anthropic-shaped stream would reject the field.
        if upstream_path in _OPENAI_STREAM_PATHS and request_json.get("stream"):
            opts = request_json.get("stream_options")
            opts = opts if isinstance(opts, dict) else {}
            opts.setdefault("include_usage", True)
            request_json["stream_options"] = opts

        client = request.app.state.http_client
        response = None
        last_exc: HTTPException | None = None

        for target in chain:
            model_key = target["route_key"]
            model_cfg = target["model_cfg"]
            request_json["model"] = target["forward_name"]
            instances = model_cfg.get("instances", [])
            # At most one failover hop per extra instance, capped so a bad request
            # storming every backend stays bounded.
            max_attempts = max(1, min(len(instances), 3))
            tried: set[str] = set()

            # Routing policy + the inputs the affinity strategies need (per group;
            # key extraction is skipped for strategies that don't use it).
            strategy_name = _resolve_strategy(request.app, model_cfg)
            session_key = _session_key(request, request_json) if strategy_name == "session_affinity" else None
            prompt_prefix = _prompt_prefix(request_json) if strategy_name == "prefix_affinity" else None

            response = None
            exhausted = False
            for attempt in range(max_attempts):
                try:
                    instance = await select_instance(
                        request.app, model_key, model_cfg,
                        strategy=strategy_name, exclude=tried,
                        session_key=session_key, prompt_prefix=prompt_prefix,
                    )
                except HTTPException as e:
                    # No servable instance in this group (all down / asleep / tried):
                    # exhaust it and let the chain try the next fallback group.
                    last_exc = e
                    exhausted = True
                    break
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
                    # fail over to another instance, then to the next group.
                    mark_backend_failure(
                        request.app, model_key, instance_id, error=str(e), cooldown_seconds=10.0,
                    )
                    decr_inflight(request.app, model_key, instance_id)
                    inflight_counted = False
                    stream_ctx = None
                    if attempt < max_attempts - 1:
                        continue
                    last_exc = HTTPException(status_code=503, detail="All backends unavailable")
                    exhausted = True
                    response = None
                    break

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

            if response is not None and not exhausted:
                break  # this group served — stop walking the fallback chain

        if response is None:
            raise last_exc or HTTPException(status_code=503, detail="All backends unavailable")

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


@router.post("/v1/messages")
async def proxy_messages(request: Request):
    """Anthropic-compatible Messages API. Routes by `model` like chat and supports
    streaming (passed through in Anthropic's SSE shape)."""
    key_name = await authenticate(request)
    return await _proxy_to_backend(request, "/v1/messages", api_key_name=key_name)


@router.post("/v1/messages/count_tokens")
async def proxy_count_tokens(request: Request):
    """Anthropic token-count endpoint — no generation, returns {input_tokens}."""
    key_name = await authenticate(request)
    return await _proxy_to_backend(request, "/v1/messages/count_tokens", api_key_name=key_name)


@router.post("/tokenize")
async def proxy_tokenize(request: Request):
    """vLLM tokenize utility — works for any model kind (all have a tokenizer)."""
    key_name = await authenticate(request)
    return await _proxy_to_backend(request, "/tokenize", api_key_name=key_name)


@router.post("/detokenize")
async def proxy_detokenize(request: Request):
    """vLLM detokenize utility — token ids back to text, any model kind."""
    key_name = await authenticate(request)
    return await _proxy_to_backend(request, "/detokenize", api_key_name=key_name)


async def _proxy_to_embedding_server(request: Request, upstream_path: str, key_name=None) -> Response:
    """Forward an embedding/rerank/score request to the embedding server.

    Shared by /v1/embeddings, /v1/rerank and /v1/score — these differ only in the
    upstream path. Unlike the LLM path there's a single embedding server (no
    instance pool), so this is a straight pass-through with API-key-attributed
    request logging and token-usage capture.
    """
    started = time.perf_counter()
    model_key = None
    try:
        config = request.app.state.config
        embedding_cfg = config.get("embedding_server", {})
        host = embedding_cfg.get("host", "localhost")
        port = embedding_cfg.get("port", 8003)
        target_url = f"http://{host}:{port}{upstream_path}"

        body = await request.body()
        try:
            model_key = (json.loads(body) or {}).get("model")
        except Exception:
            model_key = None
        headers = dict(request.headers)
        headers.pop("host", None)
        headers.pop("content-length", None)

        client = request.app.state.http_client
        # The shared client has read=None (for long LLM generations); embedding/
        # rerank calls are fast, so give this path a real bound instead.
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
                _record_request, request.app, model_key, None, upstream_path,
                resp.status_code, started, _usage_from_body(resp.content),
                None, key_name,
            ),
        )

    except httpx.RequestError as e:
        await _record_request(
            request.app, model_key, None, upstream_path, 503, started,
            error=str(e), api_key_name=key_name,
        )
        raise HTTPException(
            status_code=503, detail=f"Cannot connect to embedding server: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Unexpected error: {str(e)}"
        )


async def _dispatch_pooling(request: Request, upstream_path: str, expected_kind: str, key_name):
    """Route a pooling request (embeddings / rerank / score) to its upstream.

    Two kinds of upstream can serve these endpoints:
      - a vLLM pooling group in LLM_engines (model_config.kind == embed|rerank),
        managed exactly like an LLM group -> full _proxy_to_backend machinery
        (load-aware instance selection, failover, metrics, usage logging);
      - the bespoke lightweight embedding server (its own process, routes by
        model key internally) -> the straight _proxy_to_embedding_server path.

    We peek the requested `model`: if it names an LLM_engines group we require
    its kind to match this endpoint (else 404 — e.g. an embed model on
    /v1/rerank); anything not in LLM_engines falls through to the bespoke
    server, which is left entirely unchanged.
    """
    config = request.app.state.config
    try:
        requested = (json.loads(await request.body()) or {}).get("model")
    except Exception:
        requested = None

    resolved = resolve_model(config, requested) if requested else None
    if resolved is not None:
        kind = resolved.get("kind", "chat")
        if kind == expected_kind:
            return await _proxy_to_backend(request, upstream_path, api_key_name=key_name)
        raise HTTPException(
            status_code=404,
            detail=f"Model '{requested}' does not serve {upstream_path} (kind={kind}).",
        )
    return await _proxy_to_embedding_server(request, upstream_path, key_name)


@router.post("/v1/embeddings")
async def proxy_embeddings(request: Request):
    key_name = await authenticate(request)
    return await _dispatch_pooling(request, "/v1/embeddings", "embed", key_name)


@router.post("/v1/rerank")
async def proxy_rerank(request: Request):
    key_name = await authenticate(request)
    return await _dispatch_pooling(request, "/v1/rerank", "rerank", key_name)


@router.post("/v1/score")
async def proxy_score(request: Request):
    key_name = await authenticate(request)
    return await _dispatch_pooling(request, "/v1/score", "rerank", key_name)