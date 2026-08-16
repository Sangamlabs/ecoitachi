"""Free reward handlers: /daily, /weekly, /monthly."""

from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.types import Message

from handlers.common import ensure_user, safe_handler
from services import rewards as rewards_service
from utils import messages as msgs
from utils.sender import reply_html

logger = logging.getLogger(__name__)

NOT_CHANNEL = ~filters.channel & ~filters.bot


def _claim(kind: str):
    async def cmd_claim(client: Client, message: Message):
        await ensure_user(client, message)
        result = await rewards_service.claim(message.from_user.id, kind)
        await reply_html(
            client, message,
            msgs.reward_claimed(result["kind"], result["amount"], result["cooldown"]),
        )

    return cmd_claim


def register(app: Client) -> None:
    for kind in ("daily", "weekly", "monthly"):
        handler = safe_handler(feature="economy")(_claim(kind))
        app.on_message(filters.command(kind) & NOT_CHANNEL)(handler)
