"""Entrypoint shim so `uvicorn main:app` keeps working.

The real app factory + lifespan live in app/main.py.
"""
from app.main import app  # noqa: F401
