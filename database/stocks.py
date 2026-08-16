"""Stock market data access layer."""

from __future__ import annotations

from typing import Any

from database.mongo import mongo

ASSETS = "stocks"
HOLDINGS = "stock_holdings"
HISTORY = "stock_history"

DEFAULT_ASSETS = [
    {"symbol": "BTC", "name": "Bitcoin", "base_price": 8_450_000, "volatility": 0.03},
    {"symbol": "ETH", "name": "Ethereum", "base_price": 320_000, "volatility": 0.025},
    {"symbol": "RSX", "name": "RS Index", "base_price": 4_250, "volatility": 0.012},
    {"symbol": "SOL", "name": "Solana", "base_price": 15_000, "volatility": 0.02},
    {"symbol": "DOGE", "name": "Dogecoin", "base_price": 145, "volatility": 0.05},
]


async def ensure_indexes() -> None:
    await mongo.db[ASSETS].create_index("symbol", unique=True)
    await mongo.db[HOLDINGS].create_index([("user_id", 1), ("symbol", 1)], unique=True)
    await mongo.db[HISTORY].create_index([("symbol", 1), ("at", -1)])


async def ensure_default_assets() -> None:
    now = int(__import__("time").time())
    for asset in DEFAULT_ASSETS:
        await mongo.db[ASSETS].update_one(
            {"symbol": asset["symbol"]},
            {
                "$setOnInsert": {
                    **asset,
                    "price": asset["base_price"],
                    "open_price": asset["base_price"],
                    "high_price": asset["base_price"],
                    "low_price": asset["base_price"],
                    "volume": 0,
                    "change": 0,
                    "change_percent": 0.0,
                    "is_active": True,
                    "created_at": now,
                    "updated_at": now,
                }
            },
            upsert=True,
        )


async def get_asset(symbol: str) -> dict[str, Any] | None:
    return await mongo.db[ASSETS].find_one({"symbol": symbol.upper(), "is_active": True})


async def list_active_assets() -> list[dict[str, Any]]:
    cursor = mongo.db[ASSETS].find({"is_active": True}).sort("symbol", 1)
    return [doc async for doc in cursor]


async def get_all_assets() -> list[dict[str, Any]]:
    cursor = mongo.db[ASSETS].find({}).sort("symbol", 1)
    return [doc async for doc in cursor]


async def update_price(
    symbol: str,
    price: int,
    change: int,
    change_percent: float,
    high: int,
    low: int,
    now: int,
) -> None:
    await mongo.db[ASSETS].update_one(
        {"symbol": symbol},
        {
            "$set": {
                "price": price,
                "change": change,
                "change_percent": change_percent,
                "high_price": high,
                "low_price": low,
                "updated_at": now,
            },
            "$inc": {"volume": 1},
        },
    )


async def insert_history(symbol: str, price: int, now: int) -> None:
    await mongo.db[HISTORY].insert_one({"symbol": symbol, "price": price, "at": now})
    old = await mongo.db[HISTORY].find({"symbol": symbol}).sort("at", -1).skip(500).to_list(1)
    if old:
        await mongo.db[HISTORY].delete_many({"symbol": symbol, "at": {"$lt": old[0]["at"]}})


async def get_price_history(symbol: str, limit: int = 30) -> list[dict[str, Any]]:
    cursor = mongo.db[HISTORY].find({"symbol": symbol}).sort("at", -1).limit(limit)
    return [doc async for doc in cursor]


async def get_holding(user_id: int, symbol: str) -> dict[str, Any] | None:
    return await mongo.db[HOLDINGS].find_one({"user_id": user_id, "symbol": symbol.upper()})


async def add_holding(user_id: int, symbol: str, quantity: float) -> None:
    await mongo.db[HOLDINGS].update_one(
        {"user_id": user_id, "symbol": symbol.upper()},
        {"$inc": {"quantity": quantity}},
        upsert=True,
    )


async def remove_holding(user_id: int, symbol: str, quantity: float) -> bool:
    """Remove quantity; returns False if the user does not own enough."""
    result = await mongo.db[HOLDINGS].update_one(
        {"user_id": user_id, "symbol": symbol.upper(), "quantity": {"$gte": quantity}},
        {"$inc": {"quantity": -quantity}},
    )
    if result.modified_count == 0:
        return False
    holding = await mongo.db[HOLDINGS].find_one(
        {"user_id": user_id, "symbol": symbol.upper()}
    )
    if holding and holding["quantity"] <= 1e-12:
        await mongo.db[HOLDINGS].delete_one({"user_id": user_id, "symbol": symbol.upper()})
    return True


async def get_user_holdings(user_id: int) -> list[dict[str, Any]]:
    cursor = mongo.db[HOLDINGS].find({"user_id": user_id, "quantity": {"$gt": 1e-12}})
    return [doc async for doc in cursor]


async def all_holdings() -> list[dict[str, Any]]:
    cursor = mongo.db[HOLDINGS].find({"quantity": {"$gt": 1e-12}})
    return [doc async for doc in cursor]
