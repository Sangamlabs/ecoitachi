"""Security data access layer.

Manages security cases, security dumps, global bans, and security audit events.
"""

from __future__ import annotations

import time
from typing import Any

from database.mongo import mongo

CASES_COLLECTION = "security_cases"
DUMPS_COLLECTION = "security_dumps"
BANS_COLLECTION = "global_bans"
EVENTS_COLLECTION = "security_events"


async def ensure_indexes() -> None:
    cases = mongo.db[CASES_COLLECTION]
    await cases.create_index("case_id", unique=True)
    await cases.create_index("user_id")
    await cases.create_index("case_type")
    await cases.create_index("created_at")

    dumps = mongo.db[DUMPS_COLLECTION]
    await dumps.create_index("dump_id", unique=True)
    await dumps.create_index("case_id")
    await dumps.create_index("original_user_id")
    await dumps.create_index("status")
    await dumps.create_index("created_at")

    bans = mongo.db[BANS_COLLECTION]
    await bans.create_index("ban_id", unique=True)
    await bans.create_index("user_id", unique=True)
    await bans.create_index("case_id")
    await bans.create_index("created_at")

    events = mongo.db[EVENTS_COLLECTION]
    await events.create_index("event_id", unique=True)
    await events.create_index("user_id")
    await events.create_index("case_id")
    await events.create_index("dump_id")
    await events.create_index("event_type")
    await events.create_index("created_at")


# ============================================================
# Security Cases
# ============================================================

async def create_case(
    case_id: str,
    user_id: int,
    case_type: str,
    reason: str,
    created_by: int | None = None,
    dump_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = int(time.time())
    doc = {
        "case_id": case_id,
        "user_id": user_id,
        "case_type": case_type,
        "reason": reason,
        "created_by": created_by,
        "dump_id": dump_id,
        "metadata": metadata or {},
        "status": "open",
        "created_at": now,
        "updated_at": now,
        "resolved_at": None,
        "resolved_by": None,
    }
    await mongo.db[CASES_COLLECTION].insert_one(doc)
    return doc


async def get_case(case_id: str) -> dict[str, Any] | None:
    return await mongo.db[CASES_COLLECTION].find_one({"case_id": case_id})


async def get_cases_by_user(user_id: int, limit: int = 20) -> list[dict[str, Any]]:
    cursor = mongo.db[CASES_COLLECTION].find({"user_id": user_id}).sort("created_at", -1).limit(limit)
    return [doc async for doc in cursor]


async def update_case(case_id: str, **changes: Any) -> dict[str, Any] | None:
    changes["updated_at"] = int(time.time())
    result = await mongo.db[CASES_COLLECTION].find_one_and_update(
        {"case_id": case_id},
        {"$set": changes},
        return_document=True,
    )
    return result


async def list_cases(
    case_type: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {}
    if case_type:
        query["case_type"] = case_type
    if status:
        query["status"] = status
    cursor = mongo.db[CASES_COLLECTION].find(query).sort("created_at", -1).limit(limit)
    return [doc async for doc in cursor]


# ============================================================
# Security Dumps
# ============================================================

async def create_dump(doc: dict[str, Any]) -> str:
    await mongo.db[DUMPS_COLLECTION].insert_one(doc)
    return doc["dump_id"]


async def get_dump(dump_id: str) -> dict[str, Any] | None:
    return await mongo.db[DUMPS_COLLECTION].find_one({"dump_id": dump_id})


async def get_dump_by_case(case_id: str) -> dict[str, Any] | None:
    return await mongo.db[DUMPS_COLLECTION].find_one({"case_id": case_id})


async def get_dumps_by_user(user_id: int, limit: int = 20) -> list[dict[str, Any]]:
    cursor = mongo.db[DUMPS_COLLECTION].find({"original_user_id": user_id}).sort("created_at", -1).limit(limit)
    return [doc async for doc in cursor]


async def update_dump(dump_id: str, **changes: Any) -> dict[str, Any] | None:
    result = await mongo.db[DUMPS_COLLECTION].find_one_and_update(
        {"dump_id": dump_id},
        {"$set": changes},
        return_document=True,
    )
    return result


async def list_dumps(
    dump_type: str | None = None,
    status: str | None = None,
    user_id: int | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {}
    if dump_type:
        query["dump_type"] = dump_type
    if status:
        query["status"] = status
    if user_id:
        query["original_user_id"] = user_id
    cursor = mongo.db[DUMPS_COLLECTION].find(query).sort("created_at", -1).limit(limit)
    return [doc async for doc in cursor]


async def count_dumps(
    dump_type: str | None = None,
    status: str | None = None,
    user_id: int | None = None,
) -> int:
    query: dict[str, Any] = {}
    if dump_type:
        query["dump_type"] = dump_type
    if status:
        query["status"] = status
    if user_id:
        query["original_user_id"] = user_id
    return await mongo.db[DUMPS_COLLECTION].count_documents(query)


# ============================================================
# Global Bans
# ============================================================

async def create_global_ban(
    ban_id: str,
    user_id: int,
    reason: str,
    banned_by: int,
    case_id: str | None = None,
) -> dict[str, Any]:
    now = int(time.time())
    doc = {
        "ban_id": ban_id,
        "user_id": user_id,
        "reason": reason,
        "banned_by": banned_by,
        "case_id": case_id,
        "is_active": True,
        "created_at": now,
        "updated_at": now,
        "unbanned_at": None,
        "unbanned_by": None,
    }
    await mongo.db[BANS_COLLECTION].insert_one(doc)
    return doc


async def get_global_ban(user_id: int) -> dict[str, Any] | None:
    return await mongo.db[BANS_COLLECTION].find_one({"user_id": user_id, "is_active": True})


async def get_global_ban_by_id(ban_id: str) -> dict[str, Any] | None:
    return await mongo.db[BANS_COLLECTION].find_one({"ban_id": ban_id})


async def remove_global_ban(user_id: int, unbanned_by: int) -> bool:
    result = await mongo.db[BANS_COLLECTION].update_one(
        {"user_id": user_id, "is_active": True},
        {
            "$set": {
                "is_active": False,
                "unbanned_at": int(time.time()),
                "unbanned_by": unbanned_by,
                "updated_at": int(time.time()),
            }
        },
    )
    return result.modified_count == 1


async def list_global_bans(limit: int = 50) -> list[dict[str, Any]]:
    cursor = mongo.db[BANS_COLLECTION].find({"is_active": True}).sort("created_at", -1).limit(limit)
    return [doc async for doc in cursor]


# ============================================================
# Security Events (Audit Log)
# ============================================================

async def create_security_event(
    event_id: str,
    event_type: str,
    user_id: int,
    actor_id: int | None,
    case_id: str | None = None,
    dump_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = int(time.time())
    doc = {
        "event_id": event_id,
        "event_type": event_type,
        "user_id": user_id,
        "actor_id": actor_id,
        "case_id": case_id,
        "dump_id": dump_id,
        "metadata": metadata or {},
        "created_at": now,
    }
    await mongo.db[EVENTS_COLLECTION].insert_one(doc)
    return doc


async def get_security_events(
    user_id: int | None = None,
    event_type: str | None = None,
    case_id: str | None = None,
    dump_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {}
    if user_id:
        query["user_id"] = user_id
    if event_type:
        query["event_type"] = event_type
    if case_id:
        query["case_id"] = case_id
    if dump_id:
        query["dump_id"] = dump_id
    cursor = mongo.db[EVENTS_COLLECTION].find(query).sort("created_at", -1).limit(limit)
    return [doc async for doc in cursor]