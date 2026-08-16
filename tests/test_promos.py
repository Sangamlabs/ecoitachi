"""Integration tests for the promo code system.

Covers parsing/normalization, the atomic redemption flow (currency, multi-reward
bundles with stocks + assets), per-user uniqueness, total limits, expiry,
admin lifecycle (create/edit/disable), stats, the in-memory detector cache and
safe rollback when a referenced asset becomes unavailable.

Run with:  pytest tests/test_promos.py -v
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
os.environ.setdefault("MONGO_DB_NAME", "unoitachi_tests_promos")
os.environ.setdefault("OWNER_ID", "1")
os.environ.setdefault("CATBOX_ENABLED", "false")

from database import promos as promos_db  # noqa: E402
from database import transactions as tx_db  # noqa: E402
from database import users as users_db  # noqa: E402
from database.mongo import mongo  # noqa: E402
from services import assets as asset_service  # noqa: E402
from services import economy, settings as settings_service  # noqa: E402
from services import stocks as stock_service  # noqa: E402
from services import transaction as tx_service  # noqa: E402
from services import promos as promo_service  # noqa: E402
from services.promo_rewards import PromoRewardError, describe_reward, parse_reward_tokens  # noqa: E402

A, B, C, ADMIN = 9701, 9702, 9703, 1
USERS = (A, B, C)


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
    await db.users.delete_many({"user_id": {"$in": list(USERS)}})
    await db.transactions.delete_many({"user_id": {"$in": list(USERS)}})
    await db.promo_codes.delete_many({})
    await db.promo_redemptions.delete_many({})
    await db.stocks.delete_many({})
    await db.stock_holdings.delete_many({})
    await db.assets.delete_many({})
    await db.asset_holdings.delete_many({})
    await db.asset_listings.delete_many({})
    await db.asset_price_history.delete_many({})
    await db.asset_admin_log.delete_many({})
    await db.settings.delete_many({})
    await settings_service.ensure_indexes()
    await promos_db.ensure_indexes()
    await settings_service.update_settings(starting_balance=0)
    for user in USERS:
        await users_db.get_or_create_user(user)
    await asset_service.ensure_market()
    promo_service.cache.invalidate()
    yield
    await mongo.close()


async def _seed_stock() -> str:
    await stock_service.add_asset("BTC", "Bitcoin", 100_000, 0.1)
    return "BTC"


async def _seed_asset() -> str:
    asset = await asset_service.create_asset(
        ADMIN, "VILA", "Villa", "REAL_ESTATE", 2_000_000, 0.02
    )
    return asset["asset_id"]


async def _tx_metadata(user_id: int, ttype: str) -> list[dict]:
    txs = await tx_db.recent_by_user(user_id, 50)
    return [t for t in txs if t["type"] == ttype]


# --------------------------------------------------------------------------- #
# Parsing (no DB needed beyond imports)
# --------------------------------------------------------------------------- #


def test_normalize_code():
    assert promo_service.normalize_code(" itachi500 ") == "ITACHI500"
    assert promo_service.normalize_code("a-b_c") == "ABC"
    with pytest.raises(promo_service.PromoInvalidArgument):
        promo_service.normalize_code("AB")
    with pytest.raises(promo_service.PromoInvalidArgument):
        promo_service.normalize_code("A" * 21)


def test_parse_duration():
    expires_at, label = promo_service.parse_duration("7days")
    assert label == "7 days"
    assert expires_at > int(time.time())

    lifetime, label = promo_service.parse_duration("lifetime")
    assert lifetime is None
    assert label == "Lifetime"

    with pytest.raises(promo_service.PromoInvalidArgument):
        promo_service.parse_duration("banana")


def test_parse_limit():
    assert promo_service.parse_limit("100") == 100
    assert promo_service.parse_limit("unlimited") is None
    assert promo_service.parse_limit("0") is None
    with pytest.raises(promo_service.PromoInvalidArgument):
        promo_service.parse_limit("lots")


def test_parse_reward_tokens():
    rewards = parse_reward_tokens(["rs:500", "stock:BTC:0.01", "asset:AST-00021:1"])
    assert rewards[0] == {"type": "currency", "amount": 50_000}
    assert rewards[1]["symbol"] == "BTC"
    assert rewards[2]["asset_id"] == "AST-00021"
    with pytest.raises(PromoRewardError):
        parse_reward_tokens(["bitcoin:10"])
    with pytest.raises(PromoRewardError):
        parse_reward_tokens(["rs:-5"])


def test_describe_reward():
    assert describe_reward({"type": "currency", "amount": 50_000}) == "💰 ₹500"
    assert describe_reward({"type": "stock", "symbol": "BTC", "quantity": 0.01}) == "📈 BTC × 0.01"


# --------------------------------------------------------------------------- #
# Redemption flow
# --------------------------------------------------------------------------- #


async def test_create_and_redeem_currency():
    await promo_service.create_promo(ADMIN, "ITACHI500", "lifetime", "unlimited", ["rs:500"])

    result = await promo_service.redeem(A, "ITACHI500")
    assert result["promo"]["code"] == "ITACHI500"
    assert result["granted"][0]["amount"] == 50_000

    bal = await economy.get_balance(A)
    assert bal["wallet"] == 50_000

    txs = await _tx_metadata(A, tx_service.PROMO_CURRENCY)
    assert len(txs) == 1
    assert txs[0]["metadata"]["promo_code"] == "ITACHI500"
    assert txs[0]["metadata"]["source"] == "PROMO"

    redemption = await promos_db.get_redemption(result["promo"]["_id"], A)
    assert redemption["status"] == "completed"

    with pytest.raises(promo_service.PromoAlreadyUsed):
        await promo_service.redeem(A, "itachi500")


async def test_per_user_unique():
    await promo_service.create_promo(ADMIN, "ONLYONCE", "lifetime", "unlimited", ["rs:10"])
    promo = await promos_db.get_promo_by_code("ONLYONCE")
    await promo_service.redeem(A, "ONLYONCE")
    await promo_service.redeem(B, "ONLYONCE")
    with pytest.raises(promo_service.PromoAlreadyUsed):
        await promo_service.redeem(A, "ONLYONCE")
    with pytest.raises(promo_service.PromoAlreadyUsed):
        await promo_service.redeem(B, "ONLYONCE")
    assert await promos_db.count_completed(promo["_id"]) == 2


async def test_multi_reward_bundle():
    await _seed_stock()
    asset_id = await _seed_asset()
    await promo_service.create_promo(
        ADMIN, "MEGA", "10min", "5", ["rs:250", "stock:BTC:0.01", f"asset:{asset_id}:1"]
    )

    await promo_service.redeem(A, "MEGA")

    bal = await economy.get_balance(A)
    assert bal["wallet"] == 25_000

    holding = await mongo.db.stock_holdings.find_one({"user_id": A, "symbol": "BTC"})
    assert holding["quantity"] == pytest.approx(0.01)

    asset_holding = await mongo.db.asset_holdings.find_one({"user_id": A, "asset_id": asset_id})
    assert asset_holding["quantity"] == pytest.approx(1)

    assert len(await _tx_metadata(A, tx_service.PROMO_CURRENCY)) == 1
    assert len(await _tx_metadata(A, tx_service.PROMO_STOCK)) == 1
    assert len(await _tx_metadata(A, tx_service.PROMO_ASSET)) == 1


async def test_total_limit():
    await promo_service.create_promo(ADMIN, "LIMITED", "lifetime", "2", ["rs:50"])
    await promo_service.redeem(A, "LIMITED")
    await promo_service.redeem(B, "LIMITED")
    with pytest.raises(promo_service.PromoLimitReached):
        await promo_service.redeem(C, "LIMITED")

    promo = await promos_db.get_promo_by_code("LIMITED")
    assert promo["redeemed_count"] == 2
    assert promo["max_redemptions"] == 2


async def test_rollback_on_unavailable_asset():
    await _seed_stock()
    asset_id = await _seed_asset()
    await promo_service.create_promo(
        ADMIN, "BROKEN", "lifetime", "unlimited",
        ["rs:100", "stock:BTC:0.01", f"asset:{asset_id}:1"],
    )
    await asset_service.deactivate_asset(ADMIN, "VILA")

    with pytest.raises(PromoRewardError):
        await promo_service.redeem(A, "BROKEN")

    bal = await economy.get_balance(A)
    assert bal["wallet"] == 0
    assert await mongo.db.stock_holdings.count_documents({"user_id": A}) == 0
    assert await mongo.db.asset_holdings.count_documents({"user_id": A}) == 0

    promo = await promos_db.get_promo_by_code("BROKEN")
    assert promo["redeemed_count"] == 0
    assert await promos_db.count_completed(promo["_id"]) == 0
    # failed redemption record was removed so the user may retry later
    assert await promos_db.get_redemption(promo["_id"], A) is None


async def test_expiry():
    await promo_service.create_promo(ADMIN, "SHORT", "10min", "unlimited", ["rs:10"])
    await promos_db.update_promo(
        (await promos_db.get_promo_by_code("SHORT"))["_id"],
        {"expires_at": int(time.time()) - 10},
    )
    with pytest.raises(promo_service.PromoExpired):
        await promo_service.redeem(A, "SHORT")

    handled = await promo_service.expire_overdue()
    assert handled == 1
    promo = await promos_db.get_promo_by_code("SHORT")
    assert promo["is_active"] is False


async def test_rmpromo():
    await promo_service.create_promo(ADMIN, "GONE", "lifetime", "unlimited", ["rs:10"])
    await promo_service.disable_promo(ADMIN, "GONE")
    with pytest.raises(promo_service.PromoInactive):
        await promo_service.redeem(A, "GONE")
    docs, _ = await promo_service.list_promos("inactive")
    assert any(d["code"] == "GONE" for d in docs)


async def test_edit_promo():
    await _seed_stock()
    await promo_service.create_promo(ADMIN, "EDITME", "lifetime", "unlimited", ["rs:10"])

    await promo_service.edit_promo(ADMIN, "EDITME", "limit", ["3"])
    assert (await promos_db.get_promo_by_code("EDITME"))["max_redemptions"] == 3

    await promo_service.edit_promo(ADMIN, "EDITME", "expiry", ["7days"])
    edited = await promos_db.get_promo_by_code("EDITME")
    assert edited["expiry_label"] == "7 days"

    await promo_service.edit_promo(ADMIN, "EDITME", "reward", ["rs:500", "stock:BTC:0.5"])
    edited = await promos_db.get_promo_by_code("EDITME")
    assert len(edited["rewards"]) == 2
    assert edited["rewards"][0]["amount"] == 50_000
    assert edited["rewards"][1]["symbol"] == "BTC"

    await promo_service.edit_promo(ADMIN, "EDITME", "active", ["off"])
    assert (await promos_db.get_promo_by_code("EDITME"))["is_active"] is False

    await promo_service.edit_promo(ADMIN, "EDITME", "active", ["on"])
    assert (await promos_db.get_promo_by_code("EDITME"))["is_active"] is True


async def test_stats():
    await _seed_stock()
    asset_id = await _seed_asset()
    await promo_service.create_promo(
        ADMIN, "STATS", "lifetime", "unlimited",
        ["rs:100", f"asset:{asset_id}:1"],
    )
    await promo_service.redeem(A, "STATS")
    await promo_service.redeem(B, "STATS")

    stats = await promo_service.get_promo_stats("STATS")
    assert stats["total_redemptions"] == 2
    assert stats["unique_users"] == 2
    assert stats["remaining"] is None
    assert stats["currency_total"] == 20_000
    assert stats["asset_rows"] == [(asset_id, 2.0)]


async def test_cache_candidates():
    await promo_service.create_promo(ADMIN, "CACHEME", "lifetime", "unlimited", ["rs:10"])
    await promo_service.create_promo(ADMIN, "CACHETWO", "10min", "unlimited", ["rs:10"])
    promo_service.cache.invalidate()
    found = await promo_service.cache.candidates(["CACHEME", "NOPE", "CACHETWO"])
    assert found == ["CACHEME", "CACHETWO"]

    await promo_service.disable_promo(ADMIN, "CACHEME")
    promo_service.cache.invalidate()
    found = await promo_service.cache.candidates(["CACHEME", "CACHETWO"])
    assert found == ["CACHETWO"]
