"""Start / help handler."""

from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.enums import ChatType, ParseMode
from pyrogram.types import Message

from config import config
from database import users as users_db
from handlers.common import ensure_user, safe_handler
from utils.messages import help_text, start
from utils.sender import reply_html

logger = logging.getLogger(__name__)


async def _send_start_image(client: Client, message: Message, image_url: str, caption: str) -> None:
    """Send start image as spoiler media with caption. Falls back to text-only on failure."""
    if not image_url:
        await reply_html(client, message, caption)
        return
    try:
        await client.send_photo(
            chat_id=message.chat.id,
            photo=image_url,
            caption=caption,
            has_spoiler=True,
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:
        logger.warning("start image send failed (url=%s): %s", image_url, exc)
        await reply_html(client, message, caption)


def register(app: Client) -> None:
    @app.on_message(filters.command("start"))
    @safe_handler
    async def cmd_start(client: Client, message: Message):
        await ensure_user(client, message)
        user = message.from_user
        doc = await users_db.get_user(user.id)

        is_private = message.chat.type == ChatType.PRIVATE

        if is_private:
            # A private /start marks the user as a DM broadcast target.
            await users_db.set_user_flags(user.id, bot_started=True)
            image_url = config.START1_URL
        else:
            image_url = config.START2_URL

        caption = start(doc)
        await _send_start_image(client, message, image_url, caption)

    @app.on_message(filters.command("help") & filters.private)
    @safe_handler
    async def cmd_help(client: Client, message: Message):
        await reply_html(client, message, help_text())