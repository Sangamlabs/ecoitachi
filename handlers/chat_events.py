"""Automatic group registration hooks.

Any group the bot is added to (or that simply sends a message) is
idempotently registered in the ``group_config`` collection so it becomes a
potential ``/bgc`` broadcast target without a manual ``/setchat``.  Both hooks
are fire-and-forget: a registration failure must never break message handling.
"""

from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus
from pyrogram.types import ChatMemberUpdated, Message

from services import group_config as group_config_service

logger = logging.getLogger(__name__)


async def _register(chat_id: int) -> None:
    try:
        await group_config_service.ensure_registered(chat_id)
    except Exception as exc:  # noqa: BLE001 - registration is best-effort
        logger.warning("could not auto-register chat %s: %s", chat_id, exc)


def register(app: Client) -> None:
    @app.on_chat_member_updated(filters.group)
    async def on_group_member_updated(client: Client, update: ChatMemberUpdated):
        new_member = update.new_chat_member
        if new_member is None:
            return
        user = getattr(new_member, "user", None)
        if user is None or not getattr(user, "is_self", False):
            return
        if new_member.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        ):
            await _register(update.chat.id)

    @app.on_message(filters.group & ~filters.bot)
    async def on_group_message(client: Client, message: Message):
        if message.chat is None:
            message.continue_propagation()
        if message.from_user and getattr(message.from_user, "is_bot", False):
            message.continue_propagation()
        await _register(message.chat.id)
        message.continue_propagation()