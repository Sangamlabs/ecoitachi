"""Transaction (audit) data access layer."""

from __future__ import annotations

from typing import Any

from database.mongo import mongo

COLLECTION = "transactions"


async def ensure_indexes() -> None:
    tx = mongo.db[COLLECTION]
    await tx.create_index("transaction_id", unique=True)
    await tx.create_index("user_id")
    await tx.create_index([("user_id", 1), ("created_at", -1)])
    await tx.create_index("created_at")


async def insert_transaction(doc: dict[str, Any]) -> str:
    await mongo.db[COLLECTION].insert_one(doc)
    return doc["transaction_id"]


async def recent_by_user(user_id: int, limit: int = 10) -> list[dict[str, Any]]:
    cursor = mongo.db[COLLECTION].find({"user_id": user_id}).sort("created_at", -1).limit(limit)
    return [doc async for doc in cursor]


async def count_for_user(user_id: int) -> int:
    return await mongo.db[COLLECTION].count_documents({"user_id": user_id})
