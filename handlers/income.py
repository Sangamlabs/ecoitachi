"""Daily income claim handlers: /interestbank, /interestasset, /stockinterest."""

from __future__ import annotations

from pyrogram import Client, filters
from pyrogram.types import Message

from handlers.common import ensure_user, safe_handler
from services import income as income_service
from utils import messages as msgs
from utils.sender import reply_html

NOT_CHANNEL = ~filters.channel & ~filters.bot

COMMANDS = {
    "interestbank": income_service.BANK,
    "interestasset": income_service.ASSET,
    "stockinterest": income_service.STOCK,
}


def register(app: Client) -> None:
    for command, source in COMMANDS.items():

        @app.on_message(filters.command(command) & NOT_CHANNEL)
        @safe_handler(feature="economy")
        async def cmd_income(client: Client, message: Message, _source: str = source):
            await ensure_user(client, message)
            result = await income_service.claim(message.from_user.id, _source)
            await reply_html(client, message, msgs.income_claim(_source, result))
