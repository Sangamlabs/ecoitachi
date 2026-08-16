"""Recovery service: dump management, /clear, /restore, /recover, /restorecase."""

from __future__ import annotations

import logging
import secrets
import time
import hashlib
from typing import Any

from database import security as sec_db, users as users_db
from database.mongo import mongo
from services import economy, settings as settings_service, transaction as tx_service
from services.economy import ensure_active
from utils.money import format_money

logger = logging.getLogger(__name__)


async def _is_owner(user_id: int) -> bool:
    from config import config
    return user_id == config.OWNER_ID


async def _notify_owner(client, text: str) -> None:
    from config import config
    from utils.sender import send_html
    try:
        await send_html(client, config.OWNER_ID, text)
    except Exception:
        logger.exception("Failed to notify owner")


async def _log_recovery_event(
    event_type: str,
    user_id: int,
    actor_id: int,
    case_id: str | None = None,
    dump_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    from database import security as sec_db
    event_id = f"EVT-{secrets.token_hex(4).upper()}"
    await sec_db.create_security_event(
        event_id=event_id,
        event_type=event_type,
        user_id=user_id,
        actor_id=actor_id,
        case_id=case_id,
        dump_id=dump_id,
        metadata=metadata,
    )
    return event_id


async def create_manual_dump(
    target_user_id: int,
    owner_id: int,
    reason: str = "manual_clear",
) -> tuple[str, str]:
    """Create a manual security dump and case for /clear. Returns (case_id, dump_id)."""
    case_id = f"CASE-{secrets.token_hex(4).upper()}"
    dump_id = await create_security_dump(target_user_id, case_id, "MANUAL", reason, owner_id)

    # Create case with MANUAL_CLEAR type
    await sec_db.create_case(
        case_id=case_id,
        user_id=target_user_id,
        case_type="MANUAL_CLEAR",
        reason=reason,
        created_by=owner_id,
        dump_id=dump_id,
    )

    await _log_recovery_event("MANUAL_CLEAR", target_user_id, owner_id, case_id=case_id, dump_id=dump_id)
    return case_id, dump_id


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
    await _log_recovery_event("DUMP_CREATED", user_id, created_by, case_id=case_id, dump_id=dump_id)
    return dump_id


async def validate_dump_integrity(dump: dict[str, Any]) -> bool:
    """Validate dump integrity by recomputing security hash."""
    user_id = dump.get("original_user_id")
    wallet = dump.get("wallet", 0)
    bank = dump.get("bank", 0)
    stock_value = dump.get("stock_value", 0)
    asset_value = dump.get("asset_value", 0)
    case_id = dump.get("case_id")
    created_at = dump.get("created_at")

    snapshot_data = f"{user_id}{wallet}{bank}{stock_value}{asset_value}{case_id}{created_at}"
    computed_hash = hashlib.sha256(snapshot_data.encode()).hexdigest()[:16]
    return computed_hash == dump.get("security_hash")


async def clear_live_account(
    target_user_id: int,
    recovery_balance: int = 20000,
    actor_id: int | None = None,
) -> dict[str, Any]:
    """Clear live account and set recovery balance. Returns previous state."""
    user = await users_db.get_user(target_user_id)
    if not user:
        raise ValueError(f"User {target_user_id} not found")

    prev_wallet = user.get("wallet", 0)
    prev_bank = user.get("bank", 0)

    # Clear economy state
    await mongo.db[users_db.COLLECTION].update_one(
        {"user_id": target_user_id},
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
    await mongo.db["stock_holdings"].delete_many({"user_id": target_user_id})
    # Clear asset holdings
    await mongo.db["asset_holdings"].delete_many({"user_id": target_user_id})
    # Clear inventory
    await mongo.db["inventory"].delete_many({"user_id": target_user_id})

    # Create recovery transaction
    if actor_id:
        await tx_service.record(
            user_id=target_user_id,
            ttype="SECURITY_RECOVERY",
            amount=recovery_balance,
            balance_before=prev_wallet,
            balance_after=recovery_balance,
            metadata={
                "actor": actor_id,
                "reason": "manual_clear_recovery",
                "prev_wallet": prev_wallet,
                "prev_bank": prev_bank,
            },
        )

    return {
        "prev_wallet": prev_wallet,
        "prev_bank": prev_bank,
        "recovery_balance": recovery_balance,
    }


async def restore_from_dump(
    dump: dict[str, Any],
    actor_id: int,
    unban: bool = False,
    unquarantine: bool = False,
) -> dict[str, Any]:
    """Restore user account from dump. Must be atomic."""
    user_id = dump.get("original_user_id")
    if not user_id:
        raise ValueError("Invalid dump: missing original_user_id")

    # Verify dump integrity
    if not await validate_dump_integrity(dump):
        dump_id = dump.get("dump_id")
        await sec_db.update_dump(dump_id, status="LOCKED")
        await _log_recovery_event("DUMP_INTEGRITY_FAILURE", user_id, actor_id, dump_id=dump_id)
        raise ValueError("Dump integrity check failed")

    # Check if already restored
    if dump.get("status") == "RESTORED":
        raise ValueError("Dump already restored")

    # Acquire lock - atomic status transition
    updated = await sec_db.update_dump(
        dump.get("dump_id"),
        status="RESTORING",
    )
    if not updated:
        raise ValueError("Dump not found or already being restored")

    try:
        restoration_id = f"RESTORE-{secrets.token_hex(4).upper()}"

        # Atomic restore using MongoDB session if available
        # For now, do sequential but with error handling
        restored_wallet = dump.get("wallet", 0)
        restored_bank = dump.get("bank", 0)

        # Restore wallet and bank
        await mongo.db[users_db.COLLECTION].update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "wallet": restored_wallet,
                    "bank": restored_bank,
                    "updated_at": int(time.time()),
                }
            },
            upsert=True,
        )

        # Restore stock holdings
        await mongo.db["stock_holdings"].delete_many({"user_id": user_id})
        if dump.get("stock_holdings"):
            for h in dump["stock_holdings"]:
                h["user_id"] = user_id
                await mongo.db["stock_holdings"].insert_one(h)

        # Restore asset holdings
        await mongo.db["asset_holdings"].delete_many({"user_id": user_id})
        if dump.get("asset_holdings"):
            for h in dump["asset_holdings"]:
                h["user_id"] = user_id
                await mongo.db["asset_holdings"].insert_one(h)

        # Restore inventory
        await mongo.db["inventory"].delete_many({"user_id": user_id})
        if dump.get("inventory"):
            for h in dump["inventory"]:
                h["user_id"] = user_id
                await mongo.db["inventory"].insert_one(h)

        # Handle ban/quarantine state
        if unban:
            await users_db.set_user_flags(user_id, is_banned=False, is_frozen=False)
            await sec_db.remove_global_ban(user_id, actor_id)
        elif unquarantine:
            await users_db.set_user_flags(user_id, is_frozen=False)

        # Update dump status
        await sec_db.update_dump(
            dump.get("dump_id"),
            status="RESTORED",
            recovery_status="RESTORED",
            restored_at=int(time.time()),
            restored_by=actor_id,
            restoration_id=restoration_id,
        )

        # Log audit
        await _log_recovery_event(
            "RECOVERY_COMPLETED",
            user_id,
            actor_id,
            case_id=dump.get("case_id"),
            dump_id=dump.get("dump_id"),
            metadata={
                "restoration_id": restoration_id,
                "unban": unban,
                "unquarantine": unquarantine,
            },
        )

        return {
            "restoration_id": restoration_id,
            "wallet": restored_wallet,
            "bank": restored_bank,
            "unbanned": unban,
            "unquarantined": unquarantine,
        }

    except Exception as e:
        # On failure, reset dump status to AVAILABLE
        await sec_db.update_dump(dump.get("dump_id"), status="AVAILABLE")
        await _log_recovery_event("RECOVERY_FAILED", user_id, actor_id, dump_id=dump.get("dump_id"))
        raise


