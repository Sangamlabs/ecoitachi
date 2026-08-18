"""Broadcast service — send a copied message to registered groups or users.

Every broadcast gets a unique ``broadcast_id`` and is logged to the
``broadcast_logs`` collection.  Sends go through Pyrogram's ``copy_message``
so formatting, entities and media are preserved exactly; sends are throttled in
batches to respect Telegram rate limits, and FloodWait is handled with retries.

Nothing here can be triggered without OWNER/SUDO confirmation — see
``handlers/broadcast.py``.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from pyrogram import Client
from pyrogram.errors import FloodWait, RPCError

from database.mongo import mongo

logger = logging.getLogger("broadcast")

COLLECTION = "broadcast_logs"

TYPE_DM = "dm"
TYPE_GROUP = "group"
STATUS_PENDING = "pending"
STATUS_COMPLETED = "completed"
STATUS_CANCELLED = "cancelled"

DEFAULT_BATCH_SIZE = 5
BATCH_DELAY = 1.0
MAX_FLOODWAIT_RETRIES = 3


async def ensure_indexes() -> None:
    """Create indexes on the broadcast logs collection."""
    logs = mongo.db[COLLECTION]
    await logs.create_index("broadcast_id", unique=True)
    await logs.create_index("type")
    await logs.create_index("created_at")
    await logs.create_index("status")
    await logs.create_index([("type", 1), ("created_at", -1)])


def new_broadcast_id() -> str:
    return f"BC-{uuid.uuid4().hex[:8].upper()}"


# ─── Target discovery ───────────────────────────────────────────────────────

async def get_target_chats() -> list[int]:
    """Chat IDs of all configured groups with group features enabled."""
    from database import group_config as group_config_db

    cursor = mongo.db[group_config_db.COLLECTION].find({})
    chats: list[int] = []
    async for doc in cursor:
        if doc.get("group_enabled", True):
            chats.append(int(doc["chat_id"]))
    return chats


async def get_target_users() -> list[int]:
    """User IDs of all registered, non-banned, non-frozen users."""
    from database import users as users_db

    cursor = mongo.db[users_db.COLLECTION].find(
        {"is_banned": {"$ne": True}, "is_frozen": {"$ne": True}},
        {"user_id": 1, "_id": 0},
    )
    return [doc["user_id"] async for doc in cursor]


# ─── Per-target senders ─────────────────────────────────────────────────────

def _classify_error(exc: RPCError) -> str:
    """Classify a send error as ``blocked`` or ``failed``."""
    text = str(exc).lower()
    if any(k in text for k in ("deactivated", "was blocked", "forbidden", "chat not found", "user not found", "kicked")):
        return "blocked"
    return "failed"


async def _copy_with_retry(
    client: Client,
    chat_id: int,
    source_chat_id: int,
    source_message_id: int,
) -> tuple[bool, str]:
    """Copy a message once, honoring FloodWait with limited retries.

    Returns ``(ok, outcome)`` where outcome is ``sent``/``failed``/``blocked``.
    """
    for attempt in range(1, MAX_FLOODWAIT_RETRIES + 1):
        try:
            await client.copy_message(
                chat_id=chat_id,
                from_chat_id=source_chat_id,
                message_id=source_message_id,
                protect_content=False,
            )
            return True, "sent"
        except FloodWait as e:
            if attempt == MAX_FLOODWAIT_RETRIES:
                logger.warning(
                    "FloodWait exhausted for chat %s (%ss)", chat_id, e.value
                )
                return False, "failed"
            await asyncio.sleep(max(1, min(e.value, 30)))
        except RPCError as exc:
            return False, _classify_error(exc)
    return False, "failed"


async def broadcast_dm(
    client: Client,
    source_chat_id: int,
    source_message_id: int,
    user_id: int,
) -> str:
    """Send one DM broadcast; returns outcome: sent/failed/blocked."""
    ok, outcome = await _copy_with_retry(
        client, user_id, source_chat_id, source_message_id
    )
    if not ok:
        logger.info("dm broadcast to %s -> %s", user_id, outcome)
    return outcome


async def broadcast_group(
    client: Client,
    source_chat_id: int,
    source_message_id: int,
    chat_id: int,
) -> str:
    """Send one group broadcast; returns outcome: sent/failed/blocked."""
    ok, outcome = await _copy_with_retry(
        client, chat_id, source_chat_id, source_message_id
    )
    if not ok:
        logger.info("group broadcast to %s -> %s", chat_id, outcome)
    return outcome


# ─── Orchestration ──────────────────────────────────────────────────────────

async def create_pending(
    broadcast_type: str,
    sender_id: int,
    source_chat_id: int,
    source_message_id: int,
    total_targets: int,
) -> dict[str, Any]:
    """Insert a pending broadcast record and return it (for confirmation)."""
    now = int(time.time())
    doc = {
        "broadcast_id": new_broadcast_id(),
        "type": broadcast_type,
        "sender_id": sender_id,
        "source_chat_id": source_chat_id,
        "source_message_id": source_message_id,
        "created_at": now,
        "status": STATUS_PENDING,
        "total_targets": total_targets,
        "sent": 0,
        "failed": 0,
        "blocked": 0,
    }
    await mongo.db[COLLECTION].insert_one(doc)
    return dict(doc)


async def set_status(broadcast_id: str, status: str, **extra: Any) -> None:
    await mongo.db[COLLECTION].update_one(
        {"broadcast_id": broadcast_id},
        {"$set": {"status": status, **extra, "updated_at": int(time.time())}},
    )


async def run_broadcast(
    client: Client,
    broadcast_type: str,
    source_chat_id: int,
    source_message_id: int,
    sender_id: int,
    broadcast_id: str,
) -> dict[str, Any]:
    """Execute a confirmed broadcast and log the final statistics.

    Idempotent per ``broadcast_id``: if the record is already ``completed``
    this is a no-op, so a broadcast can never be sent twice.
    """
    doc = await mongo.db[COLLECTION].find_one({"broadcast_id": broadcast_id})
    if doc is None:
        raise ValueError("Broadcast record not found.")
    if doc.get("status") == STATUS_COMPLETED:
        return dict(doc)

    if broadcast_type == TYPE_DM:
        targets = await get_target_users()
    elif broadcast_type == TYPE_GROUP:
        targets = await get_target_chats()
    else:
        raise ValueError(f"Invalid broadcast type: {broadcast_type}")

    targets = [t for t in targets if t != sender_id]
    if not targets:
        await set_status(broadcast_id, STATUS_COMPLETED, sent=0, failed=0, blocked=0,
                         total_targets=0, completed_at=int(time.time()), duration=0)
        return await mongo.db[COLLECTION].find_one({"broadcast_id": broadcast_id})

    started_at = time.time()
    counts = {"sent": 0, "failed": 0, "blocked": 0}

    batch_size = max(1, min(DEFAULT_BATCH_SIZE, len(targets)))
    for i in range(0, len(targets), batch_size):
        batch = targets[i : i + batch_size]
        if broadcast_type == TYPE_DM:
            outcomes = await asyncio.gather(
                *(broadcast_dm(client, source_chat_id, source_message_id, t) for t in batch),
                return_exceptions=True,
            )
        else:
            outcomes = await asyncio.gather(
                *(broadcast_group(client, source_chat_id, source_message_id, t) for t in batch),
                return_exceptions=True,
            )
        for outcome in outcomes:
            if isinstance(outcome, Exception):
                counts["failed"] += 1
            else:
                counts[outcome] += 1
        if i + batch_size < len(targets):
            await asyncio.sleep(BATCH_DELAY)

    completed_at = time.time()
    stats = {
        "sent": counts["sent"],
        "failed": counts["failed"],
        "blocked": counts["blocked"],
        "total_targets": len(targets),
        "completed_at": int(completed_at),
        "duration": round(completed_at - started_at, 2),
    }
    await set_status(broadcast_id, STATUS_COMPLETED, **stats)
    return await mongo.db[COLLECTION].find_one({"broadcast_id": broadcast_id})