"""Start / help handler."""

from __future__ import annotations

from pyrogram import Client, filters
from pyrogram.types import Message

from database import users as users_db
from handlers.common import ensure_user, safe_handler
from utils.messages import help_text, start
from utils.sender import reply_html


def register(app: Client) -> None:
    @app.on_message(filters.command("start") & filters.private)
    @safe_handler
    async def cmd_start(client: Client, message: Message):
        await ensure_user(client, message)
        user = message.from_user
        doc = await users_db.get_user(user.id)
        await reply_html(client, message, start(doc))

    @app.on_message(filters.command("help") & filters.private)
    @safe_handler
    async def cmd_help(client: Client, message: Message):
        await reply_html(client, message, help_text())
