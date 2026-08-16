"""Promo code system data access layer.

Two collections:

* ``promo_codes``     — one document per promo (``_id`` is the promo_id), with
  an atomic slot reservation for the total redemption limit;
* ``promo_redemptions`` — one document per user redemption, guarded by a unique
  ``(promo_id, user_id)`` index so a single user can never redeem twice, even
  under concurrent requests.
"""

from __future__ import annotations

import time
from typing import Any

from pymongo import ReturnDocument

from database.mongo import mongo

PROMO_CODES = "promo_codes"
PROMO_REDEMPTIONS = "promo_redemptions"


async def ensure_indexes() -> None:
    codes = mongo.db[PROMO_CODES]
    await codes.create_index("normalized_code", unique=True)
    await codes.create_index("is_active")
    await codes.create_index("created_at")
    redemptions = mongo.db[PROMO_REDEMPTIONS]
    await redemptions.create_index(
        [("promo_id", 1), ("user_id", 1)], unique=True
    )
    await redemptions.create_index("normalized_code")
    await redemptions.create_index("user_id")
    await redemptions.create_index("redeemed_at")


# --------------------------------------------------------------------------- #
# promo_codes
# --------------------------------------------------------------------------- #


async def insert_promo(doc: dict[str, Any]) -> None:
    await mongo.db[PROMO_CODES].insert_one(doc)


async def get_promo(promo_id: str) -> dict[str, Any] | None:
    return await mongo.db[PROMO_CODES].find_one({"_id": promo_id})


async def get_promo_by_code(normalized_code: str) -> dict[str, Any] | None:
    return await mongo.db[PROMO_CODES].find_one({"normalized_code": normalized_code})


async def update_promo(promo_id: str, fields: dict[str, Any]) -> None:
    fields["updated_at"] = int(time.time())
    await mongo.db[PROMO_CODES].update_one({"_id": promo_id}, {"$set": fields})


async def reserve_slot(promo_id: str, now: int) -> dict[str, Any] | None:
    """Atomically reserve one redemption slot for a promo.

    The filter enforces active + not-expired + (unlimited or
    ``redeemed_count < max_redemptions``) in a single atomic update, so two
    concurrent redemptions can never over-consume the total limit.
    """
    return await mongo.db[PROMO_CODES].find_one_and_update(
        {
            "_id": promo_id,
            "is_active": True,
            "$or": [{"expires_at": None}, {"expires_at": {"$gt": now}}],
            "$expr": {
                "$or": [
                    {"$eq": [{"$ifNull": ["$max_redemptions", None]}, None]},
                    {"$lt": ["$redeemed_count", "$max_redemptions"]},
                ]
            },
        },
        [{"$set": {"redeemed_count": {"$add": ["$redeemed_count", 1]}}}],
        return_document=ReturnDocument.AFTER,
    )


async def release_slot(promo_id: str) -> None:
    """Undo a reserved slot (duplicate redemption or failed grant)."""
    await mongo.db[PROMO_CODES].update_one(
        {"_id": promo_id},
        [{"$set": {"redeemed_count": {"$max": [{"$subtract": ["$redeemed_count", 1]}, 0]}}}],
    )


async def find_expired_active() -> list[dict[str, Any]]:
    now = int(time.time())
    cursor = mongo.db[PROMO_CODES].find(
        {"is_active": True, "expires_at": {"$ne": None, "$lte": now}}
    )
    return [doc async for doc in cursor]


async def list_active_cache() -> list[dict[str, Any]]:
    now = int(time.time())
    cursor = mongo.db[PROMO_CODES].find(
        {
            "is_active": True,
            "$or": [{"expires_at": None}, {"expires_at": {"$gt": now}}],
        },
        {"_id": 0, "normalized_code": 1, "expires_at": 1},
    )
    return [doc async for doc in cursor]


