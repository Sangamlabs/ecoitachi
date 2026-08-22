"""Economy handlers: /profile, /bal, /pay, /leader."""

from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.types import Message

from database import users as users_db
from handlers.common import ensure_user, safe_handler
from services import economy, identity as identity_service, leaderboard as leaderboard_service, rob as rob_service
from services import tax as tax_service, transaction as tx_service
from services.global_battle import missions as missions_service
from utils import messages as msgs
from utils.sender import reply_html, schedule_delete
from utils.validators import parse_amount_or_error, parse_target_arg, target_from_message

logger = logging.getLogger(__name__)

# Commands work in DM, groups and supergroups (never channels).  The chat gate
# in utils.chat enforces per-chat feature toggles centrally.
NOT_CHANNEL = ~filters.channel & ~filters.bot

# Leaderboard results self-destruct after this many seconds.
LEADERBOARD_AUTO_DELETE_SECONDS = 180


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

        # Target priority: reply user id > explicit id / @username / UID.
        # A reply carries the real Telegram User object, so register the
        # receiver through it (persists the authoritative ``is_bot`` flag).
        reply_user = getattr(getattr(message, "reply_to_message", None), "from_user", None)
        target_id = None
        amount_idx = 0
        if reply_user is not None:
            reply_doc = await identity_service.ensure_user_from_telegram(reply_user)
            if reply_doc is not None:
                target_id = reply_doc["user_id"]
        if target_id is None and args:
            parsed = parse_target_arg(args[0])
            if parsed is not None:
                amount_idx = 1
                if parsed[0] == -1:
                    doc = await identity_service.resolve_user(
                        client, message, args[0], create=True
                    )
                    if doc is None:
                        await reply_html(client, message, msgs.error("User not found."))
                        return
                    target_id = doc["user_id"]
                else:
                    target_id = parsed[0]
            elif users_db.is_uid(args[0]):
                amount_idx = 1
                doc = await identity_service.resolve_user(
                    client, message, args[0], create=True
                )
                if doc is None:
                    await reply_html(client, message, msgs.error("User not found."))
                    return
                target_id = doc["user_id"]

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

        # Auto-register the receiver through the central identity layer.
        receiver_doc = await identity_service.ensure_user(target_id)
        payment_tax = await tax_service.system_tax_amount("payments", amount)
        result = await economy.transfer(
            message.from_user.id, target_id, amount, tax=payment_tax
        )
        if payment_tax > 0:
            await tx_service.record(
                user_id=message.from_user.id,
                ttype=tx_service.TAX,
                amount=payment_tax,
                balance_before=result["sender_wallet"] + amount + payment_tax,
                balance_after=result["sender_wallet"],
                metadata={"system": "payments", "gross": amount, "receiver": target_id},
            )
        tx_id = await tx_service.record(
            user_id=message.from_user.id,
            ttype=tx_service.PAY,
            amount=amount,
            balance_before=result["sender_wallet"] + amount + payment_tax,
            balance_after=result["sender_wallet"],
            metadata={"receiver": target_id, "direction": "out", "tax": payment_tax},
        )
        await tx_service.record(
            user_id=target_id,
            ttype=tx_service.PAY,
            amount=amount,
            balance_before=result["receiver_wallet"] - amount,
            balance_after=result["receiver_wallet"],
            metadata={"sender": message.from_user.id, "direction": "in"},
        )
        sender_doc = await users_db.get_user(message.from_user.id)
        await reply_html(client, message, msgs.payment(sender_doc, receiver_doc, amount, tx_id))
        try:
            from utils.sender import send_html

            await send_html(client, target_id, msgs.payment_received(sender_doc, amount))
        except Exception:
            logger.warning("could not deliver payment notice to %s", target_id)

        # Record mission completion
        await missions_service.record_command_completion(message.from_user.id, "pay")

    @app.on_message(filters.command("rob") & NOT_CHANNEL)
    @safe_handler(feature="economy")
    async def cmd_rob(client: Client, message: Message):
        await ensure_user(client, message)
        args = message.command[1:]

        # Target priority: reply user id > explicit id / @username / UID.
        # Reply carries the real Telegram User object (authoritative is_bot).
        reply_user = getattr(getattr(message, "reply_to_message", None), "from_user", None)
        target_id = None
        if reply_user is not None:
            reply_doc = await identity_service.ensure_user_from_telegram(reply_user)
            if reply_doc is not None:
                target_id = reply_doc["user_id"]
        if target_id is None and args:
            parsed = parse_target_arg(args[0])
            if parsed is not None:
                if parsed[0] == -1:
                    doc = await identity_service.resolve_user(
                        client, message, args[0], create=True
                    )
                    if doc is None:
                        await reply_html(client, message, msgs.error("User not found."))
                        return
                    target_id = doc["user_id"]
                else:
                    target_id = parsed[0]
            elif users_db.is_uid(args[0]):
                doc = await identity_service.resolve_user(
                    client, message, args[0], create=True
                )
                if doc is None:
                    await reply_html(client, message, msgs.error("User not found."))
                    return
                target_id = doc["user_id"]

        if target_id is None:
            await reply_html(
                client, message,
                msgs.error(
                    "Usage: <code>/rob @user</code>, <code>/rob 123456789</code>, "
                    "or reply to a user with <code>/rob</code>."
                ),
            )
            return

        robber_doc = await users_db.get_user(message.from_user.id)
        target_doc = await identity_service.ensure_user(target_id)
        result = await rob_service.attempt(message.from_user.id, target_id)
        await reply_html(client, message, msgs.rob_result(result, robber_doc, target_doc))
        if result["success"]:
            try:
                from utils.sender import send_html

                await send_html(client, target_id, msgs.robbery_notice(target_doc, robber_doc, result["stolen"]))
            except Exception:
                logger.warning("could not deliver robbery notice to %s", target_id)

    @app.on_message(filters.command("leader") & NOT_CHANNEL)
    @safe_handler(feature="leaderboard")
    async def cmd_leader(client: Client, message: Message):
        await ensure_user(client, message)
        top = await leaderboard_service.top_net_worth(10)
        entries = [
            (u["user_id"], leaderboard_service.name_of(u), await leaderboard_service.net_worth(u))
            for u in top
        ]
        sent = await reply_html(client, message, msgs.leaderboard(entries))
        # Auto-delete after 3 minutes so the tagged leaderboard post stops
        # cluttering the chat for other users.
        schedule_delete(client, sent, delay=LEADERBOARD_AUTO_DELETE_SECONDS)

    @app.on_message(filters.command("topbank") & NOT_CHANNEL)
    @safe_handler(feature="leaderboard")
    async def cmd_topbank(client: Client, message: Message):
        await ensure_user(client, message)
        top = await leaderboard_service.top_bank(10)
        entries = [
            (u["user_id"], leaderboard_service.name_of(u), int(u.get("bank", 0)))
            for u in top
        ]
        sent = await reply_html(client, message, msgs.bank_leaderboard(entries))
        schedule_delete(client, sent, delay=LEADERBOARD_AUTO_DELETE_SECONDS)