async def restore_by_case(
    case_id: str,
    actor_id: int,
    unban: bool = False,
    unquarantine: bool = False,
) -> dict[str, Any]:
    """Restore using case ID - finds associated dump."""
    case = await sec_db.get_case(case_id)
    if not case:
        raise ValueError(f"Case {case_id} not found")

    dump_id = case.get("dump_id")
    if not dump_id:
        raise ValueError(f"Case {case_id} has no associated dump")

    dump = await sec_db.get_dump(dump_id)
    if not dump:
        raise ValueError(f"Dump {dump_id} not found")

    return await restore_from_dump(dump, actor_id, unban, unquarantine)


async def list_dumps(
    dump_type: str | None = None,
    status: str | None = None,
    user_id: int | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    return await sec_db.list_dumps(dump_type, status, user_id, limit)


async def get_dump_info(dump_id: str) -> dict[str, Any] | None:
    dump = await sec_db.get_dump(dump_id)
    if not dump:
        return None

    return {
        "dump_id": dump.get("dump_id"),
        "case_id": dump.get("case_id"),
        "user_id": dump.get("original_user_id"),
        "username": dump.get("original_username"),
        "name": dump.get("original_name"),
        "dump_type": dump.get("dump_type"),
        "reason": dump.get("reason"),
        "status": dump.get("status"),
        "created_at": dump.get("created_at"),
        "created_by": dump.get("created_by"),
        "wallet": dump.get("wallet"),
        "bank": dump.get("bank"),
        "stock_value": dump.get("stock_value"),
        "asset_value": dump.get("asset_value"),
        "stock_count": len(dump.get("stock_holdings", [])),
        "asset_count": len(dump.get("asset_holdings", [])),
        "recovery_status": dump.get("recovery_status"),
        "restored_at": dump.get("restored_at"),
        "restored_by": dump.get("restored_by"),
        "restoration_id": dump.get("restoration_id"),
    }


async def add_recovery_settings() -> None:
    """Add default security settings if not present."""
    config = await settings_service.get_settings()
    defaults = {
        "clear_recovery_balance": 20000,
        "global_ban_on_exploit": True,
        "secret_detection_enabled": True,
    }
    for key, value in defaults.items():
        if key not in config:
            await settings_service.update_settings(**{key: value})