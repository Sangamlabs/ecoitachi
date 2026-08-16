"""Integration tests for per-system transaction taxes and the /track lookup.

Cover: payment tax, game win tax, asset buy/sell tax, stock buy/sell tax,
/track lookup by transaction id, and the /addtax configuration path.

Run with:  pytest tests/test_taxes.py -v
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("BOT_TOKEN", "123:testtoken")
os.environ.setdefault("MONGO_URI", "mongodb://127.0.0.1:27017")
os.environ.setdefault("MONGO_DB_NAME", "unoitachi_tests_taxes")
os.environ.setdefault("OWNER_ID", "1")
os.environ.setdefault("CATBOX_ENABLED", "false")

from database import users as users_db  # noqa: E402
from database.mongo import mongo  # noqa: E402
from services import assets as asset_service  # noqa: E402
from services import economy, game_engine, settings as settings_service  # noqa: E402
from services import stocks as stocks_service  # noqa: E402
from services import tax as tax_service, transaction as tx_service  # noqa: E402
from utils.cooldown import cooldown_manager  # noqa: E402

A, B, ADMIN = 9401, 9402, 1

LAPT_PRICE = 500_000_000  # ₹5,000,000


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
    await db.tax_pool.delete_many({})
    await db.tax_distributions.delete_many({})
    await db.settings.delete_many({})
    await db.assets.delete_many({})
    await db.asset_holdings.delete_many({})
    await db.asset_price_history.delete_many({})
    await db.asset_admin_log.delete_many({})
    await settings_service.ensure_indexes()
    await settings_service.update_settings(starting_balance=0)
    await users_db.get_or_create_user(A, "user_a", "User A")
    await users_db.get_or_create_user(B, "user_b", "User B")
    await asset_service.ensure_market()
    yield
    await mongo.close()


async def _fund(user_id: int, amount: int) -> None:
    await economy.admin_give(user_id, amount, ADMIN)


async def _pool() -> int:
    return await tax_service.get_pool_size()


async def test_payment_tax_charged_to_sender():
    await settings_service.update_system_taxes(payments=1.0)
    await _fund(A, 1_000_000)
    result = await economy.transfer(A, B, 100_000, tax=await tax_service.system_tax_amount("payments", 100_000))
    assert result["tax"] == 1_000
    assert result["total"] == 101_000
    assert (await economy.get_balance(A))["wallet"] == 1_000_000 - 101_000
    assert (await economy.get_balance(B))["wallet"] == 100_000
    assert await _pool() == 1_000


async def test_payment_tax_zero_by_default():
    await _fund(A, 1_000_000)
    result = await economy.transfer(A, B, 100_000, tax=await tax_service.system_tax_amount("payments", 100_000))
    assert result["tax"] == 0
    assert (await economy.get_balance(A))["wallet"] == 900_000
    assert await _pool() == 0


async def test_game_win_tax():
    await settings_service.update_system_taxes(fly=5.0)
    await cooldown_manager.clear("fly", A)
    await _fund(A, 5_000_000)
    before = (await economy.get_balance(A))["wallet"]
    outcome = await game_engine.instant_game(A, "fly", 1_000, won=True, payout=200_000, multiplier=2.0)
    assert outcome["won"] is True
    after = (await economy.get_balance(A))["wallet"]
    assert after - before == 200_000 - 10_000 - 1_000  # payout − tax − bet
    assert await _pool() == 10_000
    txs = await tx_service.get_recent(A, 10)
    win_tx = next((t for t in txs if t["type"] == tx_service.GAME_WIN), None)
    assert win_tx is not None
    assert win_tx["metadata"]["tax"] == 10_000
    assert win_tx["metadata"]["gross_payout"] == 200_000


async def test_asset_buy_sell_tax():
    await settings_service.update_system_taxes(assets=2.0)
    await _fund(A, 700_000_000)
    buy = await asset_service.buy(A, "LAPT", "1")
    assert buy["cost"] == LAPT_PRICE
    assert buy["tax"] == int(LAPT_PRICE * 2.0) // 100
    assert buy["total"] == LAPT_PRICE + buy["tax"]
    assert (await economy.get_balance(A))["wallet"] == 700_000_000 - buy["total"]
    assert await _pool() == buy["tax"]

    sell = await asset_service.sell(A, "LAPT", "1")
    assert sell["received"] == LAPT_PRICE - sell["tax"]
    assert sell["tax"] == int(LAPT_PRICE * 2.0) // 100
    assert await _pool() == buy["tax"] + sell["tax"]
    txs = await tx_service.get_recent(A, 10)
    sell_tx = next((t for t in txs if t["type"] == tx_service.ASSET_SELL), None)
    assert sell_tx is not None
    assert sell_tx["metadata"]["tax"] == sell["tax"]


async def test_stock_buy_sell_tax():
    await settings_service.update_system_taxes(stocks=1.5)
    await stocks_service.ensure_market()
    await stocks_service.update_market_prices()
    await _fund(A, 10_000_000)
    buy = await stocks_service.buy_stock(A, "BTC", "0.01")
    tax = await tax_service.system_tax_amount("stocks", buy["cost"])
    assert buy["tax"] == tax
    assert (await economy.get_balance(A))["wallet"] == 10_000_000 - buy["total"]
    assert await _pool() == tax

    sell = await stocks_service.sell_stock(A, "BTC", "0.01")
    assert sell["tax"] == await tax_service.system_tax_amount("stocks", sell["value"])
    assert sell["received"] == sell["value"] - sell["tax"]
    assert await _pool() == tax + sell["tax"]


async def test_track_transaction_by_id():
    await _fund(A, 1_000_000)
    tx_id = await tx_service.record(
        user_id=A,
        ttype=tx_service.PAY,
        amount=5_000,
        balance_before=1_000_000,
        balance_after=995_000,
        metadata={"receiver": B, "direction": "out", "tax": 0},
    )
    doc = await tx_service.get_by_id(tx_id)
    assert doc is not None
    assert doc["type"] == tx_service.PAY
    assert doc["amount"] == 5_000
    assert doc["metadata"]["receiver"] == B
    assert await tx_service.get_by_id("does-not-exist") is None


async def test_addtax_config_path():
    await settings_service.update_system_taxes(mines=7.5)
    config = await settings_service.get_system_taxes()
    assert config["mines"] == 7.5
    assert config["assets"] == 0.0
    await settings_service.update_system_taxes(assets=3.0, bet=2.0)
    config = await settings_service.get_system_taxes()
    assert config["assets"] == 3.0
    assert config["bet"] == 2.0
    assert config["mines"] == 7.5
