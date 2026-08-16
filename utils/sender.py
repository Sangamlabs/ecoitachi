"""Central Telegram sending abstraction.

Every outbound message passes through here with ``parse_mode=HTML`` applied
globally.  No handler should call ``message.reply`` directly with raw HTML.
"""

from __future__ import annotations

import logging

from pyrogram import Client
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardMarkup, Message

logger = logging.getLogger(__name__)

KWARGS = {"parse_mode": ParseMode.HTML}


async def reply_html(
    client: Client, message: Message, text: str, quote: bool = True, **extra
) -> Message | None:
    try:
        return await message.reply(text, quote=quote, **KWARGS, **extra)
    except Exception as exc:
        logger.warning("reply failed (id=%s): %s", message.id, exc)
        return None


async def send_html(
    client: Client, chat_id: int, text: str, reply_markup: InlineKeyboardMarkup | None = None, **extra
) -> Message | None:
    try:
        return await client.send_message(
            chat_id, text, reply_markup=reply_markup, **KWARGS, **extra
        )
    except Exception as exc:
        logger.warning("send failed (chat=%s): %s", chat_id, exc)
        return None


async def edit_html(
    client: Client, message: Message, text: str, reply_markup: InlineKeyboardMarkup | None = None, **extra
) -> Message | None:
    try:
        return await message.edit(text, reply_markup=reply_markup, **KWARGS, **extra)
    except Exception as exc:
        logger.warning("edit failed (id=%s): %s", message.id, exc)
        return None


async def answer_callback(client: Client, callback_query, text: str, show_alert: bool = False) -> None:
    try:
        await callback_query.answer(text, show_alert=show_alert)
    except Exception as exc:
        logger.warning("callback answer failed: %s", exc)


async def notify_owner(client: Client, text: str) -> None:
    """Send a message to the configured owner (used for scheduler alerts)."""
    from config import config

    try:
        await client.send_message(config.OWNER_ID, text, **KWARGS)
    except Exception as exc:
        logger.warning("failed to notify owner: %s", exc)
