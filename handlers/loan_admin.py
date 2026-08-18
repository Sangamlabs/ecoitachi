"""Loan admin controls — OWNER/SUDO only.

Commands: /setloan KEY VALUE, /loanstats, /loanuser USER.
"""

from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.types import Message

from database import loans as loans_db
from handlers.common import safe_handler
from services import identity as identity_service
from services import loan as loans_service
from services import settings as settings_service
from utils import messages as msgs
from utils.money import format_money
from utils.permissions import sudo_only
from utils.sender import reply_html

logger = logging.getLogger(__name__)

NOT_CHANNEL = ~filters.channel & ~filters.bot

# /setloan key -> settings field + validator
LOAN_FIELDS = {
    "max": ("max_principal", lambda v: v > 0),
    "interest": ("interest_rate", lambda v: 0 <= v <= 100),
    "mindays": ("min_duration_days", lambda v: v >= 1),
    "maxdays": ("max_duration_days", lambda v: v >= 1),
    "recovery": ("recovery_percent", lambda v: 0 <= v <= 100),
    "enabled": ("enabled", lambda v: v in (0, 1)),
}


def register(app: Client) -> None:
    @app.on_message(filters.command("setloan") & NOT_CHANNEL)
    @sudo_only
    @safe_handler(feature="admin")
    async def cmd_setloan(client: Client, message: Message):
        """/setloan KEY VALUE — configure loan rules (OWNER/SUDO)."""
        args = message.command[1:]
        if len(args) != 2:
            await reply_html(
                client, message,
                msgs.error(
                    "Usage: <code>/setloan key value</code>\n"
                    "Keys: max, interest, mindays, maxdays, recovery, enabled"
                ),
            )
            return
        key = args[0].lower()
        if key not in LOAN_FIELDS:
            await reply_html(
                client, message,
                msgs.error(f"Invalid key. Valid keys: {', '.join(sorted(LOAN_FIELDS))}"),
            )
            return
        field, validate = LOAN_FIELDS[key]
        try:
            value = int(args[1]) if key != "interest" else float(args[1])
        except ValueError:
            await reply_html(client, message, msgs.error("Value must be a number."))
            return
        if not validate(value):
            await reply_html(client, message, msgs.error("Value is out of range for this setting."))
            return

        current = await loans_service.get_loan_config()
        current[field] = value
        await settings_service.update_loan_config(**current)
        await reply_html(
            client, message,
            msgs.success(f"Loan setting <b>{key}</b> set to <b>{args[1]}</b>."),
        )

    @app.on_message(filters.command("loanstats") & NOT_CHANNEL)
    @sudo_only
    @safe_handler(feature="admin")
    async def cmd_loanstats(client: Client, message: Message):
        """/loanstats — overall loan statistics (OWNER/SUDO)."""
        stats = await loans_db.get_loan_stats()
        await reply_html(
            client, message,
            msgs.success(
                "📊 <b>LOAN STATISTICS</b>\n"
                "<blockquote>"
                f"<b>Active loans:</b> {stats['active_loans']}\n"
                f"<b>Overdue loans:</b> {stats['overdue_loans']}\n"
                f"<b>Paid loans:</b> {stats['paid_loans']}\n"
                f"<b>Total principal issued:</b> {format_money(stats['total_principal_issued'])}\n"
                f"<b>Total outstanding:</b> {format_money(stats['total_outstanding'])}\n"
                f"<b>Total interest accrued:</b> {format_money(stats['total_interest_accrued'])}\n"
                f"<b>Total repaid:</b> {format_money(stats['total_repaid'])}\n"
                f"<b>Unique borrowers:</b> {stats['unique_borrowers']}"
                "</blockquote>"
            ),
        )

    @app.on_message(filters.command("loanuser") & NOT_CHANNEL)
    @sudo_only
    @safe_handler(feature="admin")
    async def cmd_loanuser(client: Client, message: Message):
        """/loanuser USER — show a user's loan status (OWNER/SUDO)."""
        args = message.command[1:]
        doc = await identity_service.resolve_user(
            client, message, args[0] if args else None, create=False
        )
        if doc is None:
            await reply_html(
                client, message,
                msgs.error("User not found. Provide a Telegram ID, @username, UNOITACHI UID, or reply to the user."),
            )
            return
        target_id = doc["user_id"]
        loan = await loans_db.get_active_loan(target_id)
        if loan is None:
            await reply_html(
                client, message,
                msgs.info(f"User <code>{target_id}</code> has no active loan."),
            )
            return
        due_at = loan.get("due_at")
        await reply_html(
            client, message,
            msgs.success(
                f"🏦 <b>USER LOAN STATUS</b>\n"
                f"<code>{target_id}</code>\n"
                "<blockquote>"
                f"<b>Loan ID:</b> <code>{loan['loan_id']}</code>\n"
                f"<b>Principal:</b> {format_money(loan.get('principal', 0))}\n"
                f"<b>Outstanding:</b> {format_money(loan.get('outstanding_principal', 0))}\n"
                f"<b>Overdue interest:</b> {loan.get('interest_rate', 0)}% per day\n"
                f"<b>Due date:</b> {due_at.strftime('%Y-%m-%d') if due_at else '—'}\n"
                f"<b>Status:</b> {loan.get('status', '?')}"
                "</blockquote>"
            ),
        )