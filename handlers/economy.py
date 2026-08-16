"""Economy handlers: /profile, /bal, /pay, /leader."""

from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.types import Message

from database import users as users_db
from handlers.common import ensure_user, safe_handler
from services import economy, leaderboard as leaderboard_service, transaction as tx_service
from utils import messages as msgs
from utils.sender import reply_html
from utils.validators import parse_amount_or_error, parse_target_arg, target_from_message

logger = logging.getLogger(__name__)

# Commands work in DM, groups and supergroups (never channels).  The chat gate
# in utils.chat enforces per-chat feature toggles centrally.
NOT_CHANNEL = ~filters.channel & ~filters.bot


def register(app: Client) -> None:
    @app.on_message(filters.command("profile") & NOT_CHANNEL)
    @safe_handler(feature="economy")
    async def cmd_profile(client: Client, message: Message):
        await ensure_user(client, message)
        user = await users_db.get_user(message.from_user.id)
        await reply_html(client, message, msgs.profile(user))

    @app.on_message(filters.command("bal") & NOT_CHANNEL)
    @safe_handler(feature="economy")
    async def cmd_bal(client: Client, message: Message):
        await ensure_user(client, message)
        target_id = target_from_message(message)
        args = message.command[1:]
        if target_id is None and args:
            parsed = parse_target_arg(args[0])
            if parsed is not None:
                pid, username = parsed
                if pid == -1:
                    doc = await users_db.get_user_by_username(username)
                    if doc is None:
                        await reply_html(client, message, msgs.error("User not found."))
                        return
                    target_id = doc["user_id"]
                else:
                    target_id = pid
        if target_id is None:
            target_id = message.from_user.id

        target_doc = await users_db.get_or_create_user(target_id)
        await reply_html(client, message, msgs.balance(target_doc, target_doc))

    @app.on_message(filters.command("pay") & NOT_CHANNEL)
    @safe_handler(feature="economy")
    async def cmd_pay(client: Client, message: Message):
        await ensure_user(client, message)
        args = message.command[1:]

        # Target priority: reply user id > explicit numeric id > username lookup.
        target_id = target_from_message(message)
        amount_idx = 0
        if target_id is None and args:
            parsed = parse_target_arg(args[0])
            if parsed is not None:
                pid, username = parsed
                amount_idx = 1
                if pid == -1:
                    doc = await users_db.get_user_by_username(username)
                    if doc is None:
                        await reply_html(client, message, msgs.error("User not found. They must start the bot."))
                        return
                    target_id = doc["user_id"]
                else:
                    target_id = pid

        if target_id is None:
            await reply_html(
                client, message,
                msgs.error(
                    "Usage: <code>/pay @user amount</code>, <code>/pay 123456789 amount</code>, "
                    "or reply to a user with <code>/pay amount</code>."
                ),
            )
            return

        amount_raw = args[amount_idx] if len(args) > amount_idx else None
        if amount_raw is None:
            await reply_html(
                client, message,
                msgs.error(
                    "Usage: <code>/pay @user amount</code>, <code>/pay 123456789 amount</code>, "
                    "or reply to a user with <code>/pay amount</code>."
                ),
            )
            return
        amount, err = parse_amount_or_error(amount_raw)
        if err:
            await reply_html(client, message, msgs.error(err))
            return

        receiver_doc = await users_db.get_or_create_user(target_id)
        result = await economy.transfer(message.from_user.id, target_id, amount)
        tx_id = await tx_service.record(
            user_id=message.from_user.id,
            ttype=tx_service.PAY,
            amount=amount,
            balance_before=result["sender_wallet"] + amount,
            balance_after=result["sender_wallet"],
            metadata={"receiver": target_id},
        )
        sender_doc = await users_db.get_user(message.from_user.id)
        await reply_html(client, message, msgs.payment(sender_doc, receiver_doc, amount, tx_id))
        try:
            from utils.sender import send_html

            await send_html(client, target_id, msgs.payment_received(sender_doc, amount))
        except Exception:
            logger.warning("could not deliver payment notice to %s", target_id)

    @app.on_message(filters.command("leader") & NOT_CHANNEL)
    @safe_handler(feature="leaderboard")
    async def cmd_leader(client: Client, message: Message):
        await ensure_user(client, message)
        top = await leaderboard_service.top_net_worth(10)
        entries = [
            (u["user_id"], leaderboard_service.name_of(u), await leaderboard_service.net_worth(u))
            for u in top
        ]
        await reply_html(client, message, msgs.leaderboard(entries))
