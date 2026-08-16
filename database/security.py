"""Persistent security model for global bans, cases, and dumps.

All entities use numeric Telegram user IDs.  Usernames are never stored
as identifiers.  The module only defines MongoDB document schemas and
CRUD helpers — business logic lives in ``services/security.py``.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from database.mongo import mongo

COLLECTIONS: dict[str, str] = {
    "global_bans": "global_bans",
    "security_cases": "security_cases",
    "security_dumps": "security_dumps",
    "security_events": "security_events",
}


def ensure_indexes() -> None:
    """Create indexed fields required for fast look-ups."""
    for name, collection in COLLECTIONS.items():
        coll = mongo.db[collection]
        if name == "global_bans":
            coll.create_index("user_id", unique=True)
            coll.create_index("is_active")
        elif name == "security_cases":
            coll.create_index("case_id", unique=True)
            coll.create_index("user_id")
            coll.create_index("status")
            coll.create_index("created_at")
        elif name == "security_dumps":
            coll.create_index("dump_id", unique=True)
            coll.create_index("user_id")
            coll.create_index("status")
            coll.create_index("created_at")
        elif name == "security_events":
            coll.create_index("event_id", unique=True)
            coll.create_index("user_id")
            coll.create_index("created_at")
            coll.create_index("type")


# ---------------------------------------------------------------------------
# Global Bans
# ---------------------------------------------------------------------------


async def create_global_ban(
    user_id: int,
    banned_by: int,
    reason: str,
    source: str = "manual",
    case_id: Optional[str] = None,
) -> dict[str, Any]:
    """Create a new global ban entry.

    Returns the stored document (without ``_id`` leakage).
    """
    doc = {
        "user_id": user_id,
        "banned_by": banned_by,
        "reason": reason,
        "source": source,
        "case_id": case_id,
        "is_active": True,
        "banned_at": int(time.time()),
    }
    await mongo.db[COLLECTIONS["global_bans"]].replace_one(
        {"user_id": user_id}, doc, upsert=True
    )
    return doc


async def get_global_ban(user_id: int) -> Optional[dict[str, Any]]:
    """Return the active global ban document for *user_id*, or ``None``."""
    return await mongo.db[COLLECTIONS["global_bans"]].find_one({"user_id": user_id, "is_active": True})


async def list_global_bans(active_only: bool = True) -> list[dict[str, Any]]:
    """List global bans.  ``active_only=True`` excludes inactive entries."""
    q = {"is_active": True} if active_only else {}
    cursor = mongo.db[COLLECTIONS["global_bans"]].find(q)
    return await cursor.to_list(length=None)


async def remove_global_ban(user_id: int) -> bool:
    """Soft‑delete a global ban (set ``is_active`` to ``False``)."""
    result = await mongo.db[COLLECTIONS["global_bans"]].update_one(
        {"user_id": user_id}, {"$set": {"is_active": False}}
    )
    return result.modified_count > 0


# ---------------------------------------------------------------------------
# Security Cases
# ---------------------------------------------------------------------------


async def create_case(
    case_id: str,
    user_id: int,
    title: str,
    detail: str,
    created_by: int,
    severity: str = "medium",
) -> dict[str, Any]:
    """Persist a new security case."""

    doc = {
        "case_id": case_id,
        "user_id": user_id,
        "title": title,
        "detail": detail,
        "created_by": created_by,
        "severity": severity,
        "status": "open",
        "created_at": int(time.time()),
    }
    await mongo.db[COLLECTIONS["security_cases"]].replace_one(
        {"case_id": case_id}, doc, upsert=True
    )
    return doc


async def get_case(case_id: str) -> Optional[dict[str, Any]]:
    """Return the case document, or ``None`` if not found."""
    return await mongo.db[COLLECTIONS["security_cases"]].find_one({"case_id": case_id})


async def list_cases(
    user_id: Optional[int] = None,
    severity: Optional[str] = None,
    status: Optional[str] = None,
) -> list[dict[str, Any]]:
    """List security cases with optional filters."""
    q: dict[str, Any] = {}
    if user_id is not None:
        q["user_id"] = user_id
    if severity:
        q["severity"] = severity
    if status:
        q["status"] = status

    cursor = mongo.db[COLLECTIONS["security_cases"]].find(q)
    return await cursor.to_list(length=None)


async def update_case(
    case_id: str,
    *,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    resolved_by: Optional[int] = None,
) -> bool:
    """Update a case's status / severity / resolver."""
    q = {"case_id": case_id}
    s: dict[str, Any] = {}
    if status is not None:
        s["status"] = status
    if severity is not None:
        s["severity"] = severity
    if resolved_by is not None:
        s["resolved_by"] = resolved_by
        s["resolved_at"] = int(time.time())

    if not s:
        return False

    result = await mongo.db[COLLECTIONS["security_cases"]].update_one(q, {"$set": s})
    return result.modified_count > 0


