"""Recovery Handler - handles /restore, /recover, /restorecase, /unquarantine commands."""
from pyrogram import Client, filters
from pyrogram.types import Message

from database import security as sec_db
from services import settings as settings_service
from services import economy as econ
from utils.messages import error, success, info
from utils.sender import reply_html

async def register_recovery_handlers(app: Client) -> None:
    """Register recovery handlers with the Pyrogram Client."""
    pass

async def cmd_restore(client: Client, message: Message):
    """Handle /restore command - Restore from dump."""
    # Owner-only manual clear of recovery balance
    target = message.from_user.id
    ok, msg = await manual_clear(target)
    if ok:
        await reply_html(client, message, success(msg))
    else:
        await reply_html(client, message, msgs.error(msg))

async def cmd_recover(client: Client, message: Message):
    """Handle /recover command - Recover from dump (same as /restore)."""
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
