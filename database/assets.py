"""Assets Market data access layer.

Collections:

- ``assets``             — asset definitions (admin-created market types)
- ``asset_price_history``— periodic price snapshots for the market engine
- ``asset_admin_log``    — audit trail for every admin asset operation
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from database.mongo import mongo

ASSETS = "assets"
PRICE_HISTORY = "asset_price_history"
ADMIN_LOG = "asset_admin_log"

CATEGORIES: tuple[str, ...] = (
    "REAL_ESTATE",
    "VEHICLE",
    "BUSINESS",
    "GOLD",
    "BOND",
    "COMMODITY",
    "LUXURY",
    "COLLECTIBLE",
    "DIGITAL",
    "OTHER",
)

DEFAULT_ASSETS: list[dict[str, Any]] = [
    {
        "symbol": "LAPT",
        "name": "Luxury Apartment",
        "category": "REAL_ESTATE",
        "description": "A premium apartment in the city centre.",
        "emoji": "🏠",
        "base_price": 500_000_000,  # ₹5,000,000
        "volatility": 0.01,
    },
    {
        "symbol": "M5",
        "name": "BMW M5",
        "category": "VEHICLE",
        "description": "A high-performance luxury sports sedan.",
        "emoji": "🚗",
        "base_price": 250_000_000,  # ₹2,500,000
        "volatility": 0.02,
    },
    {
        "symbol": "GOLD",
        "name": "Gold Reserve",
        "category": "GOLD",
        "description": "Investment-grade physical gold, tradable in fractions.",
        "emoji": "🥇",
        "base_price": 7_500_000,  # ₹75,000
        "volatility": 0.015,
        "allow_fractional": True,
        "min_quantity": 0.01,
        "quantity_step": 0.01,
    },
    {
        "symbol": "OIL",
        "name": "Crude Oil",
        "category": "COMMODITY",
        "description": "Benchmark crude oil futures exposure.",
        "emoji": "🛢️",
        "base_price": 450_000,  # ₹4,500
        "volatility": 0.03,
        "allow_fractional": True,
        "min_quantity": 0.1,
        "quantity_step": 0.1,
    },
    {
        "symbol": "BOND",
        "name": "Government Bond",
        "category": "BOND",
        "description": "A low-volatility sovereign bond.",
        "emoji": "📜",
        "base_price": 1_000_000,  # ₹10,000
        "volatility": 0.005,
    },
    {
        "symbol": "DIAMOND",
        "name": "Rare Diamond",
        "category": "LUXURY",
        "description": "A one-of-a-kind certified rare diamond.",
        "emoji": "💎",
        "base_price": 20_000_000,  # ₹200,000
        "volatility": 0.08,
    },
]

DEFAULT_MARKET: dict[str, Any] = {
    "allow_fractional": False,
    "min_quantity": 1.0,
    "max_quantity": None,
    "quantity_step": 1.0,
    "max_holding": None,
}


def new_asset_id() -> str:
    return f"AST-{uuid.uuid4().hex[:8].upper()}"


async def ensure_indexes() -> None:
    await mongo.db[ASSETS].create_index("symbol", unique=True)
    await mongo.db[ASSETS].create_index("category")
    await mongo.db[ASSETS].create_index("is_active")
    await mongo.db[PRICE_HISTORY].create_index([("symbol", 1), ("timestamp", -1)])
    await mongo.db[PRICE_HISTORY].create_index("timestamp")
    await mongo.db[ADMIN_LOG].create_index([("timestamp", -1)])


async def ensure_default_assets() -> None:
    now = int(time.time())
    for asset in DEFAULT_ASSETS:
        existing = await mongo.db[ASSETS].find_one({"symbol": asset["symbol"]})
        if existing is not None:
            continue
        doc = {
            **DEFAULT_MARKET,
            **asset,
            "asset_id": new_asset_id(),
            "price": asset["base_price"],
            "open_price": asset["base_price"],
            "high_price": asset["base_price"],
            "low_price": asset["base_price"],
            "change": 0,
            "change_percent": 0.0,
            "volume": 0,
            "is_active": True,
            "is_tradeable": True,
            "created_at": now,
            "updated_at": now,
        }
        await mongo.db[ASSETS].update_one(
            {"symbol": asset["symbol"]},
            {"$setOnInsert": doc},
            upsert=True,
        )


async def get_asset(symbol: str) -> dict[str, Any] | None:
    return await mongo.db[ASSETS].find_one({"symbol": symbol.upper()})


async def get_asset_by_id(asset_id: str) -> dict[str, Any] | None:
    return await mongo.db[ASSETS].find_one({"asset_id": asset_id})


async def active_asset(symbol: str) -> dict[str, Any] | None:
    return await mongo.db[ASSETS].find_one({"symbol": symbol.upper(), "is_active": True})


async def list_assets(
    *,
    active_only: bool = True,
    category: str | None = None,
    search: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {}
    if active_only:
        query["is_active"] = True
    if category:
        query["category"] = category.upper()
    if search:
        query["$or"] = [
            {"symbol": {"$regex": search, "$options": "i"}},
            {"name": {"$regex": search, "$options": "i"}},
            {"category": {"$regex": search, "$options": "i"}},
        ]
    cursor = (
        mongo.db[ASSETS]
        .find(query)
        .sort([("symbol", 1)])
        .skip(offset)
        .limit(limit)
    )
    return [doc async for doc in cursor]


async def count_assets(
    *, active_only: bool = True, category: str | None = None
) -> int:
    query: dict[str, Any] = {}
    if active_only:
        query["is_active"] = True
    if category:
        query["category"] = category.upper()
    return await mongo.db[ASSETS].count_documents(query)


async def insert_asset(doc: dict[str, Any]) -> None:
    await mongo.db[ASSETS].insert_one(doc)


async def update_asset(symbol: str, fields: dict[str, Any]) -> None:
    await mongo.db[ASSETS].update_one(
        {"symbol": symbol.upper()},
        {"$set": {**fields, "updated_at": int(time.time())}},
    )


async def insert_price_history(symbol: str, price: int, now: int) -> None:
    await mongo.db[PRICE_HISTORY].insert_one(
        {"symbol": symbol, "price": price, "timestamp": now}
    )


async def prune_price_history(symbol: str, keep: int) -> None:
    cursor = (
        mongo.db[PRICE_HISTORY]
        .find({"symbol": symbol})
        .sort("timestamp", -1)
        .skip(keep)
        .limit(1)
    )
    oldest_kept = await cursor.to_list(1)
    if not oldest_kept:
        return
    await mongo.db[PRICE_HISTORY].delete_many(
        {"symbol": symbol, "timestamp": {"$lt": oldest_kept[0]["timestamp"]}}
    )


async def insert_admin_log(doc: dict[str, Any]) -> None:
    await mongo.db[ADMIN_LOG].insert_one(doc)


async def recent_admin_logs(limit: int = 20) -> list[dict[str, Any]]:
    cursor = mongo.db[ADMIN_LOG].find({}).sort("timestamp", -1).limit(limit)
    return [doc async for doc in cursor]