# ---------------------------------------------------------------------------
# Security Dumps
# ---------------------------------------------------------------------------


async def create_dump(
    dump_id: str,
    user_id: int,
    dump_type: str,
    reason: str,
    snapshot: dict[str, Any],
    case_id: Optional[str] = None,
) -> dict[str, Any]:
    """Persist a new security dump *before* any destructive economy reset."""

    doc = {
        "dump_id": dump_id,
        "user_id": user_id,
        "dump_type": dump_type,
        "reason": reason,
        "snapshot": snapshot,
        "case_id": case_id,
        "status": "active",
        "created_at": int(time.time()),
    }
    await mongo.db[COLLECTIONS["security_dumps"]].replace_one(
        {"dump_id": dump_id}, doc, upsert=True
    )
    return doc


async def get_dump(dump_id: str) -> Optional[dict[str, Any]]:
    """Return the dump document, or ``None`` if not found."""
    return await mongo.db[COLLECTIONS["security_dumps"]].find_one({"dump_id": dump_id, "status": "active"})


async def list_dumps(
    user_id: Optional[int] = None,
    dump_type: Optional[str] = None,
    status: Optional[str] = None,
) -> list[dict[str, Any]]:
    """List security dumps with optional filters."""
    q: dict[str, Any] = {}
    if user_id is not None:
        q["user_id"] = user_id
    if dump_type:
        q["dump_type"] = dump_type
    if status:
        q["status"] = status

    cursor = mongo.db[COLLECTIONS["security_dumps"]].find(q)
    return await cursor.to_list(length=None)


async def update_dump(
    dump_id: str,
    *,
    status: Optional[str] = None,
    used_by: Optional[int] = None,
    used_at: Optional[int] = None,
) -> bool:
    """Mark a dump as used / consumed."""
    q = {"dump_id": dump_id}
    s: dict[str, Any] = {}
    if status is not None:
        s["status"] = status
    if used_by is not None:
        s["used_by"] = used_by
    if used_at is not None:
        s["used_at"] = used_at

    if not s:
        return False

    result = await mongo.db[COLLECTIONS["security_dumps"]].update_one(q, {"$set": s})
    return result.modified_count > 0


# ---------------------------------------------------------------------------
# Security Events
# ---------------------------------------------------------------------------


async def create_event(
    event_id: str,
    event_type: str,
    user_id: int,
    actor_id: int,
    details: dict[str, Any],
    case_id: Optional[str] = None,
    dump_id: Optional[str] = None,
) -> dict[str, Any]:
    """Persist a security event (ban, dump creation, recovery, etc.)."""

    doc = {
        "event_id": event_id,
        "type": event_type,
        "user_id": user_id,
        "actor_id": actor_id,
        "details": details,
        "case_id": case_id,
        "dump_id": dump_id,
        "created_at": int(time.time()),
    }
    await mongo.db[COLLECTIONS["security_events"]].replace_one(
        {"event_id": event_id}, doc, upsert=True
    )
    return doc


async def list_events(
    user_id: Optional[int] = None,
    event_type: Optional[str] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List recent security events, newest first."""
    q: dict[str, Any] = {}
    if user_id is not None:
        q["user_id"] = user_id
    if event_type:
        q["type"] = event_type

    cursor = mongo.db[COLLECTIONS["security_events"]].find(q).sort("created_at", -1)
    return await cursor.limit(limit).to_list(length=None)


# ---------------------------------------------------------------------------
# Convenience: short‑hand for the most common-case dump creation
# ---------------------------------------------------------------------------


async def create_security_dump_user(
    user_id: int,
    dump_type: str = "manual",
    reason: str = "Manual security clear",
    snapshot: Optional[dict[str, Any]] = None,
    case_id: Optional[str] = None,
) -> dict[str, Any]:
    """Create a dump for a user — the call‑site generates a UUID‑based ID."""

    import uuid

    dump_id = f"DUMP-{uuid.uuid4().hex[:8].upper()}"
    snap = snapshot or {}
    # Ensure we always have a user snapshot even if caller forgot
    snap.setdefault("wallet", None)
    snap.setdefault("bank", None)
    snap.setdefault("stocks", {})
    snap.setdefault("assets", {})

    return await create_dump(dump_id, user_id, dump_type, reason, snap, case_id)