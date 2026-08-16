"""Centralized SecurityService — global ban, quarantine, secret detection,
economy integrity, dump creation / recovery, and audit logging.

All handlers must NOT contain business logic.  They must only:
    1. Parse the command / resolve targets
    2. Check permission (owner_only / sudo_or_owner)
    3. Call a Service method
    4. Send the HTML response
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Dict, List, Optional, Set

from pyrogram import Client, filters
from pyrogram.types import Message

from database import security as sec_db
from services import settings as settings_service
from utils.permissions import is_owner, is_sudo
from utils.sender import reply_html
from utils.messages import error, success, info

logger = logging.getLogger("security")


# ---------------------------------------------------------------------------
# Secret‑detection configuration
# ---------------------------------------------------------------------------

SECRET_PATTERNS: Dict[str, re.Pattern[str]] = {
    "bot_token": re.compile(r"^\d{3,5}:[a-zA-Z0-9_-]{35}$"),
    "api_key": re.compile(r"(?i)key\s*[:=]\s*[A-Za-z0-9]{20,}"),
    "api_secret": re.compile(r"(?i)secret\s*[:=]\s*[A-Za-z0-9]{20,}"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPEN )?PRIVATE KEY-----"),
    "mongo_uri": re.compile(r"mongodb://[^\s]+|mongodb+srv://[^\s]+"),
    "session_string": re.compile(r"^[0-9A-Za-z_\-\+]{30,}$"),
}

SECURITY_KEYWORDS: List[str] = [
    "token", "api_key", "secret", "private_key", "mongo_uri", "session"
]


def _contains_secret(text: str) -> Optional[str]:
    """Return the pattern key if a secret is detected, else ``None``."""
    for ptype, pattern in SECRET_PATTERNS.items():
        if pattern.search(text):
            return ptype
    lowered = text.lower()
    for kw in SECURITY_KEYWORDS:
        if re.search(rf"\b{re.escape(kw)}\b", lowered):
            return kw
    return None


# ---------------------------------------------------------------------------
# Global Ban Service
# ---------------------------------------------------------------------------


async def global_ban_check(user_id: int) -> tuple[bool, Optional[str]]:
    """Return ``(is_banned, reason)`` for a user."""
    from database import security as sec_db
    doc = await sec_db.get_global_ban(user_id)
    if doc:
        return True, doc.get("reason")
    return False, None


async def global_ban(
    user_id: int,
    reason: str,
    banned_by: int,
    source: str = "manual",
    case_id: Optional[str] = None,
) -> dict[str, Any]:
    """Create / renew a global ban for *user_id*."""
    # Soft‑delete any existing ban
    await sec_db.remove_global_ban(user_id)

    # If a critical ban, create a security case first
    if "exploit" in reason.lower() or "critical" in reason.lower():
        case_id = case_id or f"CASE-{uuid.uuid4().hex[:8].upper()}"
        await sec_db.create_case(
            case_id=case_id,
            user_id=user_id,
            title="Global ban – critical security violation",
            detail=reason,
            created_by=banned_by,
            severity="high",
        )

    ban_doc = await sec_db.create_global_ban(
        user_id=user_id,
        banned_by=banned_by,
        reason=reason,
        source=source,
        case_id=case_id,
    )

    # Create a security event
    await sec_db.create_event(
        event_id=f"EVT-{uuid.uuid4().hex[:8].upper()}",
        event_type="global_ban",
        user_id=user_id,
        actor_id=banned_by,
        details={
            "reason": reason,
            "source": source,
            "case_id": case_id,
        },
    )

    # Quarantine the user's economy
    from services import economy as econ
    try:
        await econ.quarantine_user(user_id)
    except Exception:
        logger.warning("Failed to quarantine user %d during global ban", user_id)

    return ban_doc


async def global_unban(user_id: int) -> bool:
    """Remove a global ban (soft‑delete)."""
    result = await sec_db.remove_global_ban(user_id)
    if result:
        from services import economy as econ
        await econ.clear_quarantine(user_id)
    return result


# ---------------------------------------------------------------------------
# Security Cases Service
# ---------------------------------------------------------------------------


async def create_security_case(
    user_id: int,
    title: str,
    detail: str,
    created_by: int,
    severity: str = "medium",
) -> str:
    """Create a new security case and return the ``case_id``."""
    case_id = f"CASE-{uuid.uuid4().hex[:8].upper()}"
    await sec_db.create_case(
        case_id=case_id,
        user_id=user_id,
        title=title,
        detail=detail,
        created_by=created_by,
        severity=severity,
    )
    return case_id


async def list_security_cases(
    user_id: Optional[int] = None,
    severity: Optional[str] = None,
) -> list[dict[str, Any]]:
    """List security cases, optionally filtered by user or severity."""
    return await sec_db.list_cases(user_id=user_id, severity=severity)


# ---------------------------------------------------------------------------
# Security Dumps Service
# ---------------------------------------------------------------------------


async def create_security_dump(
    user_id: int,
    dump_type: str = "manual",
    reason: str = "Manual security clear",
    case_id: Optional[str] = None,
    snapshot: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Create a security dump *before* any destructive economy operation."""
    import uuid as _uuid

    dump_id = f"DUMP-{_uuid.uuid4().hex[:8].upper()}"

    from services import economy as econ
    if snapshot is None:
        try:
            snap = await econ.get_user_economy_snapshot(user_id)
        except Exception:
            snap = {"wallet": None, "bank": None, "stocks": {}, "assets": {}}
    else:
        snap = snapshot

    return await sec_db.create_dump(
        dump_id=dump_id,
        user_id=user_id,
        dump_type=dump_type,
        reason=reason,
        snapshot=snap,
        case_id=case_id,
    )


