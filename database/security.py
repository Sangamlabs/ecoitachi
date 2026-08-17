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
    "security_quarantines": "security_quarantines",
    "security_events": "security_events",
    "security_recovery": "security_recovery",
    "security_quarantines": "security_quarantines",

    "global_bans": "global_bans",
    "security_cases": "security_cases",
    "security_dumps": "security_dumps",
    "security_quarantines": "security_quarantines",
    "security_events": "security_events",
    "security_quarantines": "security_quarantines",
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
        elif name == "security_recovery":
            coll.create_index("user_id", unique=True)
            coll.create_index("last_dump_id")


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


async def restore_from_dump(dump_id: str, target_user_id: int, actor_id: int) -> tuple[bool, str]:
    """Restore a user's economy from a saved security dump.

    Manual flow: admin uses the dump_id (recovery_id) to restore a previously
    saved economy snapshot. Logs the recovery action in the audit log.

    Returns ``(ok, message)`` tuple.
    """
    dump = await get_dump(dump_id)
    if not dump:
        return False, f"Dump <code>{dump_id}</code> not found."

    snapshot = dump.get("snapshot", {})
    reason = dump.get("reason", "Manual recovery from dump")

    from services import economy as econ
    # Apply the snapshot to restore the user's economy state
    restored = False
    applied = []

    if snapshot.get("wallet") is not None:
        await econ.set_user_balance(target_user_id, "wallet", int(snapshot["wallet"]))
        restored = True
        applied.append("wallet")
    bank_value = snapshot.get("bank", snapshot.get("bank_balance"))
    if bank_value is not None:
        await econ.set_user_balance(target_user_id, "bank", int(bank_value))
        restored = True
        applied.append("bank")
    for sym, qty in (snapshot.get("stocks") or {}).items():
        if qty is not None:
            await econ.set_user_stock(target_user_id, sym, int(qty))
            restored = True
            applied.append("stocks")
    for aid, info in (snapshot.get("assets") or {}).items():
        qty = info.get("quantity", 0) if isinstance(info, dict) else info
        if qty is not None and qty > 0:
            await econ.set_user_asset(target_user_id, aid, int(qty))
            restored = True
            applied.append("assets")

    # Log the recovery action
    from database import security as sec_db
    await sec_db.create_event(
        event_id=f"EVT-{__import__('uuid').uuid4().hex[:8].upper()}",
        event_type="recovery_from_dump",
        user_id=target_user_id,
        actor_id=actor_id,
        details={
            "dump_id": dump_id,
            "restored_fields": applied,
            "reason": reason,
        },
    )

    if restored:
        return True, f"Recovered economy for user {target_user_id}. Fields restored: {', '.join(applied)}."
    else:
        return False, "Nothing was restored from the dump."



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

# ---------------------------------------------------------------------------
# Quarantine State
# ---------------------------------------------------------------------------

async def set_quarantine(user_id: int, is_quarantined: bool, reason: str = "", case_id: Optional[str] = None) -> None:
    """Set quarantine state for a user."""
    doc = {
        "user_id": user_id,
        "is_quarantined": is_quarantined,
        "reason": reason,
        "case_id": case_id,
        "quarantined_at": int(time.time()),
        "updated_at": int(time.time()),
    }
    await mongo.db[COLLECTIONS["security_quarantines"]].replace_one(
        {"user_id": user_id}, doc, upsert=True
    )

async def get_quarantine(user_id: int) -> Optional[dict[str, Any]]:
    """Return the quarantine document for *user_id*, or ``None``."""
    return await mongo.db[COLLECTIONS["security_quarantines"]].find_one({"user_id": user_id})

async def get_quarantine_info(user_id: int) -> Optional[dict[str, Any]]:
    """Return quarantine info for a user."""
    doc = await get_quarantine(user_id)
    if doc:
        return {
            "is_quarantined": doc.get("is_quarantined", False),
            "reason": doc.get("reason", ""),
            "case_id": doc.get("case_id"),
            "quarantined_at": doc.get("quarantined_at"),
            "updated_at": doc.get("updated_at"),
        }
    return None

async def list_quarantined_users() -> list[dict[str, Any]]:
    """List all quarantined users."""
    cursor = mongo.db[COLLECTIONS["security_quarantines"]].find({"is_quarantined": True})
    return await cursor.to_list(length=None)

async def remove_quarantine(user_id: int) -> bool:
    """Remove quarantine state for a user."""
    result = await mongo.db[COLLECTIONS["security_quarantines"]].update_one(
        {"user_id": user_id}, {"$set": {"is_quarantined": False, "updated_at": int(time.time())}}
    )
    return result.modified_count > 0


# ---------------------------------------------------------------------------
# Recovery State (security/recovery layer — NOT the economy service)
# ---------------------------------------------------------------------------


async def reset_recovery_balance(
    user_id: int,
    default_balance: int,
    dump_id: str | None = None,
    operator_id: int | None = None,
) -> dict[str, Any]:
    """Record the manual security clear's recovery state for *user_id*.

    Security/recovery state is owned by this layer.  The actual economy reset
    is performed by the Economy Service (which owns balances); this method
    only persists the recovery metadata used by ``/data`` and audit.
    """
    doc = {
        "user_id": user_id,
        "recovery_balance": int(default_balance),
        "last_dump_id": dump_id,
        "cleared_by": operator_id,
        "cleared_at": int(time.time()),
        "updated_at": int(time.time()),
    }
    await mongo.db[COLLECTIONS["security_recovery"]].replace_one(
        {"user_id": user_id}, doc, upsert=True
    )
    return doc


async def get_recovery_balance(user_id: int) -> Optional[dict[str, Any]]:
    """Return the recovery state document for *user_id*, or ``None``."""
    return await mongo.db[COLLECTIONS["security_recovery"]].find_one({"user_id": user_id})

