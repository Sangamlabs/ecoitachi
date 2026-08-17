"""Asset holdings data access layer.

One document per (user, asset).  All quantity / cost-basis changes are applied
with atomic MongoDB updates (including aggregation-pipeline updates) so
concurrent buys and sells can never corrupt balances or oversell.
"""

from __future__ import annotations

import time
from typing import Any

from database.mongo import mongo

HOLDINGS = "asset_holdings"

EPSILON = 1e-9


async def ensure_indexes() -> None:
    await mongo.db[HOLDINGS].create_index([("user_id", 1), ("asset_id", 1)], unique=True)
    await mongo.db[HOLDINGS].create_index("user_id")
    await mongo.db[HOLDINGS].create_index("asset_id")


async def _add_pipeline(qty: float, cost: int, price: int) -> list[dict[str, Any]]:
    """Aggregation-pipeline update adding a purchase atomically."""
    return [
        {
            "$set": {
                "quantity": {"$add": [{"$ifNull": ["$quantity", 0]}, qty]},
                "total_invested": {"$add": [{"$ifNull": ["$total_invested", 0]}, cost]},
            }
        },
        {
            "$set": {
                "average_buy_price": {"$divide": ["$total_invested", "$quantity"]},
                "current_value": {"$multiply": [price, "$quantity"]},
                "updated_at": int(time.time()),
            }
        },
    ]


async def add_holding(
    user_id: int, asset_id: str, symbol: str, qty: float, cost: int, price: int
) -> None:
    """Atomically add ``qty`` bought for ``cost`` sub-units to a holding."""
    result = await mongo.db[HOLDINGS].update_one(
        {"user_id": user_id, "asset_id": asset_id},
        await _add_pipeline(qty, cost, price),
    )
    if result.matched_count:
        return
    try:
        await mongo.db[HOLDINGS].insert_one(
            {
                "user_id": user_id,
                "asset_id": asset_id,
                "symbol": symbol,
                "quantity": qty,
                "average_buy_price": cost / qty,
                "total_invested": cost,
                "current_value": round(price * qty),
                "created_at": int(time.time()),
                "updated_at": int(time.time()),
            }
        )
    except Exception:
        await mongo.db[HOLDINGS].update_one(
            {"user_id": user_id, "asset_id": asset_id},
            await _add_pipeline(qty, cost, price),
        )


async def set_holding_quantity(user_id: int, asset_id: str, quantity: float) -> None:
    """Restore an exact asset holding quantity (used by security dump restore)."""
    if quantity is None or quantity <= EPSILON:
        await mongo.db[HOLDINGS].delete_one({"user_id": user_id, "asset_id": asset_id})
        return
    existing = await mongo.db[HOLDINGS].find_one({"user_id": user_id, "asset_id": asset_id})
    now = int(time.time())
    if existing is None:
        await mongo.db[HOLDINGS].insert_one(
            {
                "user_id": user_id,
                "asset_id": asset_id,
                "symbol": asset_id,
                "quantity": quantity,
                "average_buy_price": 0,
                "total_invested": 0,
                "current_value": 0,
                "created_at": now,
                "updated_at": now,
            }
        )
        return
    await mongo.db[HOLDINGS].update_one(
        {"user_id": user_id, "asset_id": asset_id},
        {"$set": {"quantity": quantity, "updated_at": now}},
    )


async def _sell_pipeline(qty: float, price: int) -> list[dict[str, Any]]:
    return [
        {
            "$set": {
                "cost_basis": {"$multiply": ["$total_invested", {"$divide": [qty, "$quantity"]}]},
                "quantity": {"$subtract": ["$quantity", qty]},
            }
        },
        {
            "$set": {
                "total_invested": {"$subtract": ["$total_invested", {"$round": ["$cost_basis", 0]}]},
                "quantity": {"$max": [0, "$quantity"]},
            }
        },
        {
            "$set": {
                "current_value": {"$multiply": [price, "$quantity"]},
                "average_buy_price": {
                    "$cond": [{"$eq": ["$quantity", 0]}, 0, {"$divide": ["$total_invested", "$quantity"]}]
                },
                "updated_at": int(time.time()),
            }
        },
        {"$unset": "cost_basis"},
    ]


async def remove_holding(
    user_id: int, asset_id: str, qty: float, price: int
) -> bool:
    """Atomically remove ``qty`` from a holding. Returns False if not enough is owned."""
    result = await mongo.db[HOLDINGS].update_one(
        {"user_id": user_id, "asset_id": asset_id, "quantity": {"$gte": qty}},
        await _sell_pipeline(qty, price),
    )
    if result.matched_count == 0:
        return False
    await mongo.db[HOLDINGS].delete_many({"quantity": {"$lte": EPSILON}})
    return True