async def list_security_dumps(
    user_id: Optional[int] = None,
    dump_type: Optional[str] = None,
) -> list[dict[str, Any]]:
    """List security dumps, optionally filtered by user or type."""
    return await sec_db.list_dumps(user_id=user_id, dump_type=dump_type)


async def restore_from_dump(dump_id: str, target_user_id: int) -> bool:
    """Atomic recovery from a security dump."""
    dump = await sec_db.get_dump(dump_id)
    if not dump:
        return False
    if dump.get("status") != "active":
        return False

    from services import economy as econ
    snapshot = dump.get("snapshot", {})

    try:
        if snapshot.get("wallet") is not None:
            await econ.set_user_balance(target_user_id, "wallet", int(snapshot["wallet"]))
        if snapshot.get("bank") is not None:
            await econ.set_user_balance(target_user_id, "bank", int(snapshot["bank"]))
        stocks = snapshot.get("stocks", {})
        for sym, qty in stocks.items():
            if qty is not None:
                await econ.set_user_stock(target_user_id, sym, int(qty))
        assets = snapshot.get("assets", {})
        for aid, info in assets.items():
            qty = info.get("quantity", 0) if isinstance(info, dict) else info
            if qty is not None and qty > 0:
                await econ.set_user_asset(target_user_id, aid, int(qty))
    except Exception as e:
        logger.error("Failed to restore dump %s: %s", dump_id, e)
        return False

    await sec_db.update_dump(dump_id, status="used", used_by=target_user_id, used_at=int(time.time()))

    await sec_db.create_event(
        event_id=f"EVT-{uuid.uuid4().hex[:8].upper()}",
        event_type="recovery_from_dump",
        user_id=target_user_id,
        actor_id=dump.get("user_id", 0),
        details={"dump_id": dump_id, "restored_to": target_user_id},
    )
    return True


# ---------------------------------------------------------------------------
# Quarantine Service
# ---------------------------------------------------------------------------


async def quarantine_check(user_id: int) -> bool:
    from services import economy as econ
    return await econ.is_quarantined(user_id)


async def quarantine_user(user_id: int, reason: str = "Security quarantine") -> bool:
    from services import economy as econ
    return await econ.quarantine_user(user_id, reason)


async def clear_quarantine(user_id: int) -> bool:
    from services import economy as econ
    return await econ.clear_quarantine(user_id)


# ---------------------------------------------------------------------------
# Secret Detection on Messages
# ---------------------------------------------------------------------------


