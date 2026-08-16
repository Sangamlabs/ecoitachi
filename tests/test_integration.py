"""Integration tests that require a running MongoDB.

These are skipped automatically when MongoDB is not reachable.  They exercise
the real economy engine end-to-end: transfer, deposit, withdraw, interest,
tax distribution, stocks and games.

Run with:  pytest tests/test_integration.py -v
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
os.environ.setdefault("MONGO_DB_NAME", "rs_economy_tests")
os.environ.setdefault("OWNER_ID", "1")
os.environ.setdefault("CATBOX_ENABLED", "false")

from database import users as users_db  # noqa: E402
from database.mongo import mongo  # noqa: E402
from games import mines as mines_game  # noqa: E402
from services import (  # noqa: E402
    bank,
    economy,
    interest,
    stocks,
    tax,
)
from services import game_engine  # noqa: E402
from utils.cooldown import cooldown_manager  # noqa: E402

A, B = 9101, 9102


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
    await db["game_sessions"].delete_many({})
    await db["game_cooldowns"].delete_many({})
    await db["tax_pool"].delete_many({})
    await db["tax_distributions"].delete_many({})
    await db["transactions"].delete_many({"user_id": {"$in": [A, B]}})
    await db["stocks"].delete_many({})
    await db["stock_holdings"].delete_many({})
    await users_db.get_or_create_user(A, "user_a", "User A")
    await users_db.get_or_create_user(B, "user_b", "User B")
    yield
    await mongo.close()


async def test_user_creation_and_give():
    await users_db.get_or_create_user(A, "user_a", "User A")
    await economy.admin_give(A, 5000, 1)
    bal = await economy.get_balance(A)
    assert bal["wallet"] == 5000


async def test_pay():
    await economy.admin_give(A, 50_000, 1)
    result = await economy.transfer(A, B, 25_000)
    assert (await economy.get_balance(A))["wallet"] == 25_000
    assert (await economy.get_balance(B))["wallet"] == 25_000
    assert result["amount"] == 25_000


async def test_pay_self_rejected():
    await economy.admin_give(A, 50_000, 1)
    with pytest.raises(economy.EconomyError):
        await economy.transfer(A, A, 100)


async def test_insufficient_balance():
    with pytest.raises(economy.InsufficientBalance):
        await economy.remove_wallet(A, 100)


async def test_concurrent_double_spend_blocked():
    await economy.admin_give(A, 1_000_000, 1)
    results = await asyncio.gather(
        economy.remove_wallet(A, 600_000, spend=True),
        economy.remove_wallet(A, 600_000, spend=True),
        return_exceptions=True,
    )
    assert any(isinstance(r, economy.InsufficientBalance) for r in results)
    assert (await economy.get_balance(A))["wallet"] == 400_000


async def test_deposit_withdraw_and_tax():
    await economy.admin_give(A, 1_000_000, 1)
    await bank.deposit(A, 500_000)
    bal = await economy.get_balance(A)
    assert bal["wallet"] == 500_000 and bal["bank"] == 500_000

    settings = await bank.get_bank_settings()
    rate = float(settings["withdrawal_tax_rate"])
    wd = await bank.withdraw(A, 100_000)
    assert wd["tax"] == int(100_000 * rate / 100)
    assert wd["received"] == 100_000 - wd["tax"]
    assert (await tax.get_pool_size()) > 0


async def test_interest_idempotent():
    await economy.admin_give(A, 1_000_000, 1)
    await bank.deposit(A, 1_000_000)
    now = int(time.time())
    paid1 = await interest.process_due_interest(now + 86_400 + 10)
    paid2 = await interest.process_due_interest(now + 86_400 + 20)
    assert any(p["user_id"] == A for p in paid1)
    assert all(p["user_id"] != A for p in paid2)


async def test_stock_buy_sell_and_portfolio():
    await stocks.ensure_market()
    await stocks.update_market_prices()
    await economy.admin_give(A, 10_000_000, 1)
    buy = await stocks.buy_stock(A, "BTC", "0.01")
    assert buy["cost"] > 0
    pf = await stocks.portfolio(A)
    assert any(r["symbol"] == "BTC" for r in pf["rows"])
    sell = await stocks.sell_stock(A, "BTC", "0.005")
    assert sell["value"] > 0


async def test_stock_sell_over_owned_rejected():
    await stocks.ensure_market()
    await economy.admin_give(A, 10_000_000, 1)
    with pytest.raises(economy.EconomyError):
        await stocks.sell_stock(A, "BTC", "999")


async def test_game_engine_cooldown():
    await economy.admin_give(A, 1_000_000, 1)
    await cooldown_manager.clear("fly", A)
    await game_engine.instant_game(A, "fly", 1_000, won=True, payout=2_000, multiplier=2.0)
    with pytest.raises(game_engine.GameCooldownError):
        await game_engine.instant_game(A, "fly", 1_000, won=True, payout=2_000, multiplier=2.0)
    await cooldown_manager.clear("fly", A)


async def test_game_bet_validation_zero():
    await economy.admin_give(A, 1_000, 1)
    with pytest.raises((economy.MoneyError, ValueError)):
        await game_engine.instant_game(A, "fly", 0, won=True, payout=0, multiplier=1.0)


async def test_mines_full_round_and_no_double_settle():
    await economy.admin_give(A, 5_000_000, 1)
    sid, state = await mines_game.start(A, 5_000)
    safe_tiles = [t for t in range(36) if t not in state["mines"]]
    for tile in safe_tiles[:3]:
        await mines_game.reveal(sid, A, tile)
    result = await mines_game.cashout(sid, A)
    assert result["won"] is True and result["payout"] > 0
    with pytest.raises(game_engine.NoActiveGame):
        await mines_game.cashout(sid, A)


async def test_tax_distribution_idempotent():
    await users_db.get_or_create_user(A, "user_a", "User A")
    await economy.admin_give(A, 1_000_000, 1)
    await bank.deposit(A, 500_000)
    await bank.withdraw(A, 100_000)
    now = int(time.time())
    dist = await tax.distribute_monthly(now=now + 40 * 86_400)
    assert dist is not None and dist["distributed"] > 0
    assert await tax.distribute_monthly(now=now + 40 * 86_400) is None