async def list_promos(
    status: str = "all", page: int = 1, per_page: int = 10
) -> tuple[list[dict[str, Any]], int]:
    now = int(time.time())
    if status == "active":
        query: dict[str, Any] = {
            "is_active": True,
            "$or": [{"expires_at": None}, {"expires_at": {"$gt": now}}],
        }
    elif status == "expired":
        query = {"is_active": True, "expires_at": {"$ne": None, "$lte": now}}
    elif status == "inactive":
        query = {"is_active": False}
    else:
        query = {}
    total = await mongo.db[PROMO_CODES].count_documents(query)
    skip = max(0, page - 1) * per_page
    cursor = (
        mongo.db[PROMO_CODES]
        .find(query)
        .sort("created_at", -1)
        .skip(skip)
        .limit(per_page)
    )
    docs = [doc async for doc in cursor]
    return docs, total


async def count_all() -> int:
    return await mongo.db[PROMO_CODES].count_documents({})


# --------------------------------------------------------------------------- #
# promo_redemptions
# --------------------------------------------------------------------------- #


async def insert_redemption(doc: dict[str, Any]) -> None:
    await mongo.db[PROMO_REDEMPTIONS].insert_one(doc)


async def get_redemption(promo_id: str, user_id: int) -> dict[str, Any] | None:
    return await mongo.db[PROMO_REDEMPTIONS].find_one(
        {"promo_id": promo_id, "user_id": user_id}
    )


async def update_redemption(redemption_id: str, fields: dict[str, Any]) -> None:
    await mongo.db[PROMO_REDEMPTIONS].update_one(
        {"_id": redemption_id}, {"$set": fields}
    )


async def delete_redemption(redemption_id: str) -> None:
    """Remove a failed redemption so the user can retry the same code."""
    await mongo.db[PROMO_REDEMPTIONS].delete_one({"_id": redemption_id})


async def count_redemptions(promo_id: str) -> int:
    return await mongo.db[PROMO_REDEMPTIONS].count_documents({"promo_id": promo_id})


async def count_completed(promo_id: str) -> int:
    return await mongo.db[PROMO_REDEMPTIONS].count_documents(
        {"promo_id": promo_id, "status": "completed"}
    )


async def list_redemptions(promo_id: str, limit: int = 50) -> list[dict[str, Any]]:
    cursor = (
        mongo.db[PROMO_REDEMPTIONS]
        .find({"promo_id": promo_id})
        .sort("redeemed_at", -1)
        .limit(limit)
    )
    return [doc async for doc in cursor]


async def latest_redemption(promo_id: str) -> dict[str, Any] | None:
    return await mongo.db[PROMO_REDEMPTIONS].find_one(
        {"promo_id": promo_id}, sort=[("redeemed_at", -1)]
    )


async def unique_users(promo_id: str) -> int:
    """Count distinct users who redeemed a promo (all statuses)."""
    pipeline = [
        {"$match": {"promo_id": promo_id}},
        {"$group": {"_id": "$user_id"}},
        {"$count": "n"},
    ]
    result = await mongo.db[PROMO_REDEMPTIONS].aggregate(pipeline).to_list(1)
    return result[0]["n"] if result else 0


async def aggregate_granted(promo_id: str) -> list[dict[str, Any]]:
    """Totals per reward type/detail from completed redemptions.

    Returns rows like ``{"type": "stock", "detail": "BTC", "total": 0.12}``.
    """
    pipeline = [
        {"$match": {"promo_id": promo_id, "status": "completed"}},
        {"$unwind": "$rewards_granted"},
        {
            "$group": {
                "_id": {
                    "type": "$rewards_granted.type",
                    "detail": "$rewards_granted.detail",
                },
                "total": {"$sum": "$rewards_granted.amount"},
            }
        },
        {"$project": {"_id": 0, "type": "$_id.type", "detail": "$_id.detail", "total": 1}},
    ]
    return await mongo.db[PROMO_REDEMPTIONS].aggregate(pipeline).to_list(200)