async def get_holding(user_id: int, asset_id: str) -> dict[str, Any] | None:
    return await mongo.db[HOLDINGS].find_one({"user_id": user_id, "asset_id": asset_id})


async def get_user_holdings(user_id: int) -> list[dict[str, Any]]:
    cursor = mongo.db[HOLDINGS].find({"user_id": user_id, "quantity": {"$gt": EPSILON}})
    return [doc async for doc in cursor]


async def all_holdings() -> list[dict[str, Any]]:
    cursor = mongo.db[HOLDINGS].find({"quantity": {"$gt": EPSILON}})
    return [doc async for doc in cursor]


async def holdings_for_asset(asset_id: str, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
    cursor = (
        mongo.db[HOLDINGS]
        .find({"asset_id": asset_id, "quantity": {"$gt": EPSILON}})
        .sort("current_value", -1)
        .skip(offset)
        .limit(limit)
    )
    return [doc async for doc in cursor]


async def count_holdings() -> int:
    return await mongo.db[HOLDINGS].count_documents({"quantity": {"$gt": EPSILON}})


async def count_holders() -> int:
    pipeline = [
        {"$match": {"quantity": {"$gt": EPSILON}}},
        {"$group": {"_id": "$user_id"}},
        {"$count": "total"},
    ]
    result = await mongo.db[HOLDINGS].aggregate(pipeline).to_list(1)
    return int(result[0]["total"]) if result else 0


async def aggregate_current_value() -> int:
    pipeline = [{"$group": {"_id": None, "total": {"$sum": "$current_value"}}}]
    result = await mongo.db[HOLDINGS].aggregate(pipeline).to_list(1)
    return int(result[0]["total"]) if result else 0


async def aggregate_value_for_asset(asset_id: str) -> int:
    pipeline = [
        {"$match": {"asset_id": asset_id, "quantity": {"$gt": EPSILON}}},
        {"$group": {"_id": None, "total": {"$sum": "$current_value"}}},
    ]
    result = await mongo.db[HOLDINGS].aggregate(pipeline).to_list(1)
    return int(result[0]["total"]) if result else 0


async def count_holders_for_asset(asset_id: str) -> int:
    return await mongo.db[HOLDINGS].count_documents(
        {"asset_id": asset_id, "quantity": {"$gt": EPSILON}}
    )


async def aggregate_invested() -> int:
    pipeline = [{"$group": {"_id": None, "total": {"$sum": "$total_invested"}}}]
    result = await mongo.db[HOLDINGS].aggregate(pipeline).to_list(1)
    return int(result[0]["total"]) if result else 0


async def top_assets_by_value(limit: int = 5) -> list[dict[str, Any]]:
    pipeline = [
        {"$match": {"quantity": {"$gt": EPSILON}}},
        {"$group": {"_id": "$asset_id", "value": {"$sum": "$current_value"}, "holders": {"$sum": 1}}},
        {"$sort": {"value": -1}},
        {"$limit": limit},
    ]
    rows = await mongo.db[HOLDINGS].aggregate(pipeline).to_list(limit)
    result = []
    for row in rows:
        asset = await mongo.db["assets"].find_one(
            {"asset_id": row["_id"]}, {"_id": 0, "symbol": 1, "name": 1, "emoji": 1}
        )
        if asset:
            result.append(
                {
                    "symbol": asset.get("symbol", row["_id"]),
                    "name": asset.get("name", ""),
                    "emoji": asset.get("emoji", ""),
                    "value": int(row["value"]),
                    "holders": int(row["holders"]),
                }
            )
    return result


async def most_held_assets(limit: int = 5) -> list[dict[str, Any]]:
    pipeline = [
        {"$match": {"quantity": {"$gt": EPSILON}}},
        {"$group": {"_id": "$asset_id", "quantity": {"$sum": "$quantity"}, "holders": {"$sum": 1}}},
        {"$sort": {"quantity": -1}},
        {"$limit": limit},
    ]
    rows = await mongo.db[HOLDINGS].aggregate(pipeline).to_list(limit)
    result = []
    for row in rows:
        asset = await mongo.db["assets"].find_one(
            {"asset_id": row["_id"]}, {"_id": 0, "symbol": 1, "name": 1}
        )
        if asset:
            result.append(
                {
                    "symbol": asset.get("symbol", row["_id"]),
                    "name": asset.get("name", ""),
                    "quantity": round(float(row["quantity"]), 6),
                    "holders": int(row["holders"]),
                }
            )
    return result
