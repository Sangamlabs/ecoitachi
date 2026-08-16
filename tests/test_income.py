"""Integration tests for the daily income claim commands.

Cover: first-run initialization, 24h accumulation with multi-day stacking,
claim payout to wallet with transaction logging, double-claim protection,
and admin rate configuration.

Run with:  pytest tests/test_income.py -v
"""

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("BOT_TOKEN", "123:testtoken")
os.environ.setdefault("MONGO_URI", "mongodb://127.0.0.1:27017")
os.environ.setdefault("MONGO_DB_NAME", "unoitachi_tests_income")
os.environ.setdefault("OWNER_ID", "1")
os.environ.setdefault("CATBOX_ENABLED", "false")

from database import income as income_db  # noqa: E402
from database import users as users_db  # noqa: E402
from database.mongo import mongo  # noqa: E402
from services import economy, income as income_service  # noqa: E402
from services import settings as settings_service  # noqa: E402
from services import transaction as tx_service  # noqa: E402

A, B, ADMIN = 9301, 9302, 1

DAY = income_service.DAY_SECONDS


def mongo_available() -> bool:
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(mongo.connect())
        loop.run_until_complete(mongo.close())
        loop.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not mongo_available(), reason="MongoDB not reachable")


@pytest.fixture(autouse=True)
async def clean_db():
    await mongo.connect()
    db = mongo.db
    await db.users.delete_many({"user_id": {"$in": [A, B]}})
    await db.transactions.delete_many({"user_id": {"$in": [A, B]}})
    await db.settings.delete_many({})
    await settings_service.ensure_indexes()
    await settings_service.update_settings(starting_balance=0)
    await users_db.get_or_create_user(A, "user_a", "User A")
    await users_db.get_or_create_user(B, "user_b", "User B")
    yield
    await mongo.close()


async def _fund_bank(user_id: int, amount: int) -> None:
    await economy.admin_give(user_id, amount, ADMIN)
    await economy.deposit(user_id, amount)


async def _rewind_last(user_id: int, source: str, days_back: int) -> None:
    field = income_service.LAST_FIELD_BY_SOURCE[source]
    now = int(time.time())
    await users_db.set_user_field(user_id, field, now - DAY * days_back)


async def test_bank_first_run_starts_tracking():
    await _fund_bank(A, 1_000_000)
    result = await income_service.claim(A, income_service.BANK)
    assert result["started"] is True
    assert result["amount"] == 0
    assert result["value"] == 1_000_000


async def test_bank_claim_accumulates_multi_day():
    await _fund_bank(A, 1_000_000)
    await income_service.claim(A, income_service.BANK)
    await _rewind_last(A, income_service.BANK, 3)
    before = await economy.get_balance(A)
    result = await income_service.claim(A, income_service.BANK)
    rate = 2.0
    expected = int(1_000_000 * rate) // 100 * 3
    assert result["amount"] == expected
    assert result["days"] == 3
    after = await economy.get_balance(A)
    assert after["wallet"] - before["wallet"] == expected
    tx = await tx_service.get_recent(A, 1)
    assert tx[0]["type"] == tx_service.INTEREST_CLAIM


async def test_no_claim_within_24h():
    await _fund_bank(A, 1_000_000)
    await income_service.claim(A, income_service.BANK)
    result = await income_service.claim(A, income_service.BANK)
    assert result["amount"] == 0
    assert result["days"] == 0
    assert result["next_in"] > 0


async def test_asset_income_uses_cached_asset_value():
    await users_db.set_user_field(A, "asset_value", 5_000_000)
    await income_service.claim(A, income_service.ASSET)
    await _rewind_last(A, income_service.ASSET, 2)
    result = await income_service.claim(A, income_service.ASSET)
    rate = float((await settings_service.get_income_config())["asset_rate_percent"])
    assert result["amount"] == (int(5_000_000 * rate) // 100) * 2
    assert result["value"] == 5_000_000
    tx = await tx_service.get_recent(A, 1)
    assert tx[0]["type"] == tx_service.ASSET_INCOME_CLAIM


async def test_stock_income_uses_cached_stock_value():
    await users_db.set_user_field(A, "stocks_value", 8_000_000)
    await income_service.claim(A, income_service.STOCK)
    await _rewind_last(A, income_service.STOCK, 5)
    result = await income_service.claim(A, income_service.STOCK)
    rate = float((await settings_service.get_income_config())["stock_rate_percent"])
    assert result["amount"] == (int(8_000_000 * rate) // 100) * 5
    tx = await tx_service.get_recent(A, 1)
    assert tx[0]["type"] == tx_service.STOCK_INCOME_CLAIM


async def test_double_claim_pays_once():
    await _fund_bank(A, 1_000_000)
    await income_service.claim(A, income_service.BANK)
    await _rewind_last(A, income_service.BANK, 1)
    r1 = await income_service.claim(A, income_service.BANK)
    r2 = await income_service.claim(A, income_service.BANK)
    assert r1["amount"] > 0
    assert r2["amount"] == 0
    assert r2["days"] == 0


async def test_concurrent_claim_guard_advances_once():
    await _fund_bank(A, 1_000_000)
    await income_service.claim(A, income_service.BANK)
    await _rewind_last(A, income_service.BANK, 1)
    field = income_service.LAST_FIELD_BY_SOURCE[income_service.BANK]
    stale = (await users_db.get_user(A))[field]
    now = int(time.time())
    won = await income_db.advance_last_claim(A, field, stale, now)
    lost = await income_db.advance_last_claim(A, field, stale, now)
    assert won is True
    assert lost is False
    user = await users_db.get_user(A)
    assert user[field] == now


async def test_zero_balance_advances_clock():
    await income_service.claim(A, income_service.BANK)
    await _rewind_last(A, income_service.BANK, 2)
    result = await income_service.claim(A, income_service.BANK)
    assert result["amount"] == 0
    user = await users_db.get_user(A)
    assert user[income_service.LAST_FIELD_BY_SOURCE[income_service.BANK]] > 0


async def test_admin_sets_income_rate():
    await settings_service.update_income_config(asset_rate_percent=4.5)
    config = await settings_service.get_income_config()
    assert config["asset_rate_percent"] == 4.5
    await users_db.set_user_field(A, "asset_value", 1_000_000)
    await income_service.claim(A, income_service.ASSET)
    await _rewind_last(A, income_service.ASSET, 1)
    result = await income_service.claim(A, income_service.ASSET)
    assert result["rate"] == 4.5
    assert result["amount"] == int(1_000_000 * 4.5) // 100


async def test_sources_have_independent_clocks():
    await _fund_bank(A, 1_000_000)
    await users_db.set_user_field(A, "asset_value", 2_000_000)
    await income_service.claim(A, income_service.BANK)
    await income_service.claim(A, income_service.ASSET)
    await _rewind_last(A, income_service.BANK, 2)
    bank_r = await income_service.claim(A, income_service.BANK)
    asset_r = await income_service.claim(A, income_service.ASSET)
    assert bank_r["days"] == 2
    assert asset_r["days"] == 0
