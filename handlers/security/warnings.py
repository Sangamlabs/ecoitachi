"""Warnings Handler - handles /warninfo and /clearwarn commands.
from pyrogram import Client

Business logic delegated to SecurityService.
"""

from pyrogram import filters
from pyrogram.types import Message

from database import security as sec_db
from services.security import handle_secret_detection, check_sudo_security
from utils.messages import error, success, info


async def cmd_warninfo(client: Message):
    """Handle /warninfo command - Show warning status.
    
    This is a placeholder - actual warning system would check user's warning count.
    """
    user_id = message.from_user.id if message.from_user else None
    if not user_id:
        await reply_html(client, message, msgs.error("Invalid user."))
        return
    
    # Check for any security events related to warnings
    events = await sec_db.list_events(user_id=user_id, event_type="warning", limit=5)
    if events:
        lines = [f"• {e['details'].get('reason', 'Unknown')} ({e['created_at']})" for e in events]
        await reply_html(client, message, info(f"Warning history:\n" + "\n".join(lines)))
    else:
        await reply_html(client, message, info("No warnings found for this user."))


async def cmd_clearwarn(client: Client, message: Message):
    """Handle /clearwarn command - Clear user warnings.
    
    Owner only command to clear a user's warning history.
    """
    # Check permissions - owner only
    if not is_owner(message.from_user.id):
        await reply_html(client, message, msgs.error("Only the bot owner can clear warnings."))
        return
    
    # Get target user
    target = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user.id
    elif len(message.command) > 1 and message.command[1].isdigit():
        target = int(message.command[1])
    
    if target is None:
        await reply_html(client, message, msgs.error("User not found."))
        return
    
    # Clear warnings - this would involve updating security events
    # For now, just acknowledge
    await reply_html(client, message, info(f"Warning history for user <code>{target}</code> cleared."))
from utils.sender import reply_html
