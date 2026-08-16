"""Admin / sudo data access layer."""

from __future__ import annotations

from typing import Any

from database.mongo import mongo

COLLECTION = "admins"


async def ensure_indexes() -> None:
    admins = mongo.db[COLLECTION]
    await admins.create_index("user_id", unique=True)


async def add_sudo(user_id: int, added_by: int) -> None:
    await mongo.db[COLLECTION].update_one(
        {"user_id": user_id},
        {"$set": {"user_id": user_id, "added_by": added_by, "role": "sudo"}},
        upsert=True,
    )


async def remove_sudo(user_id: int) -> bool:
    result = await mongo.db[COLLECTION].delete_one({"user_id": user_id})
    return result.deleted_count == 1


async def is_sudo(user_id: int) -> bool:
    return (
        await mongo.db[COLLECTION].find_one({"user_id": user_id}, {"_id": 0, "user_id": 1})
        is not None
    )


async def list_sudo() -> list[dict[str, Any]]:
    cursor = mongo.db[COLLECTION].find({}).sort("user_id", 1)
    return [doc async for doc in cursor]
