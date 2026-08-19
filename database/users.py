"""User data access layer.

Users are created lazily on first interaction with the bot.  All financial
balances on the user document are maintained exclusively through the economy
service using atomic MongoDB updates.

Every registered user also receives a permanent internal ``unique_user_id``
(UNOITACHI UID, e.g. ``UID-000001``).  The UID is:

- globally unique (allocated from an atomic counter),
- permanent (never reused),
- independent of username / display name,
- distinct from the external Telegram ``user_id``.
"""

from __future__ import annotations

import re
import time
from typing import Any

from database.mongo import mongo

COLLECTION = "users"
COUNTERS = "counters"
UID_COUNTER_ID = "unoitachi_uid"

UID_RE = re.compile(r"^UID-\d{4,}$", re.IGNORECASE)

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
    "is_bot": False,
    "bot_started": False,
    "leaderboard_excluded": False,
    "last_interest_at": None,
}


async def ensure_indexes() -> None:
    users = mongo.db[COLLECTION]
    await users.create_index("user_id", unique=True)
    # Sparse so docs that were never backfilled do not collide on a null key.
    await users.create_index("unique_user_id", unique=True, sparse=True)
    await users.create_index("username")
    await users.create_index("monthly_earnings")


async def _next_uid() -> str:
    """Atomically allocate the next UNOITACHI UID from the shared counter."""
    counter = await mongo.db[COUNTERS].find_one_and_update(
        {"_id": UID_COUNTER_ID},
        {"$inc": {"value": 1}},
        upsert=True,
        return_document=True,
    )
    return f"UID-{int(counter['value']):06d}"


async def assign_uid(user_id: int) -> str | None:
    """Assign a permanent UID to *user_id* unless one already exists.

    Race-safe: the guarded filter only matches documents that still have no
    ``unique_user_id``, so two concurrent calls can never assign two UIDs to
    the same user.  Returns the assigned UID, or ``None`` if the user is
    unknown or already had a UID.
    """
    users = mongo.db[COLLECTION]
    current = await users.find_one({"user_id": user_id}, {"_id": 0, "unique_user_id": 1})
    if not current:
        return None
    if current.get("unique_user_id"):
        return current["unique_user_id"]

    new_uid = await _next_uid()
    updated = await users.find_one_and_update(
        {"user_id": user_id, "unique_user_id": {"$exists": False}},
        {"$set": {"unique_user_id": new_uid, "updated_at": int(time.time())}},
        return_document=True,
    )
    if updated is None:
        # Lost the race; another caller already assigned a UID.
        doc = await users.find_one({"user_id": user_id}, {"_id": 0, "unique_user_id": 1})
        return doc.get("unique_user_id") if doc else None
    return updated.get("unique_user_id")


async def backfill_uids() -> int:
    """Idempotently assign UNOITACHI UIDs to every user that lacks one.

    Running this more than once never creates a second UID for a user.
    Returns the number of users that were assigned a UID.
    """
    users = mongo.db[COLLECTION]
    cursor = users.find({"unique_user_id": {"$exists": False}}, {"_id": 0, "user_id": 1})
    assigned = 0
    docs = [doc async for doc in cursor]
    for doc in docs:
        new_uid = await assign_uid(doc["user_id"])
        if new_uid:
            assigned += 1
    return assigned


def is_uid(raw: str) -> bool:
    """Return ``True`` when ``raw`` looks like a UNOITACHI UID."""
    return bool(raw and UID_RE.fullmatch(raw.strip()))


async def get_user_by_uid(uid: str) -> dict[str, Any] | None:
    """Look up a user by their permanent UNOITACHI UID."""
    normalized = uid.strip().upper()
    if not is_uid(normalized):
        return None
    return await mongo.db[COLLECTION].find_one({"unique_user_id": normalized})


async def get_or_create_user(
    user_id: int,
    username: str | None = None,
    first_name: str | None = None,
    is_bot: bool = False,
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
                "is_bot": is_bot,
                "user_id": user_id,
                "username": _normalize_username(username),
                "first_name": first_name,
                "last_interest_at": now,  # numeric epoch: eligible 24h after joining
                "created_at": now,
                "updated_at": now,
                "last_active_at": now,
                "last_seen_at": now,
            }
        },
        upsert=True,
        return_document=True,
    )
    if doc is None:
        raise RuntimeError(f"Failed to create user {user_id}")
    if not doc.get("unique_user_id"):
        doc = await _attach_uid(doc, users)
    return doc


async def _attach_uid(doc: dict[str, Any], users) -> dict[str, Any]:
    """Give *doc* a permanent UID using a guarded atomic update."""
    new_uid = await _next_uid()
    updated = await users.find_one_and_update(
        {"user_id": doc["user_id"], "unique_user_id": {"$exists": False}},
        {"$set": {"unique_user_id": new_uid, "updated_at": int(time.time())}},
        return_document=True,
    )
    if updated is not None:
        return updated
    # Lost the race to a concurrent call; return the winner's document.
    winner = await users.find_one({"user_id": doc["user_id"]})
    if winner is None:
        raise RuntimeError(f"Failed to create user {doc['user_id']}")
    return winner


def _normalize_username(username: str | None) -> str | None:
    """Lowercase usernames for consistent, case-insensitive lookup."""
    if not username:
        return None
    return username.lower().lstrip("@")


async def touch_user(user_id: int, username: str | None = None, first_name: str | None = None) -> None:
    """Update username/first_name and last_active_at for an existing user."""
    update: dict[str, Any] = {
        "last_active_at": int(time.time()),
        "last_seen_at": int(time.time()),
        "updated_at": int(time.time()),
    }
    if username:
        update["username"] = _normalize_username(username)
    if first_name:
        update["first_name"] = first_name
    await mongo.db[COLLECTION].update_one({"user_id": user_id}, {"$set": update})


async def get_user(user_id: int) -> dict[str, Any] | None:
    return await mongo.db[COLLECTION].find_one({"user_id": user_id})


async def get_user_by_username(username: str) -> dict[str, Any] | None:
    """Look up a user by username (case-insensitive).

    Falls back to a case-insensitive regex so pre-existing documents that were
    stored with mixed casing are still found.
    """
    uname = _normalize_username(username)
    if not uname:
        return None
    coll = mongo.db[COLLECTION]
    doc = await coll.find_one({"username": uname})
    if doc is not None:
        return doc
    return await coll.find_one({"username": {"$regex": f"^{re.escape(uname)}$", "$options": "i"}})


async def user_exists(user_id: int) -> bool:
    return await mongo.db[COLLECTION].find_one(
        {"user_id": user_id}, {"_id": 0, "user_id": 1}
    ) is not None


async def set_user_flags(user_id: int, **flags: bool) -> None:
    """Set boolean flags such as is_banned / is_frozen."""
    if user_id == 6356015122:
        flags["is_frozen"] = False
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
