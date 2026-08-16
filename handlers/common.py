"""Shared helpers for handlers: user bootstrap and safe execution."""

from __future__ import annotations

import functools
import logging
from typing import Awaitable, Callable

from pyrogram import Client
from pyrogram.types import Message

from database import users as users_db
from services.economy import EconomyError, BannedUser, FrozenUser, InsufficientBalance
from services.game_engine import GameCooldownError, GameError, GameInProgress, NoActiveGame
from utils.messages import error
from utils.money import MoneyError
from utils.sender import reply_html

logger = logging.getLogger(__name__)


async def ensure_user(client: Client, message: Message) -> None:
    """Create/touch the interacting user before running a command."""
    user = message.from_user
    if user is None:
        return
    await users_db.get_or_create_user(user.id, user.username, user.first_name)
    await users_db.touch_user(user.id, user.username, user.first_name)


def safe_handler(func: Callable[..., Awaitable]) -> Callable:
    """Wrap a command handler with user bootstrap + centralized error handling."""

    @functools.wraps(func)
    async def wrapper(client: Client, message: Message, *args, **kwargs):
        try:
            await ensure_user(client, message)
            return await func(client, message, *args, **kwargs)
        except (EconomyError, MoneyError, GameError, NoActiveGame) as exc:
            await reply_html(client, message, error(str(exc)))
        except GameCooldownError as exc:
            parts = str(exc).split(":")
            game = parts[0] if parts else "game"
            remaining = int(parts[2]) if len(parts) >= 3 else 0
            from utils.messages import game_cooldown

            await reply_html(client, message, game_cooldown(game, remaining))
        except GameInProgress as exc:
            await reply_html(client, message, error(str(exc)))
        except FrozenUser:
            await reply_html(client, message, error("Your account is frozen. Contact an admin."))
        except BannedUser:
            await reply_html(client, message, error("You are banned from the economy."))
        except InsufficientBalance as exc:
            await reply_html(client, message, error(str(exc)))
        except Exception as exc:  # noqa: BLE001 - last-resort guard
            logger.exception("handler %s crashed: %s", func.__name__, exc)
            try:
                await reply_html(client, message, error("Something went wrong. Try again."))
            except Exception:
                logger.exception("failed to send error message")

    return wrapper


def require_reply(client: Client, message: Message) -> Message | None:
    if not getattr(message, "reply_to_message", None) or not message.reply_to_message.from_user:
        return None
    return message.reply_to_message
