"""Security service: secret detection, global bans, quarantine, economy integrity."""

from __future__ import annotations

import logging
import re
import secrets
import time
import uuid
from typing import Any

from database import security as sec_db, users as users_db
from database.mongo import mongo
from services import economy, settings as settings_service, transaction as tx_service
from services.economy import ensure_active
from utils.money import format_money

logger = logging.getLogger(__name__)

# Secret patterns for high-confidence detection
SECRET_PATTERNS = [
    (r"[0-9]{8,10}:[a-zA-Z0-9_-]{35}", "Telegram Bot Token"),
    (r"[a-fA-F0-9]{32}", "API Key / Hash (32-char hex)"),
    (r"[a-zA-Z0-9_-]{20,}", "API Secret / Session String"),
    (r"mongodb://[^\s]+", "MongoDB URI"),
    (r"postgres://[^\s]+", "PostgreSQL URI"),
    (r"redis://[^\s]+", "Redis URI"),
    (r"-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]*?-----END [A-Z ]+PRIVATE KEY-----", "Private Key"),
    (r"[a-zA-Z0-9+/]{40,}={0,2}", "Base64 encoded secret"),
]


async def get_security_config() -> dict[str, Any]:
    return await settings_service.get_settings()


async def _is_owner(user_id: int) -> bool:
    from config import config
    return user_id == config.OWNER_ID


async def _is_sudo(user_id: int) -> bool:
    from database import admins as admins_db
    if await _is_owner(user_id):
        return True
    return await admins_db.is_sudo(user_id)


async def detect_secret(text: str) -> tuple[bool, str | None]:
    """Detect high-confidence secrets in text. Returns (found, secret_type)."""
    if not text:
        return False, None
    for pattern, secret_type in SECRET_PATTERNS:
        if re.search(pattern, text):
            return True, secret_type
    return False, None


async def _notify_owner(client, text: str) -> None:
    from config import config
    from utils.sender import send_html
    try:
        await send_html(client, config.OWNER_ID, text)
    except Exception:
        logger.exception("Failed to notify owner")


