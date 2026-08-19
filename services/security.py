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
import time
import uuid
from typing import Any, Dict, List, Optional, Set

from pyrogram import Client, filters
from pyrogram.types import Message

from database import security as sec_db
from database import users as users_db
from services import settings as settings_service
from utils.permissions import is_owner, is_sudo
from utils.messages import error, success, info
from utils.sender import reply_html

logger = logging.getLogger("security")


# ---------------------------------------------------------------------------
# Quarantine State — stored in security_global_bans collection
# ---------------------------------------------------------------------------

async def quarantine_check(user_id: int) -> bool:
    """Return ``True`` if the user is currently quarantined."""
    doc = await sec_db.get_quarantine(user_id)
    return doc is not None and doc.get("is_quarantined", False)


async def quarantine_user(user_id: int, reason: str = "Security quarantine") -> bool:
    """Quarantine a user — blocks economy commands globally."""
    await sec_db.set_quarantine(user_id, True, reason)
    return True


async def clear_quarantine(user_id: int) -> bool:
    """Clear a user's quarantine status."""
    await sec_db.set_quarantine(user_id, False)
    return True


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

# Quarantine is NO LONGER automatic during global ban.
    # Users are only quarantined via explicit /quarantine command by an admin.
    # This prevents economy data from being modified without intent.

    return ban_doc


async def global_unban(user_id: int) -> bool:
    """Remove a global ban (soft‑delete)."""
    result = await sec_db.remove_global_ban(user_id)
    if result:
        # Also clear quarantine flag via security service
        await sec_db.set_quarantine(user_id, False)
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

    dump_id = f"DUMP-{uuid.uuid4().hex[:8].upper()}"

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
        logger.error("Failed to restore dump %s for user %d: %s", dump_id, target_user_id, e)
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
# Economy Integrity Service
# ---------------------------------------------------------------------------


async def check_economy_integrity(user_id: int) -> Optional[dict[str, Any]]:
    from services import economy as econ
    return await econ.check_user_balance_integrity(user_id)


# ---------------------------------------------------------------------------
# Manual /clear Service
# ---------------------------------------------------------------------------


