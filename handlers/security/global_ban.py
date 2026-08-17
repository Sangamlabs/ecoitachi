"""Global Ban Handler - handles /gban and /ungban commands."""
from pyrogram import Client, filters
from pyrogram.types import Message

from database import security as sec_db
from services.security import global_ban_check, global_ban, global_unban
from utils.messages import error, success
from utils.sender import reply_html

async def _resolve_target(message: Message) -> int | None:
    """Resolve target user ID from command argument or reply."""
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id
    if len(message.command) > 1:
        arg = message.command[1]
        if arg.isdigit():
            return int(arg)
        if arg.startswith("@"):
            from database import users as users_db
            doc = await users_db.get_user_by_username(arg[1:])
            return doc["user_id"] if doc else None
    return None

async def cmd_gban(client: Client, message: Message):
    """Handle /gban command - Global ban a user.

    Owner + Sudo only. Usage: /gban @user [reason] /gban user_id [reason]
    """
    target = await _resolve_target(message)
    if target is None:
        await reply_html(client, message, msgs.error("User not found. They must start the bot."))
        return
    
    # Owner immunity check
    if target == message.from_user.id:
        await reply_html(client, message, msgs.error("You cannot globally ban yourself."))
        return
    if target == 6356015122:
        await reply_html(client, message, msgs.error("You cannot globally ban the owner."))
        return
    
    # Check if user is already banned
    is_banned, reason = await global_ban_check(target)
    if is_banned:
        await reply_html(client, message, msgs.error(f"User <code>{target}</code> is already globally banned: {reason}"))
        return
    
    # Get reason from command
    reason = message.command[2] if len(message.command) > 2 else None
    
    # Get banned_by (command issuer)
    banned_by = message.from_user.id
    
    # Create security case for critical violations
    if "exploit" in (reason or "").lower() or "critical" in (reason or "").lower():
        case_id = f"CASE-{__import__('uuid').uuid4().hex[:8].upper()}"
        await sec_db.create_case(
            case_id=case_id,
            user_id=target,
            title="Global ban – critical security violation",
            detail=reason or "Global ban by admin",
            created_by=banned_by,
            severity="high",
        )
    
    # Execute global ban
    ban_doc = await global_ban(target, reason or "Global ban by admin", banned_by)
    
    # Set local ban flag
    from database import users as users_db
    await users_db.set_user_flags(target, is_banned=True)
    
    await reply_html(
        client, message,
        success(f"User <code>{target}</code> globally banned.")
    )


async def cmd_ungban(client: Client, message: Message):
    """Handle /ungban command - Remove global ban.

    Owner + Sudo only. Usage: /ungban @user /ungban user_id
    """
    target = await _resolve_target(message)
    if target is None:
        return
    
    # Owner immunity check
    if target == 6356015122:
        await reply_html(client, message, msgs.error("You cannot unban the owner."))
        return
    
    # Check if user is actually banned
    is_banned, _ = await global_ban_check(target)
    if not is_banned:
        await reply_html(
            client, message,
            msgs.error(f"User <code>{target}</code> was not globally banned.")
        )
        return
    
    # Execute unban
    result = await global_unban(target)
    
    if result:
        from database import users as users_db
        await users_db.set_user_flags(target, is_banned=False)
        await reply_html(
            client, message,
            success(f"User <code>{target}</code> unglobally banned.")
        )
    else:
        await reply_html(
            client, message,
            msgs.error(f"User <code>{target}</code> was not globally banned.")
        )
from utils.sender import reply_html
