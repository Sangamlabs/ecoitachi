"""Dumps Handler - handles /dumpinfo and /dumps commands.
from pyrogram import Client

Business logic delegated to SecurityDumpService.
"""

from pyrogram import filters
from pyrogram.types import Message

from database import security as sec_db
from services.security import create_security_dump, list_security_dumps, restore_from_dump
from utils.messages import error, success, info


async def cmd_dumpinfo(client: Message, dump_id: str = None):
    """Handle /dumpinfo command - Show dump details.
    
    Usage: /dumpinfo DUMP-ID  (owner + sudo only)
    Without argument: shows all dumps summary.
    """
    # Check permissions - owner + sudo
    if not is_sudo(message.from_user.id) and not is_owner(message.from_user.id):
        await reply_html(client, message, msgs.error("Owner + Sudo only."))
        return
    
    # Get dumps list
    dumps = await list_security_dumps()
    
    if not dumps:
        await reply_html(client, message, info("No security dumps found."))
        return
    
    # If dump_id provided, show specific dump
    if dump_id:
        dump = await sec_db.get_dump(dump_id)
        if not dump:
            await reply_html(client, message, msgs.error(f"Dump <code>{dump_id}</code> not found."))
            return
        
        lines = [
            f"<b>Dump ID:</b> <code>{dump['dump_id']}</code>",
            f"<b>User ID:</b> <code>{dump['user_id']}</code>",
            f"<b>Dump type:</b> {dump.get('dump_type', '?')}",
            f"<b>Reason:</b> {dump.get('reason', '?')}",
            f"<b>Status:</b> {dump.get('status', '?')}",
            f"<b>Created at:</b> <t>{dump.get('created_at')}:R>",
            f"<b>Case ID:</b> {dump.get('case_id', '?')}",
        ]
        if dump.get('used_by'):
            lines.append(f"<b>Used by:</b> <code>{dump['used_by']}</code>")
        if dump.get('used_at'):
            lines.append(f"<b>Used at:</b> <t>{dump['used_at']}:R>")
        
        await reply_html(client, message, info("\n".join(lines)))
        return
    
    # Show summary of all dumps
    lines = [f"• <b>{d['dump_id']}</b>: {d.get('user_count', '?')} users, {d.get('dump_type', '?')}, {d.get('status', '?')}, created <t>{d.get('created_at')}:R>" for d in dumps[:10]]
    await reply_html(client, message, info("\n".join(lines)))


async def cmd_dumps(client: Message):
    """Handle /dumps command - List all dump IDs.
    
    Usage: /dumps  (owner + sudo only)
    Shows summary of all security dumps.
    """
    # Check permissions
    if not is_sudo(message.from_user.id) and not is_owner(message.from_user.id):
        await reply_html(client, message, msgs.error("Owner + Sudo only."))
        return
    
    dumps = await list_security_dumps()
    
    if not dumps:
        await reply_html(client, message, info("No security dumps found."))
        return
    
    lines = [f"<code>{d['dump_id']}</code>: {d.get('user_count', '?')} users, {d.get('dump_type', '?')}, status: {d.get('status', '?')}, created <t>{d.get('created_at')}:R>" for d in dumps]
    await reply_html(client, message, info("\n".join(lines)))
from utils.sender import reply_html