async def manual_clear(operator_id: int, target_user_id: Optional[int] = None) -> tuple[bool, str, str | None]:
    """Owner-only manual clear of a user's economy after a full backup dump.

    Flow:
        1. snapshot the user's economy,
        2. create a recovery dump + generate a recovery ID (dump_id),
        3. record an audit event,
        4. reset the user's current economy state through the Economy Service:
           wallet -> ``starting_balance`` (never a security/recovery sentinel),
           bank -> 0, stock/asset holdings removed, cached value fields
           refreshed — so /bal, /profile, /topbank and /leader all read the
           fresh authoritative state immediately,
        5. persist recovery state in the security/recovery database layer,
        6. fresh-DB verification — recompute net worth from the authoritative
           database and confirm it equals the expected cleared value,
        7. return ``(ok, message, recovery_id)``.

    Historical earnings statistics and loan liability state are intentionally
    left untouched; the recovery dump (snapshot) fully covers wallet, bank,
    stocks and assets so /recover and /restore remain fully usable.

    The economy reset is performed through the Economy Service (owner of
    balances); recovery state is owned by the security/recovery DB layer.
    """
    if target_user_id is None:
        target_user_id = operator_id

    reset_wallet = await settings_service.get_starting_balance()

    # 1 + 2. Snapshot + dump (generates the recovery ID).
    dump = await manual_dump_user(
        target_user_id,
        reason=f"/clear by admin {operator_id}",
    )
    recovery_id = dump["dump_id"]

    # 3. Audit — clear initiated.
    await sec_db.create_event(
        event_id=f"EVT-{uuid.uuid4().hex[:8].upper()}",
        event_type="recovery_clear_initiated",
        user_id=target_user_id,
        actor_id=operator_id,
        details={"dump_id": recovery_id, "action": "/clear", "reset_wallet": reset_wallet},
    )

    # 4. Reset the user's current economy state through the Economy Service.
    from services import economy as econ

    if await users_db.user_exists(target_user_id):
        await econ.clear_economy(target_user_id, reset_wallet)
        ok = True
    else:
        ok = False

    # 5. Persist recovery state in the security/recovery DB layer.
    await sec_db.reset_recovery_balance(
        target_user_id, reset_wallet, dump_id=recovery_id, operator_id=operator_id
    )

    # 3 (continued). Audit — clear completed.
    await sec_db.create_event(
        event_id=f"EVT-{uuid.uuid4().hex[:8].upper()}",
        event_type="recovery_balance_cleared",
        user_id=target_user_id,
        actor_id=operator_id,
        details={"dump_id": recovery_id, "reset_wallet": reset_wallet, "action": "/clear"},
    )

    # 6. Fresh-DB verification — every ranking (/leader, /topbank, /profile,
    #    /bal) reads the authoritative database, so recompute the cleared net
    #    worth from fresh values and confirm it matches the expected state.
    verified = None
    if ok:
        try:
            from database import loans as loans_db
            from services import leaderboard as lb_service

            fresh_user = await users_db.get_user(target_user_id)
            fresh_net = await lb_service.net_worth(fresh_user) if fresh_user else None
            expected_net = reset_wallet - await loans_db.get_outstanding(target_user_id)
            verified = fresh_net == expected_net
            if not verified:
                logger.error(
                    "Post-clear verification FAILED user=%s dump=%s fresh_net=%s expected=%s",
                    target_user_id, recovery_id, fresh_net, expected_net,
                )
        except Exception:
            logger.exception(
                "Post-clear verification failed user=%s dump=%s",
                target_user_id, recovery_id,
            )

    if ok:
        if verified:
            check = "Verified against fresh database state."
        elif verified is None:
            check = "Post-clear verification could not run — see logs."
        else:
            check = "WARNING: post-clear verification mismatch — see logs."
        return (
            True,
            f"User <code>{target_user_id}</code> cleared.\n"
            f"Recovery ID: <code>{recovery_id}</code> (use <code>/recover {recovery_id}</code> to restore).\n"
            f"Wallet reset to {reset_wallet}; bank and holdings reset to 0.\n"
            f"{check}",
            recovery_id,
        )
    return False, f"User <code>{target_user_id}</code> not found.", None


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
    """Owner-only restore from a dump back to the dump's original user."""
    if not await is_owner(operator_id):
        return False, "Only the bot owner can restore from security dumps."
    dump = await sec_db.get_dump(dump_id)
    if not dump:
        return False, f"Dump <code>{dump_id}</code> not found, already used, or restore failed."
    target_user_id = dump.get("user_id")
    success = await restore_from_dump(dump_id, target_user_id)
    if success:
        return True, f"Successfully restored economy for user <code>{target_user_id}</code> from dump <code>{dump_id}</code>."
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
        ok, msg, _recovery_id = await manual_clear(operator_id, user_id)
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


# ---------------------------------------------------------------------------
# Secret Detection on Messages
# ---------------------------------------------------------------------------


async def handle_secret_detection(client: Client, message: Message) -> Optional[dict[str, Any]]:
    """Handle a message that contains a detected secret.

    Now LOGGING-ONLY: detects and records secret leaks in the audit log,
    but does NOT automatically ban, quarantine, or modify economy data.
    Manual admin action required for any enforcement.
    """
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
            "message_id": message.id,
            "chat_id": message.chat.id if message.chat else 0,
        },
    )

    # LOGGING ONLY - no automatic enforcement
    # Manual admin action required for ban/quarantine/economy modifications

    return {
        "secret_type": detected_type,
        "user_id": user_id,
        "message_id": message.id,
        "action_taken": False,  # No automatic action taken
    }
