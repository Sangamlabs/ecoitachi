"""User data access layer.

Users are created lazily on first interaction with the bot.  All financial
balances on the user document are maintained exclusively through the economy
service using atomic MongoDB updates.
"""

from __future__ import annotations

import time
from typing import Any

from database.mongo import mongo

COLLECTION = "users"

ZERO_USER = {
    "wallet": 0,
    "bank": 0,
    "total_earned": 0,
    "total_spent": 0,
    "total_deposited": 0,
    "total_withdrawn": 0,
    "total_tax_paid": 0,
    "total_interest_earned": 0,
    "monthly_earnings": 0,
    "monthly_rank": None,
    "is_banned": False,
    "is_frozen": False,
    "last_interest_at": None,
}


async def ensure_indexes() -> None:
    users = mongo.db[COLLECTION]
    await users.create_index("user_id", unique=True)
    await users.create_index("username")
    await users.create_index("monthly_earnings")


async def get_or_create_user(
    user_id: int,
    username: str | None = None,
    first_name: str | None = None,
) -> dict[str, Any]:
    """Return the user document, creating it with the starting balance if new.

    The starting balance is read from the centralized settings collection so
    admins can change the welcome grant without touching code.
    """
    users = mongo.db[COLLECTION]
    now = int(time.time())
    from services import settings as settings_service  # lazy: avoids import-order surprises

    starting_balance = int((await settings_service.get_settings()).get("starting_balance", 0))
    doc = await users.find_one_and_update(
        {"user_id": user_id},
        {
            "$setOnInsert": {
                **ZERO_USER,
                "wallet": starting_balance,
                "user_id": user_id,
                "username": username,
                "first_name": first_name,
                "last_interest_at": now,  # numeric epoch: eligible 24h after joining
                "created_at": now,
                "updated_at": now,
                "last_active_at": now,
            }
        },
        upsert=True,
        return_document=True,
    )
    if doc is None:
        raise RuntimeError(f"Failed to create user {user_id}")
    return doc


async def touch_user(user_id: int, username: str | None = None, first_name: str | None = None) -> None:
    """Update username/first_name and last_active_at for an existing user."""
    update: dict[str, Any] = {"last_active_at": int(time.time()), "updated_at": int(time.time())}
    if username:
        update["username"] = username
    if first_name:
        update["first_name"] = first_name
    await mongo.db[COLLECTION].update_one({"user_id": user_id}, {"$set": update})


async def get_user(user_id: int) -> dict[str, Any] | None:
    return await mongo.db[COLLECTION].find_one({"user_id": user_id})


async def get_user_by_username(username: str) -> dict[str, Any] | None:
    return await mongo.db[COLLECTION].find_one({"username": username.lower()})


async def user_exists(user_id: int) -> bool:
    return await mongo.db[COLLECTION].find_one(
        {"user_id": user_id}, {"_id": 0, "user_id": 1}
    ) is not None


async def set_user_flags(user_id: int, **flags: bool) -> None:
    """Set boolean flags such as is_banned / is_frozen."""
    await mongo.db[COLLECTION].update_one(
        {"user_id": user_id},
        {"$set": {**flags, "updated_at": int(time.time())}},
    )


async def count_users() -> int:
    return await mongo.db[COLLECTION].count_documents({})


async def set_monthly_rank(user_id: int, rank: int | None) -> None:
    await mongo.db[COLLECTION].update_one(
        {"user_id": user_id}, {"$set": {"monthly_rank": rank}}
    )


async def add_monthly_earnings(user_id: int, amount: int) -> None:
    """Accumulate earnings into the monthly stats counter (for distribution)."""
    await mongo.db[COLLECTION].update_one(
        {"user_id": user_id}, {"$inc": {"monthly_earnings": amount}}
    )


async def inc(user_id: int, changes: dict[str, int], *, touch: bool = True) -> None:
    """Atomic ``$inc`` on one user document (used by the economy engine)."""
    update: dict[str, Any] = {"$inc": changes}
    if touch:
        update["$set"] = {"updated_at": int(time.time())}
    await mongo.db[COLLECTION].update_one({"user_id": user_id}, update)


async def set_user_field(user_id: int, field: str, value: Any) -> None:
    """Set one numeric/cached field (e.g. asset_value) on a user."""
    await mongo.db[COLLECTION].update_one(
        {"user_id": user_id},
        {"$set": {field: value, "updated_at": int(time.time())}},
    )


async def aggregate_totals(field: str) -> int:
    """Sum of a numeric field across all users (e.g. wallet, bank)."""
    pipeline = [{"$group": {"_id": None, "total": {"$sum": f"${field}"}}}]
    result = await mongo.db[COLLECTION].aggregate(pipeline).to_list(1)
    return int(result[0]["total"]) if result else 0
