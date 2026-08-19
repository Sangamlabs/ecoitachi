"""Colour game handlers: /colour + selection/play inline callbacks."""

from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message

from games import colour as colour_game
from database import games as games_db
from handlers.common import ensure_user, safe_handler
from services import game_engine
from services import identity as identity_service
from utils import messages as msgs
from utils.money import format_money
from utils.sender import answer_callback, edit_html, reply_html
from utils.validators import parse_amount_or_error

logger = logging.getLogger(__name__)

COLOUR_CB_PREFIX = f"{colour_game.CB_PREFIX}:"

NOT_CHANNEL = ~filters.channel & ~filters.bot


def _result_text(result: dict, session_id: str) -> str:
    if result.get("expired"):
        return (
            f"<b>🎨 COLOUR — EXPIRED</b>\n"
            f"<blockquote>⏱️ Time's up — session settled as a loss.\n"
            f"❌ Lost: <b>{format_money(result['bet'])}</b>\n"
            f"🧾 <code>#{session_id}</code></blockquote>"
        )
    size = colour_game.SIZE_EMOJI[result["size"]]
    colour = colour_game.COLOUR_EMOJI[result["colour"]]
    result_line = (
        f"🎲 Result: <b>{result['number']}</b> — {colour} {result['colour']} · "
        f"{size} {result['size']}\n"
    )
    state = result.get("state", {})
    picks = state.get("size"), state.get("colour"), state.get("number")
    picks_line = f"🎯 Your picks: {picks[0]} · {picks[1]} · Number {picks[2]}\n"
    if result["won"]:
        body = (
            result_line + picks_line
            + f"✅ Matched <b>{result['matches']}</b>/3\n"
            + f"📈 Multiplier: <b>{result['multiplier']:.2f}x</b>\n"
            + f"💰 Won: <b>{format_money(result['payout'])}</b>\n"
        )
    else:
        body = (
            result_line + picks_line
            + f"❌ No match — lost <b>{format_money(result['bet'])}</b>\n"
        )
    return f"<b>🎨 COLOUR — RESULT</b>\n<blockquote>{body}🧾 <code>#{session_id}</code></blockquote>"


def register(app: Client) -> None:
    @app.on_message(filters.command("colour") & NOT_CHANNEL)
    @safe_handler(feature="games")
    async def cmd_colour(client: Client, message: Message):
        await ensure_user(client, message)
        bet, err = parse_amount_or_error(message.command[1] if len(message.command) > 1 else "")
        if err:
            await reply_html(client, message, msgs.error(f"Usage: <code>/colour amount</code>. {err}"))
            return
        session_id, state = await colour_game.start(
            message.from_user.id, bet, chat_id=message.chat.id
        )
        text = colour_game.selection_header(bet, state)
        sent = await reply_html(
            client, message, text,
            reply_markup=colour_game.build_keyboard(session_id, state),
        )
        if sent is not None:
            await games_db.bind_message(session_id, sent.id)

    @app.on_callback_query(filters.regex(rf"^{COLOUR_CB_PREFIX}"))
    async def cb_colour(client: Client, callback: CallbackQuery):
        parsed = colour_game.parse_callback(callback.data)
        if parsed is None:
            await answer_callback(client, callback, "Invalid data.", show_alert=True)
            return
        session_id, category, value = parsed
        user_id = callback.from_user.id
        if callback.from_user:
            await identity_service.ensure_user(
                user_id, callback.from_user.username, callback.from_user.first_name
            )
        chat_id = callback.message.chat.id
        message_id = callback.message.id
        try:
            if category == "play":
                result = await colour_game.play(
                    session_id, user_id, chat_id=chat_id, message_id=message_id
                )
                await edit_html(
                    client, callback.message,
                    _result_text(result, session_id), reply_markup=None,
                )
                await answer_callback(
                    client, callback,
                    f"Won {format_money(result['payout'])}!" if result.get("won")
                    else ("Expired." if result.get("expired") else "Lost."),
                )
            else:
                result = await colour_game.select(
                    session_id, user_id, category, value, chat_id=chat_id, message_id=message_id
                )
                if result.get("expired"):
                    await edit_html(
                        client, callback.message,
                        _result_text(result, session_id), reply_markup=None,
                    )
                    await answer_callback(client, callback, "Expired.", show_alert=True)
                    return
                text = colour_game.selection_header(result["bet"], result["state"])
                await edit_html(
                    client, callback.message, text,
                    reply_markup=colour_game.build_keyboard(session_id, result["state"]),
                )
                if colour_game.selections_complete(result["state"]):
                    await answer_callback(client, callback, "Ready! Press 🎯 PLAY.")
                else:
                    await answer_callback(client, callback, "Selection saved.")
        except game_engine.NoActiveGame as exc:
            await answer_callback(client, callback, str(exc), show_alert=True)
        except game_engine.GameError as exc:
            await answer_callback(client, callback, str(exc), show_alert=True)
        except (ValueError, TypeError) as exc:
            logger.warning("bad colour callback %r: %s", callback.data, exc)
            await answer_callback(client, callback, "Invalid game data.", show_alert=True)
        except Exception as exc:  # noqa: BLE001
            logger.exception("colour callback crashed: %s", exc)
            await answer_callback(client, callback, "An error occurred.", show_alert=True)