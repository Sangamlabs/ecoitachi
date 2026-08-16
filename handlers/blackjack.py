"""Blackjack handler: /blackjack amount — USER VS BOT, two cards each."""

from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.types import Message

from handlers.common import ensure_user, safe_handler
from services import blackjack as blackjack_service
from services import economy
from utils import messages as msgs
from utils.money import MoneyError
from utils.sender import reply_html
from utils.validators import parse_amount_or_error

logger = logging.getLogger(__name__)

NOT_CHANNEL = ~filters.channel & ~filters.bot


def register(app: Client) -> None:
    @app.on_message(filters.command("blackjack") & NOT_CHANNEL)
    @safe_handler(feature="games")
    async def cmd_blackjack(client: Client, message: Message):
        await ensure_user(client, message)
        bet, err = parse_amount_or_error(message.command[1] if len(message.command) > 1 else "")
        if err:
            await reply_html(
                client, message,
                msgs.error(f"Usage: <code>/blackjack amount</code>. {err}"),
            )
            return
        try:
            result = await blackjack_service.play(message.from_user.id, bet, chat_id=message.chat.id)
        except blackjack_service.BlackjackCooldown as exc:
            await reply_html(
                client, message,
                msgs.game_cooldown("BLACKJACK", exc.remaining),
            )
            return
        except blackjack_service.BlackjackError as exc:
            await reply_html(client, message, msgs.error(str(exc)))
            return
        except (economy.EconomyError, MoneyError) as exc:
            await reply_html(client, message, msgs.error(str(exc)))
            return

        await reply_html(
            client, message,
            msgs.blackjack_result(
                result["user_cards"],
                result["bot_cards"],
                result["user_total"],
                result["bot_total"],
                result["outcome"],
                result["bet"],
                result["payout"],
                result["tx_id"],
            ),
        )
