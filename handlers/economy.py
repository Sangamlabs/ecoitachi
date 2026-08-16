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
from utils.validators import parse_amount_or_error, resolve_target

logger = logging.getLogger(__name__)


def register(app: Client) -> None:
    @app.on_message(filters.command("profile") & filters.private)
    @safe_handler
    async def cmd_profile(client: Client, message: Message):
        await ensure_user(client, message)
        user = await users_db.get_user(message.from_user.id)
        await reply_html(client, message, msgs.profile(user))

    @app.on_message(filters.command("bal") & filters.private)
    @safe_handler
    async def cmd_bal(client: Client, message: Message):
        await ensure_user(client, message)
        target_id = message.from_user.id
        if len(message.command) > 1:
            text = message.command[1]
            if text.startswith("@"):
                target = await users_db.get_user_by_username(text[1:])
                if target is None:
                    await reply_html(client, message, msgs.error("User not found."))
                    return
                target_id = target["user_id"]
        elif message.reply_to_message and message.reply_to_message.from_user:
            target_id = message.reply_to_message.from_user.id

        target_doc = await users_db.get_or_create_user(target_id)
        await reply_html(client, message, msgs.balance(target_doc, target_doc))

    @app.on_message(filters.command("pay") & filters.private)
    @safe_handler
    async def cmd_pay(client: Client, message: Message):
        await ensure_user(client, message)
        args = message.command[1:]
        target_id, username, _ = resolve_target(message, args[0] if args else None)

        if target_id is None and username is None and message.reply_to_message:
            target = message.reply_to_message.from_user
            target_id, username = target.id, target.username

        if target_id is None:
            await reply_html(
                client, message,
                msgs.error("Usage: <code>/pay @user amount</code> or reply to a user with <code>/pay amount</code>."),
            )
            return
        if target_id == -1 and username:
            doc = await users_db.get_user_by_username(username)
            if doc is None:
                await reply_html(client, message, msgs.error("User not found. They must start the bot."))
                return
            target_id = doc["user_id"]

        amount_raw = args[1] if len(args) > 1 else (args[0] if args else None)
        if amount_raw is None and message.reply_to_message:
            amount_raw = args[0] if args else None
        amount, err = parse_amount_or_error(amount_raw or "")
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

    @app.on_message(filters.command("leader") & filters.private)
    @safe_handler
    async def cmd_leader(client: Client, message: Message):
        await ensure_user(client, message)
        top = await leaderboard_service.top_net_worth(10)
        entries = [
            (u["user_id"], leaderboard_service.name_of(u), await leaderboard_service.net_worth(u))
            for u in top
        ]
        await reply_html(client, message, msgs.leaderboard(entries))
