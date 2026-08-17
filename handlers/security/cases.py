"""Cases Handler - handles /caseinfo, /securitycases, /securityuser commands.
from pyrogram import Client

Business logic delegated to SecurityCaseService.
"""

from pyrogram import filters
from pyrogram.types import Message

from database import security as sec_db
from services.security import create_security_case, list_security_cases
from utils.messages import error, success, info


async def cmd_securitycases(client: Message):
    """Handle /securitycases command - List all security cases."""
    # Can be used by owner or sudo
    if not is_sudo(message.from_user.id) and not is_owner(message.from_user.id):
        await reply_html(client, message, msgs.error("Owner or Sudo only."))
        return
    
    # Get optional user filter
    args = message.command[1:]
    user_id = None
    if args and args[0].isdigit():
        user_id = int(args[0])
    
    cases = await list_security_cases(user_id=user_id)
    
    if not cases:
        await reply_html(client, message, info("No security cases found."))
        return
    
    lines = []
    for case in cases:
        severity_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(case.get("severity", "medium"), "⚪")
        lines.append(
            f"<b>Case {case['case_id']}</b>: {case['title']}\n"
            f"   User: <code>{case['user_id']}</code>\n"
            f"   Severity: {severity_emoji} {case['severity']}\n"
            f"   Status: {case['status']}\n"
            f"   Created: <t>{case['created_at']}:R>"
        )
    
    await reply_html(client, message, info("\n\n".join(lines)))


async def cmd_caseinfo(client: Message, case_id: str = None):
    """Handle /caseinfo command - Show details of a specific case."""
    # Get case ID from argument or command
    if not case_id:
        args = message.command[1:]
        if not args:
            await reply_html(client, message, msgs.error("Usage: /caseinfo CASE-ID"))
            return
        case_id = args[0]
    
    # Get case from database
    case = await sec_db.get_case(case_id)
    if not case:
        await reply_html(client, message, msgs.error(f"Case <code>{case_id}</code> not found."))
        return
    
    lines = [
        f"<b>Case ID:</b> <code>{case['case_id']}</code>",
        f"<b>User ID:</b> <code>{case['user_id']}</code>",
        f"<b>Title:</b> {case['title']}",
        f"<b>Severity:</b> {case['severity']}",
        f"<b>Status:</b> {case['status']}",
        f"<b>Created by:</b> <code>{case['created_by']}</code>",
        f"<b>Created at:</b> <t>{case['created_at']}:R>",
        f"<b>Detail:</b> {case['detail']}",
    ]
    
    if case.get('resolved_at'):
        lines.append(f"<b>Resolved at:</b> <t>{case['resolved_at']}:R>")
        lines.append(f"<b>Resolved by:</b> <code>{case['resolved_by']}</code>")
    
    await reply_html(client, message, info("\n".join(lines)))
from utils.sender import reply_html
