"""Aviator crash-game handlers: /aviator + CASH OUT inline callback."""

from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message

from database import games as games_db
from games import aviator as aviator_game
from handlers.common import ensure_user, safe_handler
from services import game_engine
from services import identity as identity_service
from utils import messages as msgs
from utils.money import format_money
from utils.sender import answer_callback, reply_html
from utils.validators import parse_amount_or_error

logger = logging.getLogger(__name__)

AVIATOR_CB_PREFIX = f"{aviator_game.CB_PREFIX}:"

NOT_CHANNEL = ~filters.channel & ~filters.bot


def register(app: Client) -> None:
    @app.on_message(filters.command("aviator") & NOT_CHANNEL)
    @safe_handler(feature="games")
    async def cmd_aviator(client: Client, message: Message):
        await ensure_user(client, message)
        bet, err = parse_amount_or_error(message.command[1] if len(message.command) > 1 else "")
        if err:
            await reply_html(client, message, msgs.error(f"Usage: <code>/aviator amount</code>. {err}"))
            return
        session_id, state = await aviator_game.start(
            message.from_user.id, bet, chat_id=message.chat.id
        )
        sent = await reply_html(
            client, message,
            aviator_game.live_text(bet, 1.0, session_id),
            reply_markup=aviator_game.cashout_button(session_id),
        )
        if sent is not None:
            await games_db.bind_message(session_id, sent.id)
            aviator_game.start_task(session_id, client, sent)

    @app.on_callback_query(filters.regex(rf"^{AVIATOR_CB_PREFIX}"))
    async def cb_aviator(client: Client, callback: CallbackQuery):
        session_id = aviator_game.parse_callback(callback.data)
        if session_id is None:
            await answer_callback(client, callback, "Invalid data.", show_alert=True)
            return
        user_id = callback.from_user.id
        if callback.from_user:
            await identity_service.ensure_user(
                user_id, callback.from_user.username, callback.from_user.first_name
            )
        try:
            result = await aviator_game.cashout(
                session_id, user_id, client, callback.message
            )
            if result["crashed"]:
                await answer_callback(client, callback, "💥 Crashed — no payout.")
            else:
                await answer_callback(
                    client, callback, f"💰 Won {format_money(result['payout'])}!"
                )
        except game_engine.NoActiveGame as exc:
            await answer_callback(client, callback, str(exc), show_alert=True)
        except game_engine.GameError as exc:
            await answer_callback(client, callback, str(exc), show_alert=True)
        except (ValueError, TypeError) as exc:
            logger.warning("bad aviator callback %r: %s", callback.data, exc)
            await answer_callback(client, callback, "Invalid game data.", show_alert=True)
        except Exception as exc:  # noqa: BLE001
            logger.exception("aviator callback crashed: %s", exc)
            await answer_callback(client, callback, "An error occurred.", show_alert=True)
