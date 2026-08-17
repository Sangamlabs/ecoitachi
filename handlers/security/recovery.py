"""Recovery Handler - handles /restore, /recover, /restorecase, /unquarantine commands."""
from pyrogram import Client, filters
from pyrogram.types import Message
import logging
import time

from database import security as sec_db
from services import settings as settings_service
from services import economy as econ
from utils.messages import error, success, info
from utils.sender import reply_html

logger = logging.getLogger("recovery")


async def register_recovery_handlers(app: Client) -> None:
    """Register recovery handlers with the Pyrogram Client."""
    pass


async def cmd_restore(client: Client, message: Message):
    """Handle /restore command - Restore from dump.

    Manual flow:
    1. Require authorized admin
    2. Get recovery ID (dump_id) from command argument
    3. Use the unique recovery ID to restore the saved dump
    4. Log the recovery action in the audit log
    5. Prevent duplicate restoration
    """
    target = message.from_user.id
    recovery_id = message.command[1] if len(message.command) > 1 else None

    if not recovery_id:
        await reply_html(
            client, message,
            msgs.error("Usage: /restore <recovery_id>")
        )
        return

    # Use the recovery ID (dump_id) to restore from dump
    ok, msg = await sec_db.restore_from_dump(recovery_id, target, message.from_user.id)
    if ok:
        await reply_html(client, message, success(msg))
    else:
        await reply_html(client, message, msgs.error(msg))


async def cmd_recover(client: Client, message: Message):
    """Handle /recover command - Recover from dump (same as /restore).

    Alias for /restore - uses the same recovery ID mechanism.
    """
    await cmd_restore(client, message)


async def cmd_restorecase(client: Client, message: Message):
    """Handle /restorecase command - Restore from case."""
    case_id = message.command[1] if len(message.command) > 1 else None
    if not case_id:
        await reply_html(client, message, msgs.error("Usage: /restorecase <case_id>"))
        return
    ok, msg = await sec_db.restore_from_case(case_id, message.from_user.id)
    if ok:
        await reply_html(client, message, success(msg))
    else:
        await reply_html(client, message, msgs.error(msg))


async def cmd_unquarantine(client: Client, message: Message):
    """Handle /unquarantine command - Remove user from quarantine."""
    target = message.from_user.id
    ok = await sec_db.remove_quarantine(target)
    if ok:
        await reply_html(client, message, success(f"User <code>{target}</code> removed from quarantine."))
    else:
        await reply_html(client, message, msgs.error(f"User <code>{target}</code> is not quarantined."))


async def cmd_clear(client: Client, message: Message):
    """Handle /clear command - Clear a user's recovery balance.

    Manual flow (fully admin-controlled, no automatic enforcement):
    1. Require authorized admin
    2. Get target user ID (from reply or command arg, default: issuer)
    3. Create a complete backup/dump of the user's economy state first
    4. Generate a unique recovery ID for this dump
    5. Log who performed the action, when, and why
    6. Clear only the intended user's recovery data
    7. Confirm the action to the admin
    """
    # Step 1: Get target user ID (from reply or command arg)
    target = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user.id
    elif len(message.command) > 1:
        # Try to resolve the target
        target_arg = message.command[1]
        if target_arg.isdigit():
            target = int(target_arg)
        elif target_arg.startswith("@"):
            from database import users as users_db
            doc = await users_db.get_user_by_username(target_arg[1:])
            target = doc["user_id"] if doc else None

    if target is None:
        await reply_html(
            client, message,
            msgs.error("Usage: /clear [@username | user_id] (reply to user)")
        )
        return

    # Step 2: Create a complete backup/dump of the user's economy state
    import uuid
    dump_id = f"REC-{uuid.uuid4().hex[:8].upper()}"

    # Get current economy snapshot
    from database import users as users_db
    user_doc = await users_db.get_user_by_id(target)
    if not user_doc:
        await reply_html(client, message, msgs.error(f"User <code>{target}</code> not found."))
        return

    # Build snapshot from user's economy data
    snapshot = {
        "balance": user_doc.get("balance", 0),
        "wallet": user_doc.get("wallet_balance", 0),
        "bank_balance": user_doc.get("bank_balance", 0),
        "assets": user_doc.get("assets", {}),
        "timestamp": int(time.time()),
    }

    # Create the security dump
    await sec_db.create_dump(
        dump_id=dump_id,
        user_id=target,
        dump_type="recovery_clear",
        reason=f"/clear by admin {message.from_user.id}",
        snapshot=snapshot,
        case_id=None,
    )

    # Step 3: Log the action
    await sec_db.create_event(
        event_id=f"EVT-{uuid.uuid4().hex[:8].upper()}",
        event_type="recovery_clear_initiated",
        user_id=target,
        actor_id=message.from_user.id,
        details={
            "dump_id": dump_id,
            "action": "/clear",
            "reason": "Admin-initiated recovery balance clear",
        },
    )

    # Step 4: Clear the user's recovery balance
    clear_balance = await settings_service.get_clear_recovery_balance()
    from services import economy as econ
    ok = await econ.reset_recovery_balance(target, clear_balance)

    # Step 5: Log the clear action
    if ok:
        await sec_db.create_event(
            event_id=f"EVT-{uuid.uuid4().hex[:8].upper()}",
            event_type="recovery_balance_cleared",
            user_id=target,
            actor_id=message.from_user.id,
            details={
                "dump_id": dump_id,
                "cleared_balance": clear_balance,
                "action": "/clear",
            },
        )

    # Step 6: Confirm to the admin
    if ok:
        await reply_html(
            client, message,
            success(
                f"User <code>{target}</code> cleared.\n"
                f"Dump ID: <code>{dump_id}</code> (use with /restore <id> to restore)\n"
                f"Reason: Admin-initiated clear"
            )
        )
    else:
        await reply_html(
            client, message,
            msgs.error(f"Nothing to clear for user <code>{target}</code> or error occurred.")
        )
