"""Per-chat group configuration data access layer."""

from __future__ import annotations

import time
from typing import Any

from database.mongo import mongo

COLLECTION = "group_config"


async def ensure_indexes() -> None:
    await mongo.db[COLLECTION].create_index("chat_id", unique=True)


async def get_doc(chat_id: int) -> dict[str, Any] | None:
    return await mongo.db[COLLECTION].find_one({"chat_id": chat_id})


async def ensure_registered(chat_id: int) -> None:
    """Idempotently create a chat document if none exists.

    Used by the auto-registration hooks so every group the bot joins becomes a
    potential broadcast target without a manual ``/setchat``.
    """
    await mongo.db[COLLECTION].update_one(
        {"chat_id": chat_id},
        {"$setOnInsert": {"chat_id": chat_id, "registered_at": int(time.time())}},
        upsert=True,
    )


async def upsert(chat_id: int, changes: dict[str, Any]) -> None:
    await mongo.db[COLLECTION].update_one(
        {"chat_id": chat_id}, {"$set": changes}, upsert=True
    )


async def delete(chat_id: int) -> None:
    await mongo.db[COLLECTION].delete_one({"chat_id": chat_id})
