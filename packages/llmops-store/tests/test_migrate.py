"""SQLite -> Postgres migration: value coercion (pure) + a full copy (PG-gated)."""
import os
import sys

import pytest

PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG_ROOT not in sys.path:
    sys.path.insert(0, PKG_ROOT)

from llmops_store import LLMOpsStore  # noqa: E402
from migrate_sqlite_to_pg import _coerce, migrate  # noqa: E402

pytestmark = pytest.mark.unit


def test_coerce_by_target_type():
    assert _coerce(5, "double precision") == 5.0 and isinstance(_coerce(5, "double precision"), float)
    assert _coerce(3.0, "bigint") == 3 and isinstance(_coerce(3.0, "bigint"), int)
    assert _coerce("hi", "text") == "hi"
    assert _coerce(None, "double precision") is None


async def _seed_sqlite(path):
    s = await LLMOpsStore(path).init()
    await s.create_api_key("ci", "h1", "sk-x", rpm_limit=60, token_quota=1000, quota_period="daily")
    await s.record_request(model_key="Qwen", path="/v1/chat/completions",
                           total_tokens=150, api_key_name="ci")
    await s.set_model_price("Qwen", 1.5, 4.0, "USD")
    await s.record_config_version('{"LLM_engines": {"Qwen": {}}}', "h-ov", actor="admin")
    await s.set_instance_desired("Qwen::a", "running")
    await s.close()


@pytest.fixture
def _pg_url():
    url = os.environ.get("LLMOPS_TEST_DB_URL")
    if not url:
        pytest.skip("set LLMOPS_TEST_DB_URL to run the Postgres migration test")
    return url


async def _wipe_pg(url):
    import asyncpg
    conn = await asyncpg.connect(url)
    await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    await conn.close()


async def test_migrate_copies_data_and_advances_sequence(tmp_path, _pg_url):
    src = str(tmp_path / "src.db")
    await _seed_sqlite(src)
    await _wipe_pg(_pg_url)

    await migrate(src, _pg_url, wipe=True)

    pg = await LLMOpsStore(db_url=_pg_url).init()
    try:
        keys = await pg.list_api_keys()
        assert len(keys) == 1 and keys[0]["name"] == "ci" and keys[0]["token_quota"] == 1000
        prices = await pg.list_model_prices()
        assert prices[0]["model"] == "Qwen" and prices[0]["input_price"] == 1.5
        assert (await pg.list_instance_desired()) == {"Qwen::a": "running"}
        # the migrated overlay is recoverable for hydration
        assert list((await pg.get_current_overlay())["LLM_engines"]) == ["Qwen"]
        # the IDENTITY sequence was advanced past the copied id=1
        assert await pg.create_api_key("new", "h2", "sk-y") == 2
    finally:
        await pg.close()


async def test_migrate_refuses_without_wipe(tmp_path, _pg_url):
    src = str(tmp_path / "src.db")
    await _seed_sqlite(src)
    await _wipe_pg(_pg_url)
    await migrate(src, _pg_url, wipe=True)  # first copy
    with pytest.raises(SystemExit):
        await migrate(src, _pg_url, wipe=False)  # target non-empty -> refuse
