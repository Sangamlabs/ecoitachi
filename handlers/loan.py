"""Loan (debt) system handlers — /loan, /loaninfo, /loanpay.

User-facing commands only; admin controls live in ``handlers/loan_admin.py``.
"""

from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.types import Message

from handlers.common import safe_handler
from services import loan as loans_service
from utils import messages as msgs
from utils.money import format_money
from utils.sender import reply_html
from utils.validators import parse_amount_or_error

logger = logging.getLogger(__name__)

NOT_CHANNEL = ~filters.channel & ~filters.bot


def register(app: Client) -> None:
    @app.on_message(filters.command("loan") & NOT_CHANNEL)
    @safe_handler(feature="economy")
    async def cmd_loan(client: Client, message: Message):
        """/loan AMOUNT DAYS — borrow money (example: /loan 50000 5)."""
        args = message.command[1:]
        if len(args) != 2:
            await reply_html(
                client, message,
                msgs.error("Usage: <code>/loan amount days</code>\nExample: <code>/loan 50000 5</code>"),
            )
            return
        amount, err = parse_amount_or_error(args[0])
        if err:
            await reply_html(client, message, msgs.error(f"Usage: <code>/loan amount days</code>. {err}"))
            return
        try:
            duration_days = int(args[1])
        except ValueError:
            await reply_html(client, message, msgs.error("Days must be an integer."))
            return
        ok, msg = await loans_service.issue_loan(
            user_id=message.from_user.id,
            amount=amount,
            duration_days=duration_days,
            actor_id=message.from_user.id,
        )
        await reply_html(client, message, msgs.success(msg) if ok else msgs.error(msg))

    @app.on_message(filters.command("loaninfo") & NOT_CHANNEL)
    @safe_handler(feature="economy")
    async def cmd_loaninfo(client: Client, message: Message):
        """/loaninfo — show the user's active loan status."""
        loan = await loans_service.get_loan_info(message.from_user.id)
        if loan is None:
            await reply_html(client, message, msgs.info("You have no active loan."))
            return

        status = loan.get("status", "?")
        due_at = loan.get("due_at")
        overdue_days = 0
        if status == "OVERDUE" and due_at is not None:
            from datetime import datetime, timezone

            overdue_days = max(0, (datetime.now(timezone.utc) - due_at).days)

        lines = [
            f"<b>🏦 LOAN ACCOUNT</b>",
            f"<blockquote>",
            f"<b>Loan ID:</b> <code>{loan['loan_id']}</code>",
            f"<b>Principal:</b> {format_money(loan.get('principal', 0))}",
            f"<b>Outstanding:</b> {format_money(loan.get('outstanding_principal', 0))}",
            f"<b>Overdue interest:</b> {loan.get('interest_rate', 0)}% per day",
            f"<b>Total repaid:</b> {format_money(loan.get('total_repaid', 0))}",
            f"<b>Issued:</b> {loan['issued_at'].strftime('%Y-%m-%d')}",
            f"<b>Due:</b> {due_at.strftime('%Y-%m-%d') if due_at else '—'}",
            f"<b>Status:</b> {status}",
        ]
        if overdue_days > 0:
            lines.append(f"<b>Days overdue:</b> {overdue_days}")
        lines.append("</blockquote>")
        await reply_html(client, message, "\n".join(lines))

    @app.on_message(filters.command("loanpay") & NOT_CHANNEL)
    @safe_handler(feature="economy")
    async def cmd_loanpay(client: Client, message: Message):
        """/loanpay AMOUNT | /loanpay all — pay toward the active loan."""
        args = message.command[1:]
        if not args:
            await reply_html(
                client, message,
                msgs.error("Usage: <code>/loanpay amount</code> or <code>/loanpay all</code>"),
            )
            return

        if args[0].lower() == "all":
            loan = await loans_service.get_loan_info(message.from_user.id)
            if loan is None:
                await reply_html(client, message, msgs.info("You have no active loan."))
                return
            amount = int(loan.get("outstanding_principal", 0))
        else:
            amount, err = parse_amount_or_error(args[0])
            if err:
                await reply_html(client, message, msgs.error(f"Usage: <code>/loanpay amount</code>. {err}"))
                return

        ok, msg = await loans_service.process_loan_payment(message.from_user.id, amount)
        await reply_html(client, message, msgs.success(msg) if ok else msgs.error(msg))
