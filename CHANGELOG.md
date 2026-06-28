# Changelog

All notable changes to vLLMux are documented here.

## v0.2.0 — 2026-06-28

A large release: a full control-plane **high-availability** path (optional
Postgres + leader election), **SSO login**, a **cost dashboard**, **config
versioning**, **lifecycle alerting**, **multi-user RBAC + audit**, and
**autoscaling** with a warm-standby tier — plus many routing and UX additions.
Single-machine SQLite stays the zero-config default; everything new is opt-in.

### Control-plane HA & reliability
- **Optional Postgres backend** for the shared store (`LLMOPS_DB_URL`): the store
  runs on either SQLite (default) or Postgres, behind a driver layer that's
  behaviour-equivalent across both. A profile-gated `postgres` compose service +
  connection retry, and a `migrate_sqlite_to_pg.py` one-time data migration.
- **Leader election** — with Postgres, only the elected leader runs the singleton
  control loops (reconcile / autoscale / prune); a standby steals the expired
  lease and takes over within ~`LLMOPS_LEADER_LEASE_TTL`. SQLite = permanent
  single leader (unchanged).
- **Overlay-in-DB hydration + desired replay** — config and "should it be running"
  intent live in the shared store; a restarted backend / new replica restores the
  models that were running.
- **Graceful drain** — instances stop taking new requests and let in-flight ones
  finish before being killed; drain marks are shared so every router replica
  honours them.
- **Router health probes** — `GET /health` (liveness) + `GET /ready` (readiness)
  for k8s / load balancers.
- HA design docs (Phase 1–3) and a multi-replica failover demo compose.

### Security & multi-user
- **SSO login (OIDC)** — sign in with a corporate IdP (Google / Entra / Okta /
  any OIDC); IdP email/groups map to roles; signed session cookie; coexists with
  tokens (the router also accepts the SSO cookie for inference).
- **Multi-user RBAC + audit log** — named operator credentials with
  `viewer`/`operator`/`admin` roles, redacted audit trail, role edit & token
  rotation, audit retention + pagination.
- **Per-key token quotas** (total / daily / monthly) and rate limits on API keys.

### Operations
- **Cost dashboard** — per-1M-token price table turns usage into cost, with
  per-model and per-key breakdowns over a time range.
- **Config versioning & backup** — every overlay change is snapshotted; export /
  import a portable backup, diff and one-click roll back to any past version.
- **Lifecycle alerting** — crash / restart-budget-exhausted / recovered events
  pushed to Slack / Discord / generic webhook, with per-sink severity floors and
  per-model cooldown.

### Autoscaling
- Per-group autoscaler control loop with a **warm-standby tier** (vLLM sleep
  mode), a management UI + API, advanced timing/threshold knobs, and embedded
  Prometheus metrics + Grafana dashboard & alerts.

### Routing & API
- **Cross-model fallback chains** — degrade to another compatible group when one
  is fully down.
- `/v1/rerank` and `/v1/score` endpoints; embeddings / rerank / score routed to
  vLLM pooling groups (with a `kind` on model_config).
- Anthropic-compatible `/v1/messages` (streaming) + `count_tokens`, and
  `/tokenize` / `/detokenize`.

### Frontend / UX
- Cost, Config Versions, Operators, Audit and Notifications pages.
- Sidebar redesign (grouped sections, collapsible rail); playground supports
  pooling models + manual embed/rerank; animated topology and health-tinted KPIs.

## v0.1.0

Initial release: paste-a-`vllm serve` model management, a single OpenAI/Anthropic-
compatible router with load-aware balancing, per-instance lifecycle, cross-instance
KV-cache sharing, bundled Prometheus + Grafana monitoring, a Playground, and
evalscope load tests + accuracy evaluation — all behind one Vue dashboard.
