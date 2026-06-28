"""HA overlay hydration: in Postgres mode the local overlay file is refreshed from
the shared DB; in SQLite mode it's a no-op."""
import json

import pytest

from app.services.overlay import hydrate_overlay_from_store, load_overlay

pytestmark = pytest.mark.unit


class _Store:
    def __init__(self, db_url, overlay):
        self.db_url = db_url
        self._overlay = overlay

    async def get_current_overlay(self):
        return self._overlay


async def test_hydrate_noop_without_store(tmp_path):
    p = str(tmp_path / "ov.json")
    assert await hydrate_overlay_from_store(None, p) is False


async def test_hydrate_noop_in_sqlite_mode(tmp_path):
    # db_url None => SQLite => the file is already the shared truth => no-op.
    p = str(tmp_path / "ov.json")
    assert await hydrate_overlay_from_store(_Store(None, {"LLM_engines": {"X": {}}}), p) is False


async def test_hydrate_writes_db_overlay_to_file(tmp_path):
    p = str(tmp_path / "ov.json")
    overlay = {"LLM_engines": {"Qwen": {"instances": [{"id": "a"}]}}}
    ok = await hydrate_overlay_from_store(_Store("postgres://x", overlay), p)
    assert ok is True
    assert json.loads(open(p).read())["LLM_engines"]["Qwen"]["instances"][0]["id"] == "a"
    assert load_overlay(p) == overlay  # readable back through the normal loader


async def test_hydrate_noop_when_db_empty(tmp_path):
    p = str(tmp_path / "ov.json")
    assert await hydrate_overlay_from_store(_Store("postgres://x", None), p) is False
