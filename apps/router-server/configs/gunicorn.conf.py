# gunicorn.conf.py

import os

bind = "0.0.0.0:8887"
workers = 1
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
