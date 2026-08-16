"""User asset listing (resale) market data access layer.

A LISTING is one user's offer to sell a quantity of an ASSET they own.  Every
listing has its own globally unique ``listing_id`` — the asset's ``asset_id``
is NEVER reused as a listing identifier.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from database.mongo import mongo

LISTINGS = "asset_listings"

STATUS_ACTIVE = "active"
STATUS_PENDING = "pending"
STATUS_SOLD = "sold"
STATUS_CANCELLED = "cancelled"


def new_listing_id() -> str:
    return f"LST-{uuid.uuid4().hex[:8].upper()}"


async def ensure_indexes() -> None:
    await mongo.db[LISTINGS].create_index("listing_id", unique=True)
    await mongo.db[LISTINGS].create_index([("status", 1), ("created_at", -1)])
    await mongo.db[LISTINGS].create_index([("symbol", 1), ("status", 1)])
    await mongo.db[LISTINGS].create_index([("seller_user_id", 1), ("status", 1)])


async def insert_listing(doc: dict[str, Any]) -> str:
    await mongo.db[LISTINGS].insert_one(doc)
    return doc["listing_id"]


async def get_listing(listing_id: str) -> dict[str, Any] | None:
    return await mongo.db[LISTINGS].find_one({"listing_id": listing_id})


async def list_active(
    symbol: str | None = None, limit: int = 10, offset: int = 0
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"status": STATUS_ACTIVE}
    if symbol:
        query["symbol"] = symbol.upper()
    cursor = (
        mongo.db[LISTINGS]
        .find(query)
        .sort("created_at", -1)
        .skip(offset)
        .limit(limit)
    )
    return [doc async for doc in cursor]


async def count_active(symbol: str | None = None) -> int:
    query: dict[str, Any] = {"status": STATUS_ACTIVE}
    if symbol:
        query["symbol"] = symbol.upper()
    return await mongo.db[LISTINGS].count_documents(query)


async def user_listings(user_id: int, status: str | None = None) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"seller_user_id": user_id}
    if status:
        query["status"] = status
    cursor = mongo.db[LISTINGS].find(query).sort("created_at", -1)
    return [doc async for doc in cursor]


async def claim_listing(listing_id: str, buyer_id: int) -> dict[str, Any] | None:
    """Atomically transition an active listing to pending (guarded)."""
    return await mongo.db[LISTINGS].find_one_and_update(
        {"listing_id": listing_id, "status": STATUS_ACTIVE},
        {
            "$set": {
                "status": STATUS_PENDING,
                "buyer_user_id": buyer_id,
                "updated_at": int(time.time()),
            }
        },
        return_document=True,
    )


async def release_listing(listing_id: str) -> None:
    """Revert a pending listing back to active (after a failed sale)."""
    await mongo.db[LISTINGS].update_one(
        {"listing_id": listing_id, "status": STATUS_PENDING},
        {"$set": {"status": STATUS_ACTIVE, "buyer_user_id": None, "updated_at": int(time.time())}},
    )


async def mark_sold(listing_id: str) -> None:
    await mongo.db[LISTINGS].update_one(
        {"listing_id": listing_id, "status": STATUS_PENDING},
        {"$set": {"status": STATUS_SOLD, "sold_at": int(time.time()), "updated_at": int(time.time())}},
    )


async def cancel_listing(listing_id: str, user_id: int) -> bool:
    result = await mongo.db[LISTINGS].update_one(
        {"listing_id": listing_id, "seller_user_id": user_id, "status": STATUS_ACTIVE},
        {"$set": {"status": STATUS_CANCELLED, "updated_at": int(time.time())}},
    )
    return result.modified_count > 0


async def admin_cancel_listing(listing_id: str) -> bool:
    result = await mongo.db[LISTINGS].update_one(
        {"listing_id": listing_id, "status": {"$in": [STATUS_ACTIVE, STATUS_PENDING]}},
        {"$set": {"status": STATUS_CANCELLED, "updated_at": int(time.time())}},
    )
    return result.modified_count > 0


async def count_listings(status: str | None = None) -> int:
    query: dict[str, Any] = {}
    if status:
        query["status"] = status
    return await mongo.db[LISTINGS].count_documents(query)


async def aggregate_listing_volume() -> int:
    pipeline = [
        {"$match": {"status": STATUS_SOLD}},
        {"$group": {"_id": None, "total": {"$sum": "$total_price"}}},
    ]
    result = await mongo.db[LISTINGS].aggregate(pipeline).to_list(1)
    return int(result[0]["total"]) if result else 0
