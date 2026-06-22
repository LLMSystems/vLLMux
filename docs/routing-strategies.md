# Pluggable request-routing strategies

Status: implemented (config + live frontend switch) · Owner: router-server · Last updated: 2026-06-20

## 1. Background

The router (`apps/router-server`) load-balances every OpenAI-style request across
the instances of a model group. Until now the policy was hard-coded:
`select_instance_least_load` scored each instance by

```
load = waiting·10 + running·3 + kv_cache_usage_perc·100      # from /metrics, polled ~1s
score = load + inflight·5 + (1e9 if in cooldown else 0)
```

and picked the minimum. This is a solid **load-aware least-load** policy, but it
optimises purely for *spreading* requests. For workloads that benefit from
*affinity* — multi-turn chat, a shared system prompt, RAG templates — spreading
actively hurts: the same conversation bounces between replicas and every turn
re-computes the prefill instead of hitting the KV / prefix cache.

There is no single right policy. This change turns the routing decision into a
**swappable strategy** chosen by config, with `least_load` as the default so
existing deployments are unchanged.

See `docs/vllm-model-serving-load-balancing.md` for the taxonomy this draws on.

## 2. Goals / non-goals

**Goals**
- A catalogue of selectable strategies (round-robin, random, least-inflight,
  least-load, power-of-two-choices, session affinity, prefix affinity).
- Pick the strategy globally (env var) or per model group (config override).
- 100% backward compatible: default is `least_load`, identical to today.
- Strategies stay small and side-effect-free; all the resilient-routing
  machinery (in-flight accounting, failover, cooldown) stays in the proxy and is
  shared by every strategy.

