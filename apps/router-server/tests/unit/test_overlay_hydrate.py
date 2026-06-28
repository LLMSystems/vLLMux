"""Router HA overlay hydration: refresh the local overlay file from the shared DB
(Postgres mode) so a router on another host routes to dynamically-added models."""
import json

import pytest

from src.llm_router.overlay import hydrate_overlay_from_store, load_overlay

pytestmark = pytest.mark.unit


class _Store:
    def __init__(self, db_url, overlay):
        self.db_url = db_url
        self._overlay = overlay

    async def get_current_overlay(self):
        return self._overlay


async def test_noop_without_store(tmp_path):
    assert await hydrate_overlay_from_store(None, str(tmp_path / "ov.json")) is False


async def test_noop_in_sqlite_mode(tmp_path):
    s = _Store(None, {"LLM_engines": {"X": {}}})
    assert await hydrate_overlay_from_store(s, str(tmp_path / "ov.json")) is False


async def test_writes_overlay_from_db(tmp_path):
    p = str(tmp_path / "ov.json")
    overlay = {"LLM_engines": {"Qwen": {"instances": [{"id": "a", "host": "h", "port": 8002}]}}}
    assert await hydrate_overlay_from_store(_Store("postgres://x", overlay), p) is True
    assert json.loads(open(p).read())["LLM_engines"]["Qwen"]["instances"][0]["id"] == "a"
    assert load_overlay(p)["LLM_engines"]["Qwen"]  # readable by the router loader
