"""Promo admin commands: /addpromo /rmpromo /editpromo /promoinfo /promolist /promostats.

Only owner/sudo admins can manage promos.  Redemptions happen automatically by
typing the promo code in any chat (see handlers/promo_detect).
"""

from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.types import Message

from handlers.common import safe_handler
from services import promos as promo_service
from utils import messages as msgs
from utils.permissions import sudo_only
from utils.sender import reply_html

logger = logging.getLogger(__name__)

NOT_CHANNEL = ~filters.channel & ~filters.bot


def register(app: Client) -> None:
    @app.on_message(filters.command("addpromo") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_addpromo(client: Client, message: Message):
        parts = message.command[1:]
        if len(parts) < 4:
            await reply_html(
                client,
                message,
                msgs.error(
                    "Usage: <code>/addpromo CODE EXPIRY LIMIT REWARD [REWARD...]</code>\n"
                    "Example: <code>/addpromo ITACHI500 7days 100 "
                    "rs:500 stock:BTC:0.01 asset:AST-00021:1</code>\n"
                    "<i>Expiry: lifetime or number + min/hr/day/week/month/year.\n"
                    "Limit: a number or 'unlimited'.\n"
                    "Rewards: rs:AMOUNT, stock:SYMBOL:QTY, asset:ASSET_ID:QTY.</i>"
                ),
            )
            return
        doc = await promo_service.create_promo(
            message.from_user.id,
            parts[0],
            parts[1],
            parts[2],
            parts[3:],
        )
        await reply_html(client, message, msgs.promo_created(doc))

    @app.on_message(filters.command("rmpromo") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_rmpromo(client: Client, message: Message):
        if len(message.command) < 2:
            await reply_html(client, message, msgs.error("Usage: <code>/rmpromo CODE</code>"))
            return
        doc = await promo_service.disable_promo(message.from_user.id, message.command[1])
        await reply_html(
            client,
            message,
            msgs.success(
                f"Promo <code>{doc['code']}</code> disabled. "
                "Its redemption history is preserved."
            ),
        )

    @app.on_message(filters.command("editpromo") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_editpromo(client: Client, message: Message):
        parts = message.command[1:]
        if len(parts) < 3:
            await reply_html(
                client,
                message,
                msgs.error(
                    "Usage: <code>/editpromo CODE FIELD VALUE [VALUE...]</code>\n"
                    "Fields: <code>expiry</code>, <code>limit</code>, "
                    "<code>active</code> (on/off), <code>reward</code> (new tokens)."
                ),
            )
            return
        doc = await promo_service.edit_promo(
            message.from_user.id, parts[0], parts[1], parts[2:]
        )
        await reply_html(
            client,
            message,
            msgs.success(f"Promo <code>{doc['code']}</code> updated."),
        )

    @app.on_message(filters.command("promoinfo") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_promoinfo(client: Client, message: Message):
        if len(message.command) < 2:
            await reply_html(client, message, msgs.error("Usage: <code>/promoinfo CODE</code>"))
            return
        doc = await promo_service.get_promo_info(message.command[1])
        await reply_html(client, message, msgs.promo_info(doc))

    @app.on_message(filters.command("promolist") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_promolist(client: Client, message: Message):
        status = "all"
        page = 1
        for arg in message.command[1:]:
            if arg.lower() in ("active", "expired", "inactive", "all"):
                status = arg.lower()
            elif arg.isdigit():
                page = int(arg)
        docs, total = await promo_service.list_promos(status, page, per_page=10)
        await reply_html(client, message, msgs.promo_list(docs, total, page, 10))

    @app.on_message(filters.command("promostats") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_promostats(client: Client, message: Message):
        if len(message.command) < 2:
            await reply_html(client, message, msgs.error("Usage: <code>/promostats CODE</code>"))
            return
        stats = await promo_service.get_promo_stats(message.command[1])
        await reply_html(client, message, msgs.promo_stats(stats))