async def _log_security_event(
    event_type: str,
    user_id: int,
    actor_id: int | None,
    case_id: str | None = None,
    dump_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    event_id = f"EVT-{secrets.token_hex(4).upper()}"
    await sec_db.create_security_event(
        event_id=event_id,
        event_type=event_id,
        user_id=user_id,
        actor_id=actor_id,
        case_id=case_id,
        dump_id=dump_id,
        metadata=metadata,
    )
    return event_id


async def _create_global_ban(
    user_id: int,
    reason: str,
    banned_by: int,
    case_id: str | None = None,
) -> str:
    ban_id = f"GB-{secrets.token_hex(4).upper()}"
    await sec_db.create_global_ban(
        ban_id=ban_id,
        user_id=user_id,
        reason=reason,
        banned_by=banned_by,
        case_id=case_id,
    )
    await users_db.set_user_flags(user_id, is_banned=True)
    return ban_id


async def _quarantine_account(user_id: int, case_id: str, reason: str) -> None:
    await users_db.set_user_flags(user_id, is_frozen=True)
    await sec_db.update_case(case_id, status="quarantined", resolved_at=int(time.time()))


async def _reset_live_economy(user_id: int, recovery_balance: int = 20000) -> None:
    """Reset live economy to recovery balance. Returns previous balances."""
    user = await users_db.get_user(user_id)
    if not user:
        return
    await mongo.db[users_db.COLLECTION].update_one(
        {"user_id": user_id},
        {
            "$set": {
                "wallet": recovery_balance,
                "bank": 0,
                "is_frozen": True,
                "updated_at": int(time.time()),
            }
        },
    )
    # Clear stock holdings
    await mongo.db["stock_holdings"].delete_many({"user_id": user_id})
    # Clear asset holdings
    await mongo.db["asset_holdings"].delete_many({"user_id": user_id})
    # Clear inventory
    await mongo.db["inventory"].delete_many({"user_id": user_id})


async def _flag_suspicious_transactions(user_id: int, case_id: str) -> int:
    result = await mongo.db["transactions"].update_many(
        {"user_id": user_id, "security_flagged": {"$ne": True}},
        {"$set": {"security_flagged": True, "case_id": case_id}},
    )
    return result.modified_count


async def _create_security_case(
    case_id: str,
    user_id: int,
    case_type: str,
    reason: str,
    created_by: int | None = None,
    dump_id: str | None = None,
) -> dict[str, Any]:
    return await sec_db.create_case(
        case_id=case_id,
        user_id=user_id,
        case_type=case_type,
        reason=reason,
        created_by=created_by,
        dump_id=dump_id,
    )


async def handle_secret_detection(
    client,
    message,
    user_id: int,
    text: str,
) -> bool:
    """Handle secret detection. Returns True if secret was detected and handled."""
    if await _is_owner(user_id):
        # Owner immunity - log only, notify owner
        case_id = f"CASE-{secrets.token_hex(4).upper()}"
        await _create_security_case(case_id, user_id, "SECRET_LEAK_OWNER", "Owner shared secret (logged only)")
        await _log_security_event("SECRET_DETECTED_OWNER", user_id, user_id, case_id=case_id)
        await _notify_owner(
            client,
            f"<b>🚨 CRITICAL SECURITY ALERT</b>\n\n"
            f"<blockquote>"
            f"<b>Event:</b> SECRET_LEAK\n"
            f"<b>User ID:</b> <code>{user_id}</code> (OWNER)\n"
            f"<b>Role:</b> OWNER\n"
            f"<b>Action:</b> LOGGED ONLY (Owner Immunity)\n"
            f"<b>Case:</b> <code>{case_id}</code>\n"
            f"</blockquote>",
        )
        return True

    # Check if sudo
    is_sudo_user = await _is_sudo(user_id)

    found, secret_type = await detect_secret(text)
    if not found:
        return False

    # Delete offending message if possible
    try:
        await message.delete()
    except Exception:
        pass

    case_id = f"CASE-{secrets.token_hex(4).upper()}"
    ban_id = await _create_global_ban(
        user_id=user_id,
        reason=f"Secret leak: {secret_type}",
        banned_by=user_id,  # auto-ban
        case_id=case_id,
    )

    if is_sudo_user:
        # Sudo secret violation: revoke sudo, dump, quarantine, global ban
        from database import admins as admins_db
        await admins_db.remove_sudo(user_id)
        dump_id = await create_security_dump(user_id, case_id, "SECRET_LEAK", "sudo_secret_violation", user_id)
        await _quarantine_account(user_id, case_id, "sudo_secret_violation")
        await _notify_owner(
            client,
            f"<b>🚨 CRITICAL SECURITY ALERT</b>\n\n"
            f"<blockquote>"
            f"<b>Event:</b> SECRET_LEAK\n"
            f"<b>User ID:</b> <code>{user_id}</code>\n"
            f"<b>Role:</b> SUDO ADMIN\n"
            f"<b>Action:</b> SUDO REVOKED + GLOBAL BAN + QUARANTINE\n"
            f"<b>Case:</b> <code>{case_id}</code>\n"
            f"<b>Ban ID:</b> <code>{ban_id}</code>\n"
            f"<b>Dump:</b> <code>{dump_id}</code>\n"
            f"</blockquote>",
        )
    else:
        # Normal user: immediate global ban
        dump_id = await create_security_dump(user_id, case_id, "SECRET_LEAK", "user_secret_leak", user_id)
        await _notify_owner(
            client,
            f"<b>🚨 CRITICAL SECURITY ALERT</b>\n\n"
            f"<blockquote>"
            f"<b>Event:</b> SECRET_LEAK\n"
            f"<b>User ID:</b> <code>{user_id}</code>\n"
            f"<b>Role:</b> USER\n"
            f"<b>Action:</b> GLOBAL BAN\n"
            f"<b>Case:</b> <code>{case_id}</code>\n"
            f"<b>Ban ID:</b> <code>{ban_id}</code>\n"
            f"<b>Dump:</b> <code>{dump_id}</code>\n"
            f"</blockquote>",
        )

    await _log_security_event("SECRET_DETECTED", user_id, user_id, case_id=case_id, dump_id=dump_id)
    return True


async def create_security_dump(
    user_id: int,
    case_id: str | None,
    dump_type: str,
    reason: str,
    created_by: int,
) -> str:
    """Create a complete security dump of user's economy state."""
    user = await users_db.get_user(user_id)
    if not user:
        raise ValueError(f"User {user_id} not found")

    # Get economy snapshot
    wallet = user.get("wallet", 0)
    bank = user.get("bank", 0)

    # Get stock holdings
    stock_holdings = []
    stock_value = 0
    cursor = mongo.db["stock_holdings"].find({"user_id": user_id})
    async for h in cursor:
        stock_holdings.append(h)
        asset = await mongo.db["stocks"].find_one({"symbol": h["symbol"]})
        if asset:
            stock_value += int(asset.get("price", 0)) * h["quantity"]

    # Get asset holdings
    asset_holdings = []
    asset_value = 0
    cursor = mongo.db["asset_holdings"].find({"user_id": user_id})
    async for h in cursor:
        asset_holdings.append(h)
        asset = await mongo.db["assets"].find_one({"symbol": h["symbol"]})
        if asset:
            asset_value += int(asset.get("price", 0)) * h["quantity"]

    # Get inventory
    inventory = []
    cursor = mongo.db["inventory"].find({"user_id": user_id})
    async for h in cursor:
        inventory.append(h)

    # Get recent transactions
    recent_txs = []
    cursor = mongo.db["transactions"].find({"user_id": user_id}).sort("created_at", -1).limit(50)
    async for t in cursor:
        recent_txs.append(t)

    dump_id = f"DUMP-{secrets.token_hex(4).upper()}"
    now = int(time.time())

    # Create security hash from immutable snapshot data
    import hashlib
    snapshot_data = f"{user_id}{wallet}{bank}{stock_value}{asset_value}{case_id}{now}"
    security_hash = hashlib.sha256(snapshot_data.encode()).hexdigest()[:16]

    dump_doc = {
        "dump_id": dump_id,
        "case_id": case_id,
        "original_user_id": user_id,
        "original_username": user.get("username"),
        "original_name": user.get("first_name"),
        "dump_type": dump_type,
        "reason": reason,
        "created_at": now,
        "created_by": created_by,
        "status": "AVAILABLE",
        "security_hash": security_hash,
        "wallet": wallet,
        "bank": bank,
        "stock_holdings": stock_holdings,
        "stock_value": stock_value,
        "asset_holdings": asset_holdings,
        "asset_value": asset_value,
        "inventory": inventory,
        "recent_transactions": recent_txs,
        "profile_config": {k: v for k, v in user.items() if k not in ("_id",)},
        "recovery_status": "NOT_RESTORED",
        "restored_at": None,
        "restored_by": None,
        "restoration_id": None,
    }

    await sec_db.create_dump(dump_doc)
    await _log_security_event("DUMP_CREATED", user_id, created_by, case_id=case_id, dump_id=dump_id)
    return dump_id


async def handle_confirmed_economy_exploit(
    client,
    user_id: int,
    reason: str,
    detection_details: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Handle confirmed economy exploit. Returns (case_id, dump_id)."""
    config = await get_security_config()
    recovery_balance = int(config.get("clear_recovery_balance", 20000))

    case_id = f"CASE-{secrets.token_hex(4).upper()}"
    dump_id = await create_security_dump(user_id, case_id, "AUTO", reason, user_id)

    # Flag suspicious transactions
    await _flag_suspicious_transactions(user_id, case_id)

    # Quarantine account
    await _quarantine_account(user_id, case_id, reason)

    # Reset live economy
    await _reset_live_economy(user_id, recovery_balance)

    # Global ban if configured
    if config.get("global_ban_on_exploit", True):
        await _create_global_ban(user_id, f"Economy exploit: {reason}", user_id, case_id)

    await _notify_owner(
        client,
        f"<b>🚨 ECONOMY SECURITY ALERT</b>\n\n"
        f"<blockquote>"
        f"<b>Event:</b> UNAUTHORIZED_ECONOMY_CHANGE\n"
        f"<b>User ID:</b> <code>{user_id}</code>\n"
        f"<b>Case:</b> <code>{case_id}</code>\n"
        f"<b>Action:</b> ACCOUNT QUARANTINED + ECONOMY RESET\n"
        f"<b>Recovery:</b> {format_money(recovery_balance)}\n"
        f"<b>Dump:</b> <code>{dump_id}</code>\n"
        f"</blockquote>",
    )

    await _log_security_event("ECONOMY_EXPLOIT_DETECTED", user_id, user_id, case_id=case_id, dump_id=dump_id)
    return case_id, dump_id


async def global_ban_check(user_id: int) -> tuple[bool, str | None]:
    """Check if user is globally banned. Returns (is_banned, ban_reason)."""
    if await _is_owner(user_id):
        return False, None
    ban = await sec_db.get_global_ban(user_id)
    if ban:
        return True, ban.get("reason", "Global ban")
    return False, None


async def quarantine_check(user_id: int) -> bool:
    """Check if user is quarantined."""
    if await _is_owner(user_id):
        return False
    user = await users_db.get_user(user_id)
    if user:
        return user.get("is_frozen", False)
    return False