**Non-goals (explicitly deferred)**
- True KV-cache-aware routing (needs per-prefix cache occupancy from each
  instance; vLLM doesn't expose it cheaply). Prefix-affinity is the pragmatic 80%.
- Disaggregated-prefill routing and EPLB — those are deployment-topology / MoE
  concerns, not a routing-algorithm swap.
- A runtime UI switch. Config + `/reload` is enough for phase 1; a dashboard
  dropdown is a later phase.

## 3. Design

### 3.1 Where the seam is

`_proxy_to_backend` in `router.py` already owns the request lifecycle: parse body,
resolve the model, then a **failover loop** of up to `min(#instances, 3)` attempts
that calls the selector, counts in-flight, marks success/failure, and retries the
next-best instance on a transport error or 5xx. The only thing that changes is the
one line that picks an instance.

```
for attempt in range(max_attempts):
    instance = await select_instance(app, model_key, model_cfg,
                                     strategy=strategy_name, exclude=tried,
                                     session_key=session_key, prompt_prefix=prompt_prefix)
    ...                      # inflight / failover / cooldown — unchanged
```

A strategy therefore only has to answer *"given the live cluster state, which one
instance?"*. Everything resilient lives outside it and is reused by all.

### 3.2 The strategy interface

`apps/router-server/src/llm_router/routing_strategies.py`:

```python
@dataclass
class SelectContext:
    app: Any                 # carries .state.metrics_cache / backend_inflight / backend_health / rr_counters
    model_key: str
    candidates: list[dict]   # instances still eligible this request (exclude already removed)
    all_instances: list[dict]# the full group roster (affinity hashes over this, stable set)
    session_key: str | None  # for session_affinity
    prompt_prefix: str | None# for prefix_affinity

Strategy = Callable[[SelectContext], dict]   # returns one instance dict
STRATEGIES: dict[str, Strategy]
```

`score_instance(app, model_key, instance) -> float` is extracted verbatim from the
old selector (load score + in-flight penalty + cooldown fail-open, with the same
cold-start "unknown metric = idle" handling). `least_load`, `least_inflight`,
`p2c`, and the affinity fallbacks all build on it, so the scoring stays a single
source of truth and `least_load` is byte-for-byte the historical behaviour.

### 3.3 The strategies

| name | signal | behaviour | best for |
|---|---|---|---|
| `round_robin` | none | per-group counter on `app.state.rr_counters`, `candidates[n % len]` | homogeneous GPUs, baseline |
| `random` | none | `random.choice(candidates)` | many short requests, lowest decision cost |
| `least_inflight` | local in-flight only | min by `inflight·W + cooldown` (no scrape) | low-latency decisions, scrape-independent |
| `least_load` *(default)* | waiting/running/kv + in-flight | the existing policy, unchanged | heterogeneous request sizes |
| `p2c` | sample 2, compare | power-of-two-choices over `score_instance` | bursty traffic, avoids herding the single "best" during the 1 s scrape blind window |
| `session_affinity` | session key → sticky | hash key to a home instance, escape to least-load when home is in cooldown or overloaded | multi-turn chat, playground |
| `prefix_affinity` | prompt prefix → sticky | same, keyed on the prompt prefix hash | shared system prompts, RAG templates |

**Affinity = sticky + load escape valve.** Both affinity strategies share one
helper:

```
home = sorted(all_instances by id)[ sha1(key) % len ]          # deterministic, salt-free
if home is a live candidate and not in cooldown
   and score(home) <= score(least_loaded_candidate) + AFFINITY_LOAD_MARGIN:
       return home                       # keep the conversation on its replica
return least_loaded_candidate            # fall back to least_load
```

- Hashing is `hashlib.sha1` (not the builtin `hash`, which is per-process salted)
  so the same key maps to the same replica across router restarts and workers.
- When there's no key (no session id / no prompt), affinity degrades to
  `least_load` — so turning the strategy on is **never worse** than the default,
  it only adds stickiness when a key is available.
- `AFFINITY_LOAD_MARGIN` is the escape threshold: how much extra load we tolerate
  on the home replica before giving up the cache benefit and spreading. Tunable
  via `LLMOPS_AFFINITY_LOAD_MARGIN` (default 50.0).

### 3.4 Key extraction (router side)

The proxy computes, once per request, the inputs the affinity strategies need:

- `session_key` = `X-Session-Id` header, else the OpenAI `user` field in the body.
- `prompt_prefix` (only when the strategy is `prefix_affinity`) = the first
  messages (`role:content`, chat) or the `prompt` (completions), truncated to 512
  chars. Cheap, bounded, and never blocks the hot path for the other strategies.

### 3.5 Choosing the strategy

Resolution order (first hit wins), evaluated per request so `/reload` picks up edits:

1. **Per-group**: `model_config.routing_strategy` in the group's config entry
   (passes through `EngineModelConfig`'s `extra="allow"`, no schema change).
2. **Global**: `LLMOPS_ROUTING_STRATEGY` env var (read once into
   `app.state.routing_strategy`).
3. **Default**: `least_load`.

An unknown name logs a warning and falls back to `least_load` rather than failing
a request.

Example — chat group sticky, embeddings round-robin:

```yaml
LLM_engines:
  Qwen3-Chat:
    instances: [ ... ]
    model_config:
      model_tag: Qwen/Qwen3-8B
      routing_strategy: session_affinity
```

```bash
LLMOPS_ROUTING_STRATEGY=least_load        # global default for groups without an override
LLMOPS_AFFINITY_LOAD_MARGIN=50            # affinity escape threshold
```

## 4. Compatibility & risk

- `select_instance_least_load` is kept as a thin wrapper over
  `select_instance(..., strategy="least_load")`, so existing imports and the
  `test_backend_selector` suite are untouched.
- New `app.state` field: `rr_counters` (round-robin) and `routing_strategy`,
  initialised in `main.py`'s lifespan alongside the existing maps.
- No schema migration; per-group override rides `extra="allow"`.
- Failure modes are contained: unknown strategy → default; affinity with no key →
  least-load; a strategy that returns nothing → first candidate.

## 5. Testing

- Unit tests per strategy in `tests/unit/test_routing_strategies.py`:
  round-robin cycles; random stays in-set; least-inflight & least-load picks;
  p2c never exceeds the worse of its two samples; session/prefix affinity is
  deterministic for a key, escapes on cooldown and over-margin load, and degrades
  to least-load with no key; dispatcher handles no-instances (500), all-excluded
  (503), single-candidate shortcut, and unknown-name fallback.
- The existing `test_backend_selector` and `test_router_proxy` suites must stay
  green (backward-compat guarantee).
- `python3 -m pytest tests/unit -q` for the suite; `mypy`/`ruff` for type-check.

## 6. Frontend hot-swap (implemented)

The router exposes a control endpoint so the global strategy can be switched live,
without a `/reload` or restart:

- `GET /routing` → `{ strategy, available[], default }`.
- `POST /routing {"strategy": "<name>"}` → validates against the registry and sets
  `app.state.routing_strategy`. Effective on the next request; **not persisted**
  (a restart falls back to `LLMOPS_ROUTING_STRATEGY`).
- Exposed through nginx as `location = /routing` (alongside `/metrics`, `/reload`).

The dashboard **Traffic** page has a strategy dropdown (next to the load-balancing
fan) wired to these via `api.getRouting()` / `api.setRouting()`. A per-group
`model_config.routing_strategy` still overrides this global value for that group.

## 7. Future phases

1. Per-group override + global default + all 7 strategies. *(done)*
2. Dashboard dropdown + live `GET/POST /routing` hot-swap. *(done)*
3. Persisted / per-group editing from the UI (write to the overlay), so a chosen
   strategy survives a router restart.
4. True KV-cache-aware routing once per-instance prefix-cache stats are exposed.
