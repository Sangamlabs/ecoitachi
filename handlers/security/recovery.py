"""Recovery Handler - handles /restore, /recover, /restorecase, /unquarantine commands.
from pyrogram import Client

Business logic delegated to RecoveryService.
Ensures /restore and /recover use the SAME RecoveryService method.
"""

from pyrogram import filters
from pyrogram.types import Message

from services.security import manual_restore, manual_restorecase, restore_from_dump
from services.economy import reset_recovery_balance
from utils.messages import error, success, info


async def cmd_restore(client: Message, dump_id: str = None):
    """Handle /restore command - Restore from dump.
    
    Usage: /restore DUMP-ID  (owner only)
    """
    # Check owner only
    if not is_owner(message.from_user.id):
        await reply_html(client, message, msgs.error("Only the bot owner can restore from dumps."))
        return
    
    if not dump_id:
        args = message.command[1:]
        if not args:
            await reply_html(client, message, msgs.error("Usage: /restore DUMP-ID"))
            return
        dump_id = args[0]
    
    # Execute restore
    ok, msg = await manual_restore(dump_id, message.from_user.id)
    await reply_html(client, message, success(msg) if ok else msgs.error(msg))


async def cmd_recover(client: Message, dump_id: str = None):
    """Handle /recover command - Recover from dump.
    
    Usage: /recover DUMP-ID  (owner only)
    Important: /restore and /recover MUST use the SAME RecoveryService method.
    """
    # Check owner only
    if not is_owner(message.from_user.id):
        await reply_html(client, message, msgs.error("Only the bot owner can recover from dumps."))
        return
    
    if not dump_id:
        args = message.command[1:]
        if not args:
            await reply_html(client, message, msgs.error("Usage: /recover DUMP-ID"))
            return
        dump_id = args[0]
    
    # Execute recover using THE SAME method as /restore
    # Both call manual_restore which calls restore_from_dump
    ok, msg = await manual_restore(dump_id, message.from_user.id)
    await reply_html(client, message, success(msg) if ok else msgs.error(msg))


async def cmd_restorecase(client: Message, case_id: str = None):
    """Handle /restorecase command - Restore from security case.
    
    Usage: /restorecase CASE-ID  (owner only)
    """
    # Check owner only
    if not is_owner(message.from_user.id):
        await reply_html(client, message, msgs.error("Only the bot owner can restore from cases."))
        return
    
    if not case_id:
        args = message.command[1:]
        if not args:
            await reply_html(client, message, msgs.error("Usage: /restorecase CASE-ID"))
            return
        case_id = args[0]
    
    # Execute restore from case
    ok, msg = await manual_restorecase(case_id, message.from_user.id)
    await reply_html(client, message, success(msg) if ok else msgs.error(msg))


async def cmd_unquarantine(client: Message, user_id_str: str = None):
    """Handle /unquarantine command - Remove quarantine from user.
    
    Usage: /unquarantine USER_ID  (owner only)
    """
    # Check owner only
    if not is_owner(message.from_user.id):
        await reply_html(client, message, msgs.error("Only the bot owner can unquarantine users."))
        return
    
    # Get target user ID
    target_id = None
    if user_id_str:
        target_id = int(user_id_str)
    elif message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
    elif len(message.command) > 1 and message.command[1].isdigit():
        target_id = int(message.command[1])
    
    if target_id is None:
        await reply_html(client, message, msgs.error("User not found."))
        return
    
    # Clear quarantine
    from services import economy as econ_service
    result = await econ_service.clear_quarantine(target_id)
    
    if result:
        await reply_html(client, message, info(f"User <code>{target_id}</code> unquarantined."))
    else:
        await reply_html(client, message, msgs.error(f"User <code>{target_id}</code> was not quarantined."))
from utils.sender import reply_html
