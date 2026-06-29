# gunicorn.conf.py

import os

bind = "0.0.0.0:8887"
# Horizontal scale-out within the one router container: each worker is an
# independent, stateless event loop sharing the same netns + shared store, so N
# workers handle N× concurrent proxied requests. The router is IO-bound (it just
# forwards to vLLM), so this is the cheapest way to add routing throughput. Set
# ROUTER_WORKERS in deploy/.env. Default 1 keeps existing single-host behaviour.
workers = max(1, int(os.environ.get("ROUTER_WORKERS", "1")))
worker_class = "uvicorn.workers.UvicornWorker"
timeout = 0
loglevel = "info"
# Access logging is disabled on purpose: every request is already persisted to
# the shared SQLite store (request_logs), so a per-request stdout write here is
# pure overhead on the single worker's event loop. Set GUNICORN_ACCESSLOG=- to
# re-enable for debugging.
accesslog = os.environ.get("GUNICORN_ACCESSLOG") or None
errorlog = "-"
preload_app = False
