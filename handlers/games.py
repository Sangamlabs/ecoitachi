"""Game handlers: /fly, /mines, /bet + mines inline-board callbacks."""

from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message

from games import bet as bet_game, fly as fly_game, mines as mines_game
from database import games as games_db
from handlers.common import ensure_user, safe_handler
from services import game_engine
from utils import messages as msgs
from utils.money import format_money
from utils.sender import answer_callback, edit_html, reply_html
from utils.validators import parse_amount_or_error

logger = logging.getLogger(__name__)

MINES_CB_PREFIX = "mines:"

NOT_CHANNEL = ~filters.channel & ~filters.bot


def _parse_mines_callback(data: str) -> tuple[str, str] | None:
    """Split ``mines:SESSION_ID:ACTION`` into (session_id, action)."""
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "mines":
        return None
    return parts[1], parts[2]


def register(app: Client) -> None:
    @app.on_message(filters.command("fly") & NOT_CHANNEL)
    @safe_handler(feature="games")
    async def cmd_fly(client: Client, message: Message):
        await ensure_user(client, message)
        args = message.command[1:]
        if len(args) < 2:
            await reply_html(
                client, message,
                msgs.error("Usage: <code>/fly low|medium|high amount</code>"),
            )
            return
        difficulty = fly_game.parse_difficulty(args[0])
        bet, err = parse_amount_or_error(args[1])
        if err:
            await reply_html(client, message, msgs.error(err))
            return
        result = await fly_game.play(
            message.from_user.id, difficulty, bet, chat_id=message.chat.id
        )
        await reply_html(
            client, message,
            msgs.fly_result(
                result["difficulty"], result["bet"], result["won"],
                result["multiplier"], result["payout"], result["session_id"],
            ),
        )

    @app.on_message(filters.command("bet") & NOT_CHANNEL)
    @safe_handler(feature="games")
    async def cmd_bet(client: Client, message: Message):
        await ensure_user(client, message)
        bet, err = parse_amount_or_error(message.command[1] if len(message.command) > 1 else "")
        if err:
            await reply_html(client, message, msgs.error(f"Usage: <code>/bet amount</code>. {err}"))
            return
        result = await bet_game.play(message.from_user.id, bet, chat_id=message.chat.id)
        await reply_html(
            client, message,
            msgs.bet_result(result["bet"], result["won"], result["multiplier"], result["payout"], result["session_id"]),
        )

    @app.on_message(filters.command("mines") & NOT_CHANNEL)
    @safe_handler(feature="games")
    async def cmd_mines(client: Client, message: Message):
        await ensure_user(client, message)
        bet, err = parse_amount_or_error(message.command[1] if len(message.command) > 1 else "")
        if err:
            await reply_html(client, message, msgs.error(f"Usage: <code>/mines amount</code>. {err}"))
            return
        cfg = await mines_game.board_settings()
        session_id, state = await mines_game.start(
            message.from_user.id, bet, chat_id=message.chat.id
        )
        text = mines_game.game_header(
            bet,
            mines_game.multiplier_after(1, cfg["multipliers"]),
            mines_game.payout_for(bet, 1, cfg["multipliers"]),
            0,
        )
        text += f"\n<pre>{mines_game.render_board(state)}</pre>"
        sent = await reply_html(
            client, message, text,
            reply_markup=mines_game.board_keyboard(session_id, state),
        )
        if sent is not None:
            await games_db.bind_message(session_id, sent.id)

    @app.on_callback_query(filters.regex(rf"^{MINES_CB_PREFIX}"))
    async def cb_mines(client: Client, callback: CallbackQuery):
        parsed = _parse_mines_callback(callback.data)
        if parsed is None:
            await answer_callback(client, callback, "Invalid data.", show_alert=True)
            return
        session_id, action = parsed
        user_id = callback.from_user.id
        chat_id = callback.message.chat.id
        message_id = callback.message.id
        try:
            if action == "cash":
                result = await mines_game.cashout(
                    session_id, user_id, chat_id=chat_id, message_id=message_id
                )
                session_doc = await games_db.get_session(session_id)
                bet = int(session_doc.get("bet", 0)) if session_doc else 0
                text = (
                    f"<b>💣 MINES — CASHED OUT</b>\n"
                    f"<blockquote>✅ Won: <b>{format_money(result['payout'])}</b>\n"
                    f"🧾 <code>#{session_id}</code></blockquote>\n"
                    f"<pre>{mines_game.render_board(result['state'], reveal_mines=True)}</pre>"
                )
                await edit_html(client, callback.message, text, reply_markup=None)
            else:
                tile = int(action)
                result = await mines_game.reveal(
                    session_id, user_id, tile, chat_id=chat_id, message_id=message_id
                )
                reveals = len(result["state"].get("revealed", []))
                if result["game_over"]:
                    if result["won"]:
                        text = (
                            f"<b>💣 MINES — COMPLETED</b>\n"
                            f"<blockquote>✅ Won: <b>{format_money(result['payout'])}</b>\n"
                            f"🧾 <code>#{session_id}</code></blockquote>\n"
                            f"<pre>{mines_game.render_board(result['state'], reveal_mines=True)}</pre>"
                        )
                    else:
                        session_doc = await games_db.get_session(session_id)
                        bet = int(session_doc.get("bet", 0)) if session_doc else 0
                        text = (
                            f"<b>💣 MINES — HIT A MINE</b>\n"
                            f"<blockquote>❌ Lost: {format_money(bet)}</blockquote>\n"
                            f"<pre>{mines_game.render_board(result['state'], reveal_mines=True)}</pre>"
                        )
                    await edit_html(client, callback.message, text, reply_markup=None)
                else:
                    session_doc = await games_db.get_session(session_id)
                    bet = int(session_doc.get("bet", 0)) if session_doc else 0
                    text = mines_game.game_header(
                        bet, result["multiplier"],
                        result["payout"], reveals,
                    )
                    text += f"\n<pre>{mines_game.render_board(result['state'])}</pre>"
                    await edit_html(
                        client, callback.message, text,
                        reply_markup=mines_game.board_keyboard(session_id, result["state"]),
                    )
            await answer_callback(client, callback, "Done.")
        except game_engine.NoActiveGame as exc:
            await answer_callback(client, callback, str(exc), show_alert=True)
        except game_engine.GameError as exc:
            await answer_callback(client, callback, str(exc), show_alert=True)
        except (ValueError, TypeError) as exc:
            logger.warning("bad mines callback %r: %s", callback.data, exc)
            await answer_callback(client, callback, "Invalid game data.", show_alert=True)
        except Exception as exc:  # noqa: BLE001
            logger.exception("mines callback crashed: %s", exc)
            await answer_callback(client, callback, "An error occurred.", show_alert=True)