async def handle_secret_detection(client: Client, message: Message) -> Optional[dict[str, Any]]:
    """Handle a message that contains a detected secret."""
    if not message.from_user:
        return None
    user_id = message.from_user.id
    text = message.text or ""

    if not text:
        return None

    detected_type = _contains_secret(text)
    if not detected_type:
        return None

    await sec_db.create_event(
        event_id=f"EVT-{uuid.uuid4().hex[:8].upper()}",
        event_type="secret_leak",
        user_id=user_id,
        actor_id=user_id,
        details={
            "secret_type": detected_type,
            "message_id": message.message_id,
            "chat_id": message.chat.id if message.chat else 0,
        },
    )

    high_confidence: Set[str] = {"bot_token", "mongo_uri", "private_key"}
    if detected_type in high_confidence:
        if not await is_owner(user_id):
            try:
                await global_ban(
                    user_id=user_id,
                    reason=f"Critical secret leak ({detected_type})",
                    banned_by=user_id,
                    source="auto_detection",
                )
                try:
                    from services import economy as econ
                    await econ.quarantine_user(user_id, "Critical secret leak")
                except Exception:
                    pass
            except Exception as e:
                logger.error("Failed to auto-ban user %d for secret leak: %s", user_id, e)

    return {
        "secret_type": detected_type,
        "user_id": user_id,
        "message_id": message.message_id,
        "action_taken": detected_type in high_confidence,
    }


# ---------------------------------------------------------------------------
# Economy Integrity Service
# ---------------------------------------------------------------------------


async def check_economy_integrity(user_id: int) -> Optional[dict[str, Any]]:
    from services import economy as econ
    return await econ.check_user_balance_integrity(user_id)


# ---------------------------------------------------------------------------
# Manual /clear Service
# ---------------------------------------------------------------------------


async def manual_clear(user_id: int, target_user_id: Optional[int] = None) -> tuple[bool, str]:
    """Owner‑only manual clear of a user's recovery balance."""
    if target_user_id is None:
        target_user_id = user_id
    clear_balance = await settings_service.get_clear_recovery_balance()
    from services import economy as econ
    ok = await econ.reset_recovery_balance(target_user_id, clear_balance)
    if ok:
        return True, f"Recovery balance cleared for user {target_user_id}. Default set to {clear_balance}."
    else:
        return False, "Nothing to clear or user not found."


# ---------------------------------------------------------------------------
# Manual Dump / Restore Service
# ---------------------------------------------------------------------------


async def manual_dump_user(user_id: int, reason: str = "Manual dump before clear") -> dict[str, Any]:
    """Owner creates a manual dump of a user's economy state."""
    from services import economy as econ
    snapshot = await econ.get_user_economy_snapshot(user_id)
    return await create_security_dump(
        user_id=user_id,
        dump_type="manual",
        reason=reason,
        snapshot=snapshot,
    )


async def manual_restore(dump_id: str, operator_id: int) -> tuple[bool, str]:
    """Owner‑only restore from a dump."""
    if not await is_owner(operator_id):
        return False, "Only the bot owner can restore from security dumps."
    success = await restore_from_dump(dump_id, operator_id)
    if success:
        return True, f"Successfully restored from dump <code>{dump_id}</code>."
    else:
        return False, f"Dump <code>{dump_id}</code> not found, already used, or restore failed."


async def manual_restorecase(case_id: str, operator_id: int) -> tuple[bool, str]:
    """Owner‑only restore from a security case."""
    if not await is_owner(operator_id):
        return False, "Only the bot owner can restore from security cases."
    case = await sec_db.get_case(case_id)
    if not case:
        return False, f"Case <code>{case_id}</code> not found."
    from services import economy as econ
    user_id = case["user_id"]
    detail = case.get("detail", "").lower()
    if "recovery" in detail or "restore" in detail:
        ok, msg = await manual_clear(operator_id, user_id)
        if ok:
            return True, f"Recovered user {user_id} from case <code>{case_id}</code>."
        return False, msg
    return False, f"Case <code>{case_id}</code> does not have a recoverable action."


# ---------------------------------------------------------------------------
# SUDO Security Helper
# ---------------------------------------------------------------------------


async def check_sudo_security(user_id: int, action: str = "unknown") -> bool:
    """Check whether a SUDO admin is permitted to perform *action*."""
    if await is_owner(user_id):
        return True
    if await is_sudo(user_id):
        blocked_commands: Set[str] = {"clear", "restore", "recover", "restorecase", "dumpinfo"}
        if action in blocked_commands:
            return False
        return True
    return False
