"""Emoji game handlers: single-player and PvP duel rounds.

Every round sends Telegram's animated emoji first, waits ~1 second, then reads
the *actual* dice value from the sent message.  The central
:func:`services.emoji_games.process_emoji_result` validates the value,
resolves the game, settles the session, records transactions and sends the
result as a NEW HTML message — the native dice message is never edited.

Duels now roll a SINGLE emoji (not one per player).  The duel resolver splits
the game's value range: upper half wins for the creator, lower half for the
joiner, exact middle draws (odd ranges only).
"""

from __future__ import annotations

import asyncio
import logging

from pyrogram import Client, filters
from pyrogram.types import Message

from database import emoji_games as emoji_db
from handlers.common import ensure_user, safe_handler
from services import economy, emoji_games as emoji_service
from services.emoji_games import (
    SINGLE_COMMANDS,
    DUEL_COMMANDS,
    EmojiGameCooldown,
    EmojiGameError,
    EmojiGameInProgress,
    get_game_def,
)
from utils import messages as msgs
from utils.money import MoneyError
from utils.sender import reply_html
from utils.validators import parse_amount_or_error

logger = logging.getLogger(__name__)

NOT_CHANNEL = ~filters.channel & ~filters.bot

SINGLE_COMMAND_NAMES = list(SINGLE_COMMANDS)
DUEL_COMMAND_NAMES = list(DUEL_COMMANDS)


async def _reply_game_error(client: Client, message: Message, exc: Exception) -> None:
    if isinstance(exc, EmojiGameCooldown):
        await reply_html(
            client, message, msgs.game_cooldown(exc.game.upper(), exc.remaining)
        )
        return
    if isinstance(exc, EmojiGameInProgress):
        await reply_html(client, message, msgs.error(str(exc)))
        return
    if isinstance(exc, (EmojiGameError, economy.EconomyError, MoneyError)):
        await reply_html(client, message, msgs.error(str(exc)))
        return
    await reply_html(client, message, msgs.error("Something went wrong. Try again."))


def _user_name(message: Message) -> tuple[str | None, str | None]:
    user = message.from_user
    if user is None:
        return None, None
    return user.username, user.first_name


def register(app: Client) -> None:
    @app.on_message(filters.command(SINGLE_COMMAND_NAMES) & NOT_CHANNEL)
    @safe_handler(feature="games")
    async def cmd_single(client: Client, message: Message):
        await ensure_user(client, message)
        command = message.command[0].lower().lstrip("/")
        game_type = SINGLE_COMMANDS.get(command)
        game_def = get_game_def(game_type)
        bet, err = parse_amount_or_error(message.command[1] if len(message.command) > 1 else "")
        if err:
            await reply_html(
                client, message,
                msgs.error(f"Usage: <code>/{command} amount</code>. {err}"),
            )
            return
        username, name = _user_name(message)
        try:
            started = await emoji_service.start_single(
                message.from_user.id,
                game_type,
                bet,
                chat_id=message.chat.id,
                username=username,
                name=name,
            )
        except EmojiGameError as exc:
            await _reply_game_error(client, message, exc)
            return
        except (economy.EconomyError, MoneyError) as exc:
            await _reply_game_error(client, message, exc)
            return

        dice = await client.send_dice(message.chat.id, emoji=game_def.emoji)
        if dice is not None:
            await emoji_db.set_message(started["session_id"], dice.id)
        await asyncio.sleep(1)
        await emoji_service.process_emoji_result(
            client, dice, message, started["session_id"]
        )

    @app.on_message(filters.command(DUEL_COMMAND_NAMES) & NOT_CHANNEL)
    @safe_handler(feature="games")
    async def cmd_duel(client: Client, message: Message):
        await ensure_user(client, message)
        command = message.command[0].lower().lstrip("/")
        game_type = DUEL_COMMANDS.get(command)
        game_def = get_game_def(game_type)
        bet, err = parse_amount_or_error(message.command[1] if len(message.command) > 1 else "")
        if err:
            await reply_html(
                client, message,
                msgs.error(f"Usage: <code>/{command} amount</code>. {err}"),
            )
            return
        username, name = _user_name(message)
        try:
            lobby = await emoji_service.create_duel(
                message.from_user.id,
                game_type,
                bet,
                chat_id=message.chat.id,
                username=username,
                name=name,
            )
        except EmojiGameError as exc:
            await _reply_game_error(client, message, exc)
            return
        except (economy.EconomyError, MoneyError) as exc:
            await _reply_game_error(client, message, exc)
            return

        await reply_html(
            client, message,
            msgs.emoji_lobby(
                game_def.label,
                game_def.emoji,
                lobby["bet"],
                lobby["game_id"],
                max(1, lobby["expires_at"] - int(__import__("time").time())),
            ),
        )

    @app.on_message(filters.command("join") & NOT_CHANNEL)
    @safe_handler(feature="games")
    async def cmd_join(client: Client, message: Message):
        await ensure_user(client, message)
        game_id = message.command[1] if len(message.command) > 1 else ""
        if not game_id.isdigit() or len(game_id) != 4:
            await reply_html(
                client, message,
                msgs.error("Usage: <code>/join GAME_ID</code> — a 4-digit code."),
            )
            return
        username, name = _user_name(message)
        try:
            joined = await emoji_service.join_duel(
                game_id,
                message.from_user.id,
                chat_id=message.chat.id,
                username=username,
                name=name,
            )
        except EmojiGameCooldown as exc:
            await _reply_game_error(client, message, exc)
            return
        except EmojiGameError as exc:
            await _reply_game_error(client, message, exc)
            return
        except (economy.EconomyError, MoneyError) as exc:
            await _reply_game_error(client, message, exc)
            return

        game_type = joined["game_type"]
        game_def = get_game_def(game_type)

        dice = await client.send_dice(message.chat.id, emoji=game_def.emoji)
        if dice is not None:
            await emoji_db.set_message(joined["session_id"], dice.id)
        await asyncio.sleep(1)
        await emoji_service.process_emoji_result(
            client, dice, message, joined["session_id"]
        )