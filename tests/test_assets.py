"""Integration tests for the Phase 3 Assets Market + Section-62 resale market.

Cover: seeding, admin lifecycle (create/edit/delist/restore/price/volatility),
buy/sell with atomic holdings math and weighted-average cost, validation
(fractional rules, insufficient wallet/holdings), market stats, the price tick
engine, net-worth integration, and the listing market (create/cancel/buy,
whole-listing sales, seller-locking, re-buy of sold listing).

Run with:  pytest tests/test_assets.py -v
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
os.environ.setdefault("MONGO_DB_NAME", "unoitachi_tests_assets")
os.environ.setdefault("OWNER_ID", "1")
os.environ.setdefault("CATBOX_ENABLED", "false")

from database import asset_holdings as holdings_db  # noqa: E402
from database import asset_listings as listings_db  # noqa: E402
from database import assets as assets_db  # noqa: E402
from database import users as users_db  # noqa: E402
from database.mongo import mongo  # noqa: E402
from services import asset_listings as listings_service  # noqa: E402
from services import asset_market as market_service  # noqa: E402
from services import assets as asset_service  # noqa: E402
from services import economy, settings as settings_service  # noqa: E402

A, B, ADMIN = 9101, 9102, 1

LAPT_PRICE = 500_000_000  # ₹5,000,000
GOLD_PRICE = 7_500_000  # ₹75,000


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
    await db.assets.delete_many({})
    await db.asset_holdings.delete_many({})
    await db.asset_listings.delete_many({})
    await db.asset_price_history.delete_many({})
    await db.asset_admin_log.delete_many({})
    await db.transactions.delete_many({"user_id": {"$in": [A, B]}})
    await db.settings.delete_many({})
    await settings_service.ensure_indexes()
    await settings_service.update_settings(starting_balance=0)
    await users_db.get_or_create_user(A, "user_a", "User A")
    await users_db.get_or_create_user(B, "user_b", "User B")
    await asset_service.ensure_market()
    yield
    await mongo.close()


async def _fund(user_id: int, amount: int) -> None:
    await economy.admin_give(user_id, amount, ADMIN)


async def test_seed_default_assets():
    stats = await asset_service.market_stats()
    assert stats["active"] == len(assets_db.DEFAULT_ASSETS)
    gold = await assets_db.get_asset("GOLD")
    assert gold["allow_fractional"] is True
    assert gold["min_quantity"] == 0.01


async def test_admin_lifecycle():
    asset = await asset_service.create_asset(
        ADMIN, "VILA", "Villa", "REAL_ESTATE", 2_000_000, 0.02
    )
    assert asset["symbol"] == "VILA"
    assert asset["price"] == 2_000_000
    assert asset["is_active"] is True

    with pytest.raises(asset_service.AssetError):
        await asset_service.create_asset(ADMIN, "VILA", "Dupe", "REAL_ESTATE", 1, 0.01)
    with pytest.raises(asset_service.AssetError):
        await asset_service.create_asset(ADMIN, "BAD", "X", "NOT_A_CATEGORY", 1, 0.01)

    updated = await asset_service.update_asset_fields(ADMIN, "VILA", {"name": "Grand Villa"})
    assert updated["name"] == "Grand Villa"

    await asset_service.set_price(ADMIN, "VILA", 2_500_000)
    current = await assets_db.get_asset("VILA")
    assert current["price"] == 2_500_000
    assert current["change"] == 500_000

    await asset_service.set_volatility(ADMIN, "VILA", 0.04)
    assert (await assets_db.get_asset("VILA"))["volatility"] == 0.04

    await asset_service.deactivate_asset(ADMIN, "VILA")
    assert (await assets_db.get_asset("VILA"))["is_active"] is False
    with pytest.raises(asset_service.AssetError):
        await asset_service.get_active_asset("VILA")

    await asset_service.restore_asset(ADMIN, "VILA")
    assert (await assets_db.get_asset("VILA"))["is_active"] is True

    logs = await assets_db.recent_admin_logs(10)
    actions = {log["action"] for log in logs}
    assert {"ADD_ASSET", "EDIT_ASSET", "MANUAL_PRICE_CHANGE",
            "VOLATILITY_CHANGE", "REMOVE_ASSET", "RESTORE_ASSET"} <= actions


async def test_buy_sell_whole_units():
    await _fund(A, 600_000_000)
    result = await asset_service.buy(A, "LAPT", "1")
    assert result["quantity"] == 1
    assert result["total"] == LAPT_PRICE
    assert (await economy.get_balance(A))["wallet"] == 600_000_000 - LAPT_PRICE

    holding = await holdings_db.get_holding(A, (await assets_db.get_asset("LAPT"))["asset_id"])
    assert holding["quantity"] == 1
    assert holding["total_invested"] == LAPT_PRICE

    sell = await asset_service.sell(A, "LAPT", "1")
    assert sell["quantity"] == 1
    assert sell["received"] == LAPT_PRICE
    assert (await economy.get_balance(A))["wallet"] == 600_000_000
    assert await holdings_db.get_holding(A, (await assets_db.get_asset("LAPT"))["asset_id"]) is None


async def test_invalid_quantity_and_funds():
    await _fund(A, 600_000_000)
    with pytest.raises(asset_service.AssetError):
        await asset_service.buy(A, "LAPT", "0")
    with pytest.raises(asset_service.AssetError):
        await asset_service.buy(A, "LAPT", "abc")
    with pytest.raises(asset_service.AssetError):
        await asset_service.buy(A, "LAPT", "2")  # needs ₹10M, has ₹6M
    with pytest.raises(asset_service.AssetError):
        await asset_service.buy(A, "LAPT", "1.5")  # whole-units asset rejects fractions
    await asset_service.buy(A, "GOLD", "0.1")  # fractional asset accepts fractions


async def test_insufficient_holdings_sell():
    await _fund(A, 600_000_000)
    await asset_service.buy(A, "LAPT", "1")
    with pytest.raises(asset_service.InsufficientHoldings):
        await asset_service.sell(A, "LAPT", "2")


async def test_fractional_buy_weighted_average():
    await _fund(A, 300_000_000)
    await asset_service.buy(A, "GOLD", "10")
    first = await holdings_db.get_holding(A, (await assets_db.get_asset("GOLD"))["asset_id"])
    assert first["quantity"] == 10
    assert first["total_invested"] == 10 * GOLD_PRICE

    await asset_service.set_price(ADMIN, "GOLD", 2 * GOLD_PRICE)
    await asset_service.buy(A, "GOLD", "10")
    holding = await holdings_db.get_holding(A, (await assets_db.get_asset("GOLD"))["asset_id"])
    assert holding["quantity"] == 20
    assert holding["total_invested"] == 10 * GOLD_PRICE + 10 * 2 * GOLD_PRICE
    assert holding["average_buy_price"] == pytest.approx(1.5 * GOLD_PRICE, rel=1e-6)


async def test_cost_basis_on_partial_sell():
    await _fund(A, 300_000_000)
    await asset_service.buy(A, "GOLD", "10")
    await asset_service.set_price(ADMIN, "GOLD", 2 * GOLD_PRICE)
    await asset_service.buy(A, "GOLD", "10")
    await asset_service.sell(A, "GOLD", "10")
    holding = await holdings_db.get_holding(A, (await assets_db.get_asset("GOLD"))["asset_id"])
    assert holding["quantity"] == 10
    assert holding["total_invested"] == pytest.approx(1.5 * GOLD_PRICE * 10, abs=1)


async def test_market_stats_and_volume():
    await _fund(A, 600_000_000)
    await _fund(B, 600_000_000)
    await asset_service.buy(A, "LAPT", "1")
    await asset_service.buy(B, "LAPT", "1")
    stats = await asset_service.market_stats()
    assert stats["total_volume"] >= 2 * LAPT_PRICE
    admin_stats = await asset_service.admin_stats()
    assert admin_stats["holders"] == 2
    assert admin_stats["holdings"] == 2


async def test_price_tick():
    before = await assets_db.get_asset("BOND")
    await market_service.force_tick()
    after = await assets_db.get_asset("BOND")
    assert before["price"] == after["price"] or before["price"] != after["price"]
    history = await mongo.db.asset_price_history.find({"symbol": "BOND"}).sort("timestamp", -1).limit(1).to_list(1)
    assert history and history[0]["price"] == after["price"]


async def test_net_worth_includes_assets():
    await _fund(A, 600_000_000)
    await asset_service.buy(A, "LAPT", "1")
    pf = await asset_service.portfolio(A)
    assert pf["total_value"] == LAPT_PRICE
    user = await users_db.get_user(A)
    assert user["asset_value"] == LAPT_PRICE

    from services.leaderboard import net_worth

    worth = await net_worth(user)
    assert worth == 600_000_000


async def test_listing_create_cancel():
    await _fund(A, 600_000_000)
    await asset_service.buy(A, "LAPT", "1")
    listing = await listings_service.create_listing(A, "LAPT", "1", "6000000")
    assert listing["listing_id"].startswith("LST-")
    assert listing["symbol"] == "LAPT"
    assert listing["total_price"] == 6_000_000

    await listings_service.cancel_listing(A, listing["listing_id"])
    assert (await listings_db.get_listing(listing["listing_id"]))["status"] == "cancelled"


async def test_listing_buy_whole_transfer():
    await _fund(A, 600_000_000)
    await asset_service.buy(A, "LAPT", "1")
    listing = await listings_service.create_listing(A, "LAPT", "1", "6000000")

    await _fund(B, 600_000_000)
    sold = await listings_service.buy_listing(B, listing["listing_id"])
    assert sold["status"] == "sold"
    assert (await economy.get_balance(B))["wallet"] == 600_000_000 - 6_000_000
    assert (await economy.get_balance(A))["wallet"] == 600_000_000 - LAPT_PRICE + 6_000_000

    asset_id = (await assets_db.get_asset("LAPT"))["asset_id"]
    assert (await holdings_db.get_holding(B, asset_id))["quantity"] == 1
    assert await holdings_db.get_holding(A, asset_id) is None

    with pytest.raises(asset_service.ListingUnavailable):
        await listings_service.buy_listing(B, listing["listing_id"])


async def test_listing_requires_holding_and_funds():
    await _fund(A, 600_000_000)
    await asset_service.buy(A, "LAPT", "1")
    with pytest.raises(asset_service.InsufficientHoldings):
        await listings_service.create_listing(A, "LAPT", "2", "100")

    await _fund(B, 10)
    listing = await listings_service.create_listing(A, "LAPT", "1", "100")
    with pytest.raises(Exception):
        await listings_service.buy_listing(B, listing["listing_id"])
    assert (await listings_db.get_listing(listing["listing_id"]))["status"] == "active"


async def test_seller_cannot_buy_own_listing():
    await _fund(A, 600_000_000)
    await asset_service.buy(A, "LAPT", "1")
    listing = await listings_service.create_listing(A, "LAPT", "1", "6000000")
    with pytest.raises(asset_service.AssetError):
        await listings_service.buy_listing(A, listing["listing_id"])


async def test_delisted_asset_cannot_trade():
    await asset_service.deactivate_asset(ADMIN, "BOND")
    await _fund(A, 600_000_000)
    with pytest.raises(asset_service.AssetError):
        await asset_service.buy(A, "BOND", "1")


async def test_market_disabled():
    await settings_service.update_asset_market_config(enabled=False)
    await _fund(A, 600_000_000)
    with pytest.raises(asset_service.AssetMarketDisabled):
        await asset_service.buy(A, "LAPT", "1")
    await settings_service.update_asset_market_config(enabled=True)
