"""Backend runtime tunables, sourced from environment variables.

Kept as a plain dataclass (rather than pulling in pydantic-settings) so there is
no extra dependency. All values have sensible defaults; override via env for
deployment without code changes.
"""
from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from typing import Optional


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _env_csv(name: str) -> tuple[str, ...]:
    """Parse a comma-separated env var into a tuple of trimmed, non-empty items."""
    raw = os.environ.get(name, "")
    return tuple(s.strip() for s in raw.split(",") if s.strip())


# Fallback session-signing key when neither LLMOPS_SESSION_SECRET nor the admin
# token is set. Process-local + random, so SSO sessions don't survive a restart
# (dev-only convenience; production must set LLMOPS_SESSION_SECRET).
import secrets as _secrets  # noqa: E402

_RANDOM_SECRET = _secrets.token_urlsafe(32)


@dataclass(frozen=True)
class BackendSettings:
    # How often the reconciler derives observed state from process + health.
    poll_interval: float = 2.0
    # STARTING -> FAILED if not READY within this many seconds.
    start_timeout: float = 300.0
    # Grace period for SIGTERM before SIGKILL when stopping.
    stop_timeout: float = 10.0
    # Graceful drain: before killing an instance on stop, tell the router to send
    # it no new requests and wait up to this long for in-flight ones to finish
    # (0 disables; returns early as soon as in-flight hits 0).
    drain_timeout: float = 30.0
    drain_poll_interval: float = 1.0
    # How often the GPU-process inventory is refreshed.
    gpu_poll_interval: float = 5.0
    # How often per-group live load (queue depth) is aggregated from the router scrape.
    load_poll_interval: float = 5.0
    # How often the autoscaler evaluates each autoscale-enabled group.
    autoscale_interval: float = 5.0
    # SQLite telemetry DB path. None -> let LLMOpsStore use its shared default.
    db_path: Optional[str] = None
    # Cap on retained audit-log rows; the oldest are pruned hourly past this.
    audit_max_rows: int = 50_000
    # Cap on retained overlay-config version snapshots; oldest pruned hourly past this.
    config_versions_max: int = 500
    # Default per-1M-token prices for models without an explicit price row, and the
    # dashboard's display currency. 0 -> cost shows as 0 until a price is set.
    default_input_price: float = 0.0
    default_output_price: float = 0.0
    price_currency: str = "USD"
    # Pre-flight VRAM check before starting a model (blocks likely-OOM starts).
    vram_guard: bool = True
    # On boot, restart models whose persisted desired state is RUNNING but which
    # aren't (after a restart or replica takeover). Disable for a dev box that
    # shouldn't auto-start models on every backend restart.
    replay_desired: bool = True
    # Auto-restart a managed model that crashes while desired=running.
    auto_restart: bool = True
    # Max consecutive auto-restarts before giving up (budget resets once READY).
    max_restarts: int = 3
    # Exponential backoff base (seconds) between auto-restart attempts.
    restart_backoff_base: float = 5.0
    # Shared admin token gating control/write operations + API-key management.
    # Empty -> auth disabled (dev mode): writes are open and a warning is logged.
    admin_token: str = ""
    # Lifecycle-alert sinks (any subset; empty -> alerting disabled). The generic
    # webhook keeps its historical name; Slack/Discord get formatted messages.
    alert_webhook: str = ""
    alert_slack_webhook: str = ""
    alert_discord_webhook: str = ""
    # Global severity floor (info|warning|error|critical) and per-(model,event)
    # cooldown so a crash-looping model can't spam the channel.
    alert_min_severity: str = "error"
    alert_cooldown_s: float = 300.0
    # Optional path for the Prometheus file_sd targets file. The backend rewrites
    # it whenever the set of ready vLLM instances changes, so Prometheus can
    # scrape a dynamic fleet without config edits. Empty -> feature disabled.
    prometheus_sd_path: str = ""
    # Total concurrency budget shared across running evals (sum of their
    # eval_batch_size). Evals run in parallel as long as the sum stays within
    # this; the rest queue. Maps to vLLM's max-num-seqs pressure. Runtime-editable
    # via the eval API (not persisted across restart).
    eval_concurrency_budget: int = 32
    # ---- SSO / OIDC (all optional; empty issuer -> SSO disabled) ----
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_url: str = ""  # empty -> derived from the request host
    oidc_scopes: str = "openid email profile"
    oidc_groups_claim: str = "groups"
    oidc_admin_emails: tuple[str, ...] = ()
    oidc_admin_groups: tuple[str, ...] = ()
    oidc_operator_groups: tuple[str, ...] = ()
    oidc_viewer_groups: tuple[str, ...] = ()
    oidc_default_role: str = "viewer"  # role for an authenticated user matching no group; "" = deny
    # Session cookie signing key + lifetime. Empty secret -> derived from
    # admin_token, else a random per-process key (dev only; set it in prod).
    session_secret: str = ""
    session_ttl: int = 28_800  # 8h
    # HA leader election (only meaningful with a shared Postgres store): this
    # replica's id (lease holder) and the lease TTL. A peer steals an expired
    # lease, so failover happens within ~ttl seconds.
    instance_id: str = ""
    leader_lease_ttl: float = 15.0

    # HA Phase 3a: address this node advertises for its instances in instances_live.
    # Empty -> use each instance's configured host (127.0.0.1 in the collapsed
    # single-host deploy). Set to this node's routable IP/hostname when splitting
    # the router/agent across hosts so routers can reach vLLM over the network.
    # The live address is heartbeated each reconcile pass; live_ttl is its lease.
    node_host: str = ""
    live_ttl: float = 30.0

    @property
    def auth_enabled(self) -> bool:
        return bool(self.admin_token)

    @property
    def sso_enabled(self) -> bool:
        return bool(self.oidc_issuer and self.oidc_client_id and self.oidc_client_secret)

    @property
    def signing_secret(self) -> str:
        """Key for session/state cookies: explicit, else admin_token, else random
        (random means a restart invalidates sessions — fine for dev, not prod)."""
        if self.session_secret:
            return self.session_secret
        if self.admin_token:
            return "sess:" + self.admin_token
        return _RANDOM_SECRET

    @classmethod
    def from_env(cls) -> "BackendSettings":
        return cls(
            admin_token=os.environ.get("LLMOPS_ADMIN_TOKEN", "").strip(),
            alert_webhook=os.environ.get("LLMOPS_ALERT_WEBHOOK", "").strip(),
            alert_slack_webhook=os.environ.get("LLMOPS_ALERT_SLACK_WEBHOOK", "").strip(),
            alert_discord_webhook=os.environ.get("LLMOPS_ALERT_DISCORD_WEBHOOK", "").strip(),
            alert_min_severity=os.environ.get("LLMOPS_ALERT_MIN_SEVERITY", "error").strip() or "error",
            alert_cooldown_s=_env_float("LLMOPS_ALERT_COOLDOWN_S", 300.0),
            prometheus_sd_path=os.environ.get("LLMOPS_PROMETHEUS_SD_PATH", "").strip(),
            poll_interval=_env_float("LLMOPS_POLL_INTERVAL", 2.0),
            start_timeout=_env_float("LLMOPS_START_TIMEOUT", 300.0),
            stop_timeout=_env_float("LLMOPS_STOP_TIMEOUT", 10.0),
            drain_timeout=_env_float("LLMOPS_DRAIN_TIMEOUT", 30.0),
            drain_poll_interval=_env_float("LLMOPS_DRAIN_POLL_INTERVAL", 1.0),
            gpu_poll_interval=_env_float("LLMOPS_GPU_POLL_INTERVAL", 5.0),
            load_poll_interval=_env_float("LLMOPS_LOAD_POLL_INTERVAL", 5.0),
            autoscale_interval=_env_float("LLMOPS_AUTOSCALE_INTERVAL", 5.0),
            db_path=os.environ.get("LLMOPS_DB_PATH"),
            config_versions_max=int(_env_float("LLMOPS_CONFIG_VERSIONS_MAX", 500)),
            oidc_issuer=os.environ.get("LLMOPS_OIDC_ISSUER", "").strip().rstrip("/"),
            oidc_client_id=os.environ.get("LLMOPS_OIDC_CLIENT_ID", "").strip(),
            oidc_client_secret=os.environ.get("LLMOPS_OIDC_CLIENT_SECRET", "").strip(),
            oidc_redirect_url=os.environ.get("LLMOPS_OIDC_REDIRECT_URL", "").strip(),
            oidc_scopes=os.environ.get("LLMOPS_OIDC_SCOPES", "openid email profile").strip()
            or "openid email profile",
            oidc_groups_claim=os.environ.get("LLMOPS_OIDC_GROUPS_CLAIM", "groups").strip() or "groups",
            oidc_admin_emails=_env_csv("LLMOPS_OIDC_ADMIN_EMAILS"),
            oidc_admin_groups=_env_csv("LLMOPS_OIDC_ADMIN_GROUPS"),
            oidc_operator_groups=_env_csv("LLMOPS_OIDC_OPERATOR_GROUPS"),
            oidc_viewer_groups=_env_csv("LLMOPS_OIDC_VIEWER_GROUPS"),
            oidc_default_role=os.environ.get("LLMOPS_OIDC_DEFAULT_ROLE", "viewer").strip(),
            session_secret=os.environ.get("LLMOPS_SESSION_SECRET", "").strip(),
            session_ttl=int(_env_float("LLMOPS_SESSION_TTL", 28_800)),
            instance_id=os.environ.get("LLMOPS_INSTANCE_ID", "").strip()
            or f"{socket.gethostname()}:{os.getpid()}",
            leader_lease_ttl=_env_float("LLMOPS_LEADER_LEASE_TTL", 15.0),
            node_host=os.environ.get("LLMOPS_NODE_HOST", "").strip(),
            live_ttl=_env_float("LLMOPS_LIVE_TTL", 30.0),
            default_input_price=_env_float("LLMOPS_DEFAULT_INPUT_PRICE", 0.0),
            default_output_price=_env_float("LLMOPS_DEFAULT_OUTPUT_PRICE", 0.0),
            price_currency=os.environ.get("LLMOPS_PRICE_CURRENCY", "USD").strip() or "USD",
            vram_guard=_env_bool("LLMOPS_VRAM_GUARD", True),
            replay_desired=_env_bool("LLMOPS_REPLAY_DESIRED", True),
            auto_restart=_env_bool("LLMOPS_AUTO_RESTART", True),
            max_restarts=int(_env_float("LLMOPS_MAX_RESTARTS", 3)),
            restart_backoff_base=_env_float("LLMOPS_RESTART_BACKOFF", 5.0),
            eval_concurrency_budget=int(_env_float("LLMOPS_EVAL_CONCURRENCY_BUDGET", 32)),
        )
