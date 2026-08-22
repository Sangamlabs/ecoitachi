"""Ping handler."""

from __future__ import annotations

import time

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import Message

from config import config
from handlers.common import safe_handler
from utils.messages import ping_text
from utils.sender import reply_html

import logging
logger = logging.getLogger(__name__)


def register(app: Client) -> None:
    @app.on_message(filters.command("ping"))
    @safe_handler
    async def cmd_ping(client: Client, message: Message):
        start_time = time.time()
        # Send a temporary message to measure round-trip
        sent = await message.reply("🏓 Pinging...")
        end_time = time.time()
        ping_ms = (end_time - start_time) * 1000

        text = ping_text(ping_ms)

        if config.PING_URL:
            try:
                await client.send_photo(
                    chat_id=message.chat.id,
                    photo=config.PING_URL,
                    caption=text,
                    has_spoiler=True,
                    parse_mode=ParseMode.HTML,
                )
                await sent.delete()
                return
            except Exception as exc:
                logger.warning("ping image send failed (url=%s): %s", config.PING_URL, exc)

        # Fallback: edit the temporary message with ping result
        await sent.edit(text, parse_mode=ParseMode.HTML)